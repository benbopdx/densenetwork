"""Configuration loaded from environment variables / .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load from .env in the current working directory (or any parent)
load_dotenv()

# ── Airtable ──────────────────────────────────────────────────────────────────
AIRTABLE_API_KEY: str | None = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID: str | None = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME: str = os.getenv("AIRTABLE_TABLE_NAME", "Contacts")

# ── Anthropic / Claude ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")

# ── Brave Search (optional) ───────────────────────────────────────────────────
BRAVE_API_KEY: str | None = os.getenv("BRAVE_API_KEY")

# ── Storage ───────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "densenet.db")

# ── Granola notes ─────────────────────────────────────────────────────────────
# Primary: path to Granola's SQLite database (macOS default shown).
GRANOLA_DB_PATH: str | None = os.getenv(
    "GRANOLA_DB_PATH",
    "~/Library/Application Support/Granola/granola.db",
)
# Fallback: directory of exported note files (.md / .txt)
GRANOLA_NOTES_DIR: str | None = os.getenv("GRANOLA_NOTES_DIR")

# ── Email / SMTP ──────────────────────────────────────────────────────────────
EMAIL_TO: str | None = os.getenv("EMAIL_TO")          # digest recipient
EMAIL_FROM: str | None = os.getenv("EMAIL_FROM")      # sender address
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str | None = os.getenv("SMTP_USER")
SMTP_PASSWORD: str | None = os.getenv("SMTP_PASSWORD")

# ── Airtable field-name mappings ──────────────────────────────────────────────
# Maps our internal field names → Airtable column names.
FIELD_MAP: dict[str, str] = {
    "name":         os.getenv("FIELD_NAME",     "Name"),
    "email":        os.getenv("FIELD_EMAIL",    "Email"),
    "company":      os.getenv("FIELD_COMPANY",  "Company"),
    "title":        os.getenv("FIELD_TITLE",    "Title"),
    "phone":        os.getenv("FIELD_PHONE",    "Phone"),
    "location":     os.getenv("FIELD_LOCATION", "Location"),
    "linkedin_url": os.getenv("FIELD_LINKEDIN", "LinkedIn URL"),
    "twitter_url":  os.getenv("FIELD_TWITTER",  "Twitter"),
    "website":      os.getenv("FIELD_WEBSITE",  "Website"),
    "notes":        os.getenv("FIELD_NOTES",    "Notes"),
    "tags":         os.getenv("FIELD_TAGS",     "Tags"),
}


def check_required() -> list[str]:
    """Return a list of missing required config keys."""
    missing = []
    if not AIRTABLE_API_KEY:
        missing.append("AIRTABLE_API_KEY")
    if not AIRTABLE_BASE_ID:
        missing.append("AIRTABLE_BASE_ID")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    return missing


def check_digest_required() -> list[str]:
    """Return missing keys needed by the digest command."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not EMAIL_TO:
        missing.append("EMAIL_TO")
    if not EMAIL_FROM:
        missing.append("EMAIL_FROM")
    if not SMTP_USER:
        missing.append("SMTP_USER")
    if not SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")
    return missing
