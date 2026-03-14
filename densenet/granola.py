"""
Read meeting notes from Granola's local storage.

Granola (macOS meeting notes app) stores data in a SQLite database at:
  ~/Library/Application Support/Granola/granola.db

This module auto-discovers the schema by inspecting column names, so it
works across Granola versions without hardcoding table/column names.

Fallback: if the DB isn't found, reads .md / .txt files from
GRANOLA_NOTES_DIR whose filenames contain the target date (YYYY-MM-DD).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from . import config


# ── SQLite reader ──────────────────────────────────────────────────────────────

def _discover_schema(conn: sqlite3.Connection) -> list[dict]:
    """
    Return a list of candidate table descriptors, each with:
      table, date_col, content_col, title_col (may be None)
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]

    candidates = []
    for table in tables:
        cols = [
            row[1].lower()
            for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        ]
        raw_cols = [
            row[1]
            for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        ]
        col_map = {c.lower(): orig for c, orig in zip(cols, raw_cols)}

        date_col = next(
            (col_map[c] for c in cols if any(k in c for k in ("date", "created", "time", "start"))),
            None,
        )
        content_col = next(
            (col_map[c] for c in cols if any(k in c for k in ("content", "body", "text", "notes", "transcript", "summary"))),
            None,
        )
        title_col = next(
            (col_map[c] for c in cols if any(k in c for k in ("title", "name", "subject", "meeting"))),
            None,
        )

        if date_col and content_col:
            candidates.append(
                {"table": table, "date_col": date_col, "content_col": content_col, "title_col": title_col}
            )

    return candidates


def _read_from_sqlite(db_path: Path, target_date: date) -> list[dict]:
    """Read notes for target_date from Granola's SQLite database."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    notes: list[dict] = []

    try:
        candidates = _discover_schema(conn)
        date_str = target_date.isoformat()  # "2025-03-14"

        for schema in candidates:
            table = schema["table"]
            date_col = schema["date_col"]
            content_col = schema["content_col"]
            title_col = schema["title_col"]

            # Match rows whose date column starts with the ISO date string
            # (handles both "2025-03-14" and "2025-03-14T09:00:00" formats)
            try:
                rows = conn.execute(
                    f"SELECT * FROM \"{table}\" WHERE \"{date_col}\" LIKE ?",
                    (f"{date_str}%",),
                ).fetchall()
            except sqlite3.OperationalError:
                continue

            for row in rows:
                d = dict(row)
                content = d.get(content_col) or ""
                # Granola sometimes stores content as JSON; try to extract plain text
                if content.startswith("{") or content.startswith("["):
                    try:
                        parsed = json.loads(content)
                        content = _flatten_json_content(parsed)
                    except (json.JSONDecodeError, TypeError):
                        pass

                if not content.strip():
                    continue

                notes.append(
                    {
                        "title": (d.get(title_col) if title_col else None) or "Meeting",
                        "content": content,
                        "date": date_str,
                        "source": f"{db_path.name}:{table}",
                    }
                )
    finally:
        conn.close()

    return notes


def _flatten_json_content(obj: object, depth: int = 0) -> str:
    """
    Recursively flatten a JSON object into readable plain text.
    Granola may store note content as a Prosemirror/Slate JSON doc.
    """
    if depth > 10:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(_flatten_json_content(item, depth + 1) for item in obj)
    if isinstance(obj, dict):
        # Common rich-text doc shapes
        text = obj.get("text") or obj.get("content") or obj.get("value") or ""
        if isinstance(text, str) and text:
            return text
        # Recurse into children / content arrays
        children = obj.get("children") or obj.get("content") or obj.get("nodes") or []
        if isinstance(children, list):
            return "\n".join(_flatten_json_content(c, depth + 1) for c in children)
    return str(obj) if obj else ""


# ── File-based fallback ────────────────────────────────────────────────────────

def _read_from_directory(notes_dir: Path, target_date: date) -> list[dict]:
    """
    Read .md / .txt files whose names contain the ISO date (YYYY-MM-DD).
    """
    notes: list[dict] = []
    date_str = target_date.isoformat()

    for path in sorted(notes_dir.iterdir()):
        if path.suffix not in (".md", ".txt", ".markdown"):
            continue
        if date_str not in path.name:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            continue
        notes.append(
            {
                "title": path.stem,
                "content": content,
                "date": date_str,
                "source": str(path),
            }
        )

    return notes


# ── Public API ─────────────────────────────────────────────────────────────────

def get_notes_for_date(target_date: date | None = None) -> list[dict]:
    """
    Return a list of notes for target_date (default: today).

    Each note dict has: title, content, date, source.

    Resolution order:
    1. GRANOLA_DB_PATH (env var or macOS default)
    2. GRANOLA_NOTES_DIR (env var, directory of markdown/text files)
    """
    if target_date is None:
        target_date = date.today()

    # 1 – SQLite database
    db_candidates: list[Path] = []
    if config.GRANOLA_DB_PATH:
        db_candidates.append(Path(config.GRANOLA_DB_PATH).expanduser())

    for db_path in db_candidates:
        if db_path.exists():
            notes = _read_from_sqlite(db_path, target_date)
            if notes:
                return notes

    # 2 – Notes directory
    if config.GRANOLA_NOTES_DIR:
        notes_dir = Path(config.GRANOLA_NOTES_DIR).expanduser()
        if notes_dir.is_dir():
            notes = _read_from_directory(notes_dir, target_date)
            if notes:
                return notes

    return []


def notes_source_description() -> str:
    """Human-readable description of where notes will be read from."""
    db_path = Path(config.GRANOLA_DB_PATH).expanduser() if config.GRANOLA_DB_PATH else None
    if db_path and db_path.exists():
        return f"Granola DB: {db_path}"
    if config.GRANOLA_NOTES_DIR:
        return f"Notes directory: {config.GRANOLA_NOTES_DIR}"
    if config.GRANOLA_DB_PATH:
        return f"Granola DB (not found): {config.GRANOLA_DB_PATH}"
    return "No Granola source configured"
