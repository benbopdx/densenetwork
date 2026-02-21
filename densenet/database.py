"""SQLite database layer for DenseNet."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from . import config


# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_id     TEXT    UNIQUE NOT NULL,
    name            TEXT,
    email           TEXT,
    company         TEXT,
    title           TEXT,
    phone           TEXT,
    location        TEXT,
    linkedin_url    TEXT,
    twitter_url     TEXT,
    website         TEXT,
    tags            TEXT,   -- JSON array
    notes           TEXT,
    raw_data        TEXT,   -- full Airtable fields as JSON
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contact_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER UNIQUE REFERENCES contacts(id) ON DELETE CASCADE,
    summary             TEXT,
    skills              TEXT,   -- JSON array
    interests           TEXT,   -- JSON array
    expertise_areas     TEXT,   -- JSON array
    work_history        TEXT,   -- JSON array of {company, role, period}
    education           TEXT,   -- JSON array of {school, degree, field}
    notable_achievements TEXT,  -- JSON array
    social_links        TEXT,   -- JSON array
    sources             TEXT,   -- JSON array of URLs used
    enrichment_method   TEXT,   -- 'web_search' | 'basic'
    enriched_at         TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_criteria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    field       TEXT    NOT NULL,
    operator    TEXT    NOT NULL,
    value       TEXT,
    active      INTEGER DEFAULT 1,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at       TEXT    DEFAULT (datetime('now')),
    contacts_added  INTEGER DEFAULT 0,
    contacts_updated INTEGER DEFAULT 0,
    contacts_total  INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'success',
    error_message   TEXT
);
"""


# ── Connection helper ──────────────────────────────────────────────────────────

@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ── Contacts ───────────────────────────────────────────────────────────────────

def upsert_contact(airtable_id: str, fields: dict[str, Any]) -> tuple[int, bool]:
    """
    Insert or update a contact.

    Returns (contact_id, created) where created=True if newly inserted.
    """
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM contacts WHERE airtable_id = ?", (airtable_id,)
        ).fetchone()

        data = {
            "airtable_id":  airtable_id,
            "name":         fields.get("name"),
            "email":        fields.get("email"),
            "company":      fields.get("company"),
            "title":        fields.get("title"),
            "phone":        fields.get("phone"),
            "location":     fields.get("location"),
            "linkedin_url": fields.get("linkedin_url"),
            "twitter_url":  fields.get("twitter_url"),
            "website":      fields.get("website"),
            "tags":         json.dumps(fields.get("tags") or []),
            "notes":        fields.get("notes"),
            "raw_data":     json.dumps(fields.get("_raw", {})),
            "updated_at":   datetime.now().isoformat(),
        }

        if existing:
            cols = [f"{k} = :{k}" for k in data if k != "airtable_id"]
            conn.execute(
                f"UPDATE contacts SET {', '.join(cols)} WHERE airtable_id = :airtable_id",
                data,
            )
            return existing["id"], False
        else:
            conn.execute(
                """INSERT INTO contacts
                   (airtable_id, name, email, company, title, phone, location,
                    linkedin_url, twitter_url, website, tags, notes, raw_data, updated_at)
                   VALUES
                   (:airtable_id, :name, :email, :company, :title, :phone, :location,
                    :linkedin_url, :twitter_url, :website, :tags, :notes, :raw_data, :updated_at)""",
                data,
            )
            row_id = conn.execute(
                "SELECT id FROM contacts WHERE airtable_id = ?", (airtable_id,)
            ).fetchone()["id"]
            return row_id, True


def get_contact(contact_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return dict(row) if row else None


def get_contact_by_name(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE LOWER(name) LIKE ?",
            (f"%{name.lower()}%",),
        ).fetchone()
        return dict(row) if row else None


def list_contacts(search: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if search:
            term = f"%{search.lower()}%"
            rows = conn.execute(
                """SELECT c.*, cp.summary, cp.enriched_at
                   FROM contacts c
                   LEFT JOIN contact_profiles cp ON cp.contact_id = c.id
                   WHERE LOWER(c.name)     LIKE ?
                      OR LOWER(c.company)  LIKE ?
                      OR LOWER(c.title)    LIKE ?
                      OR LOWER(c.notes)    LIKE ?
                      OR LOWER(cp.summary) LIKE ?
                   ORDER BY c.name""",
                (term, term, term, term, term),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*, cp.summary, cp.enriched_at
                   FROM contacts c
                   LEFT JOIN contact_profiles cp ON cp.contact_id = c.id
                   ORDER BY c.name""",
            ).fetchall()
        return [dict(r) for r in rows]


def get_contacts_needing_enrichment(max_age_days: int = 30) -> list[dict]:
    """Return contacts with no profile or profiles older than max_age_days."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*
               FROM contacts c
               LEFT JOIN contact_profiles cp ON cp.contact_id = c.id
               WHERE cp.id IS NULL
                  OR (julianday('now') - julianday(cp.enriched_at)) > ?
               ORDER BY c.name""",
            (max_age_days,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_contacts() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]


# ── Profiles ───────────────────────────────────────────────────────────────────

def save_profile(contact_id: int, profile: dict) -> None:
    """Insert or replace an enriched profile."""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO contact_profiles
               (contact_id, summary, skills, interests, expertise_areas,
                work_history, education, notable_achievements, social_links,
                sources, enrichment_method, enriched_at)
               VALUES
               (:contact_id, :summary, :skills, :interests, :expertise_areas,
                :work_history, :education, :notable_achievements, :social_links,
                :sources, :enrichment_method, :enriched_at)""",
            {
                "contact_id":            contact_id,
                "summary":               profile.get("summary", ""),
                "skills":                json.dumps(profile.get("skills") or []),
                "interests":             json.dumps(profile.get("interests") or []),
                "expertise_areas":       json.dumps(profile.get("expertise_areas") or []),
                "work_history":          json.dumps(profile.get("work_history") or []),
                "education":             json.dumps(profile.get("education") or []),
                "notable_achievements":  json.dumps(profile.get("notable_achievements") or []),
                "social_links":          json.dumps(profile.get("social_links") or []),
                "sources":               json.dumps(profile.get("sources") or []),
                "enrichment_method":     profile.get("enrichment_method", "basic"),
                "enriched_at":           datetime.now().isoformat(),
            },
        )


def get_profile(contact_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM contact_profiles WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        if not row:
            return None
        p = dict(row)
        for key in ("skills", "interests", "expertise_areas", "work_history",
                    "education", "notable_achievements", "social_links", "sources"):
            p[key] = json.loads(p.get(key) or "[]")
        return p


def get_all_contacts_with_profiles() -> list[dict]:
    """Return all contacts joined with their profiles (if any)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, cp.summary, cp.skills, cp.interests, cp.expertise_areas,
                      cp.work_history, cp.education, cp.notable_achievements,
                      cp.enrichment_method
               FROM contacts c
               LEFT JOIN contact_profiles cp ON cp.contact_id = c.id
               ORDER BY c.name"""
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        for key in ("skills", "interests", "expertise_areas", "work_history",
                    "education", "notable_achievements"):
            raw = d.get(key)
            d[key] = json.loads(raw) if raw else []
        tags_raw = d.get("tags")
        d["tags"] = json.loads(tags_raw) if tags_raw else []
        result.append(d)
    return result


# ── Sync criteria ──────────────────────────────────────────────────────────────

VALID_OPERATORS = ("equals", "not_equals", "contains", "not_empty", "empty")


def list_criteria() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_criteria ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def add_criteria(name: str, field: str, operator: str, value: str | None) -> int:
    if operator not in VALID_OPERATORS:
        raise ValueError(f"operator must be one of: {', '.join(VALID_OPERATORS)}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sync_criteria (name, field, operator, value) VALUES (?, ?, ?, ?)",
            (name, field, operator, value),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def remove_criteria(criteria_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM sync_criteria WHERE id = ?", (criteria_id,)
        )
        return cursor.rowcount > 0


def toggle_criteria(criteria_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sync_criteria SET active = ? WHERE id = ?",
            (1 if active else 0, criteria_id),
        )


# ── Sync log ───────────────────────────────────────────────────────────────────

def log_sync(added: int, updated: int, total: int,
             status: str = "success", error: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sync_log (contacts_added, contacts_updated, contacts_total, status, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (added, updated, total, status, error),
        )


def get_last_sync() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
