"""Sync contacts from Airtable into the local SQLite database."""

from __future__ import annotations

from typing import Any

from . import config, database


# ── Formula builder ────────────────────────────────────────────────────────────

def _escape(value: str) -> str:
    """Escape a value for use inside an Airtable formula string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_formula(criteria: list[dict]) -> str | None:
    """
    Convert stored criteria rows into an Airtable formula string.

    Supported operators:
      equals      – {Field} = 'value'
      not_equals  – {Field} != 'value'
      contains    – FIND('value', LOWER({Field}))
      not_empty   – NOT({Field} = '')
      empty       – {Field} = ''
    """
    active = [c for c in criteria if c.get("active")]
    if not active:
        return None

    parts: list[str] = []
    for c in active:
        field = c["field"]
        op    = c["operator"]
        val   = c.get("value") or ""

        if op == "equals":
            parts.append(f"{{{field}}} = '{_escape(val)}'")
        elif op == "not_equals":
            parts.append(f"{{{field}}} != '{_escape(val)}'")
        elif op == "contains":
            parts.append(f"FIND('{_escape(val.lower())}', LOWER({{{field}}}))")
        elif op == "not_empty":
            parts.append(f"NOT({{{field}}} = '')")
        elif op == "empty":
            parts.append(f"{{{field}}} = ''")

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "AND(" + ", ".join(parts) + ")"


# ── Field mapper ───────────────────────────────────────────────────────────────

def _map_fields(airtable_fields: dict[str, Any]) -> dict[str, Any]:
    """
    Map Airtable field names → our internal field names, using FIELD_MAP.
    Unknown fields are captured in `_raw`.
    """
    fm = config.FIELD_MAP
    # Reverse the map: airtable_name → internal_name
    reverse: dict[str, str] = {v: k for k, v in fm.items()}

    mapped: dict[str, Any] = {"_raw": airtable_fields}
    for at_name, value in airtable_fields.items():
        internal = reverse.get(at_name)
        if internal:
            # Tags / multi-select come back as lists from Airtable
            if isinstance(value, list) and internal not in ("tags",):
                value = ", ".join(str(v) for v in value)
            mapped[internal] = value

    return mapped


# ── Main sync function ─────────────────────────────────────────────────────────

def sync(verbose: bool = False) -> dict[str, int]:
    """
    Pull contacts from Airtable and upsert them into the local database.

    Returns {"added": N, "updated": N, "total": N}.
    """
    try:
        from pyairtable import Api
    except ImportError as exc:
        raise RuntimeError(
            "pyairtable is not installed. Run: pip install pyairtable"
        ) from exc

    missing = config.check_required()
    if any(k in missing for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID")):
        raise RuntimeError(
            "AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set. "
            "Copy .env.example → .env and fill in your credentials."
        )

    api   = Api(config.AIRTABLE_API_KEY)
    table = api.base(config.AIRTABLE_BASE_ID).table(config.AIRTABLE_TABLE_NAME)

    criteria = database.list_criteria()
    formula  = _build_formula(criteria)

    if verbose and formula:
        print(f"[airtable] Using filter formula: {formula}")

    kwargs: dict[str, Any] = {}
    if formula:
        kwargs["formula"] = formula

    records = table.all(**kwargs)

    added = updated = 0
    for record in records:
        airtable_id = record["id"]
        fields      = _map_fields(record.get("fields", {}))
        _, created  = database.upsert_contact(airtable_id, fields)
        if created:
            added += 1
        else:
            updated += 1

    total = added + updated
    database.log_sync(added, updated, total)
    return {"added": added, "updated": updated, "total": total}


# ── Field discovery ────────────────────────────────────────────────────────────

def list_airtable_fields() -> list[str]:
    """
    Fetch a single record from Airtable and return the field names.
    Useful for configuring FIELD_MAP.
    """
    try:
        from pyairtable import Api
    except ImportError as exc:
        raise RuntimeError("pyairtable is not installed. Run: pip install pyairtable") from exc

    api    = Api(config.AIRTABLE_API_KEY)
    table  = api.base(config.AIRTABLE_BASE_ID).table(config.AIRTABLE_TABLE_NAME)
    record = table.first()
    if not record:
        return []
    return sorted(record.get("fields", {}).keys())
