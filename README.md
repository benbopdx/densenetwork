# DenseNet

AI-powered contact network management. Sync your top contacts from Airtable, enrich their profiles with public data, and ask Claude anything about your network.

## What it does

1. **Sync** – pulls your contacts from Airtable using configurable filters
2. **Enrich** – builds a rich profile on each contact (skills, interests, work history, expertise) by searching public information and structuring it with Claude
3. **Ask** – chat with Claude about your network to find the right people for any goal
4. **Digest** – scans your Granola meeting notes each morning and emails you everything you said you'd do

## Quick start

### 1. Install

```bash
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

Minimum required:

| Variable | Where to get it |
|----------|----------------|
| `AIRTABLE_API_KEY` | [airtable.com/account](https://airtable.com/account) → Personal access tokens |
| `AIRTABLE_BASE_ID` | From your Airtable base URL: `airtable.com/appXXXXXX/...` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

Optional but recommended:

| Variable | Purpose |
|----------|---------|
| `BRAVE_API_KEY` | Enables web-search enrichment for much richer profiles |
| `AIRTABLE_TABLE_NAME` | Table name (default: `Contacts`) |

### 3. Discover your Airtable field names

```bash
densenet fields
```

This fetches a sample record and shows all column names. Use the output to configure field mappings if your columns have non-default names.

### 4. Sync your contacts

```bash
densenet sync
```

### 5. Enrich profiles

```bash
densenet enrich          # enrich contacts with no profile yet
densenet enrich --all    # enrich all contacts
```

### 6. Ask questions

```bash
# One-shot
densenet ask "Who in my network works in AI/ML?"
densenet ask "Who should I talk to about fundraising for a B2B SaaS?"
densenet ask "Find me people who are interested in climate tech"

# Interactive conversation
densenet ask
```

### 7. Get your morning task digest

```bash
densenet digest --dry-run         # preview what would be sent
densenet digest                   # send the email now
```

Schedule it to run at 7 AM with cron (see [Granola digest setup](#granola-digest-setup) below).

---

## All commands

```
densenet sync                     Pull contacts from Airtable
densenet enrich                   Enrich un-profiled contacts
densenet enrich --all             Enrich all contacts
densenet enrich --force           Re-enrich even fresh profiles
densenet enrich ID                Enrich a single contact by ID
densenet ask [QUESTION]           AI Q&A (interactive if no QUESTION)
densenet list                     List all contacts
densenet list --search TERM       Filter contacts by keyword
densenet show ID                  Detailed view of one contact
densenet criteria list            Show active sync filters
densenet criteria add ...         Add a sync filter
densenet criteria remove ID       Remove a sync filter
densenet fields                   Show Airtable field names
densenet stats                    Database statistics
densenet digest                   Scan Granola notes, send task digest email
densenet digest --dry-run         Preview digest without sending
densenet digest --date YYYY-MM-DD Scan a specific day's notes
densenet digest --scan-only       List notes found without extracting tasks
```

---

## Configuring sync criteria

Criteria let you sync only a subset of your Airtable. For example, to sync only contacts tagged "Top" in a "Category" field:

```bash
densenet criteria add --field "Category" --operator equals --value "Top"
```

Then run `densenet sync` – only matching rows will be imported.

Available operators:

| Operator | Behaviour |
|----------|-----------|
| `equals` | Field exactly matches value |
| `not_equals` | Field does not match value |
| `contains` | Field contains value (case-insensitive) |
| `not_empty` | Field has any value |
| `empty` | Field is blank |

Multiple criteria are combined with AND.

---

## Field name mapping

If your Airtable columns have non-default names, map them in `.env`:

```dotenv
FIELD_NAME=Full Name
FIELD_EMAIL=Work Email
FIELD_COMPANY=Organization
FIELD_TITLE=Job Title
FIELD_LINKEDIN=LinkedIn Profile URL
FIELD_NOTES=My Notes
```

Run `densenet fields` to see all available column names.

---

## How enrichment works

1. **Web search** (requires `BRAVE_API_KEY`): runs 2–3 targeted searches for each contact (name + company, name + title, LinkedIn) using Brave Search
2. **Claude** structures the search snippets into a clean profile: summary, skills, interests, expertise areas, work history, education, achievements
3. Profile is stored locally in SQLite; re-enrichment respects a 30-day freshness window

Without a Brave API key, Claude still builds a basic profile from the Airtable data you already have.

---

## How AI search works

`densenet ask` uses Claude Opus 4.6 with adaptive thinking:

- All contact profiles are included in the system prompt (with prompt caching for efficiency)
- You can have a multi-turn conversation – context is maintained within the session
- Claude reasons about your whole network to surface the most relevant connections and explain *why* each person is a good match

---

## Granola digest setup

`densenet digest` reads your [Granola](https://granola.ai) meeting notes, uses Claude to extract every action item you personally committed to, and sends you a clean email digest.

### 1. Configure email in `.env`

```dotenv
# Recipient and sender
EMAIL_TO=you@example.com
EMAIL_FROM=you@example.com

# SMTP (Gmail example — use an App Password, not your regular password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail App Password
```

For Gmail, generate an App Password at: **Google Account → Security → App Passwords**.

### 2. Configure Granola path (optional)

By default, the digest looks for Granola's SQLite database at:
```
~/Library/Application Support/Granola/granola.db
```

Override with `.env`:
```dotenv
GRANOLA_DB_PATH=~/Library/Application Support/Granola/granola.db
```

If you export notes as Markdown files instead, point to that directory:
```dotenv
GRANOLA_NOTES_DIR=~/Documents/granola-notes
```
Files must contain the date in their name, e.g. `2025-03-14-standup.md`.

### 3. Test it

```bash
densenet digest --scan-only    # confirm notes are being found
densenet digest --dry-run      # preview the extracted tasks
densenet digest                # send the real email
```

### 4. Schedule at 7 AM with cron

```bash
crontab -e
```

Add this line (replace `/path/to/your/project` with the actual path):

```cron
0 7 * * 1-5 cd /path/to/your/project && /usr/local/bin/densenet digest >> ~/densenet-digest.log 2>&1
```

This runs Monday–Friday at 7:00 AM, scans yesterday's notes, and sends the email. Logs go to `~/densenet-digest.log`.

> **Tip:** Run `which densenet` to get the full path to the binary.

---

## Database

Contacts and profiles are stored in `densenet.db` (SQLite) in your current directory. Override the path with `DATABASE_PATH=` in `.env`.

The database is local and private – no data leaves your machine except API calls to Airtable, Anthropic, and (optionally) Brave Search.
