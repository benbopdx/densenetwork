"""
End-of-day task digest agent.

Pipeline:
  1. Read today's (or yesterday's) Granola notes
  2. Ask Claude to extract every commitment / action item the user made
  3. Format the results into a friendly email
  4. Send via SMTP

Run directly:
  python -m densenet.digest           # scans yesterday, sends email
  python -m densenet.digest --dry-run # prints the digest, no email

Or via CLI:
  densenet digest
  densenet digest --dry-run
  densenet digest --date 2025-03-13
"""

from __future__ import annotations

import smtplib
import ssl
import textwrap
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic

from . import config, granola


# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a personal assistant that synthesizes someone's daily meeting notes
    into a clear, structured end-of-day brief.

    Produce exactly three sections in this order:

    1. What did you learn today?
       Summarize the key insights, new information, and important things discovered
       across all meetings. Focus on things that matter — skip small talk and logistics.

    2. What did you decide on today?
       Summarize the key decisions made during the day, noting which meeting they
       came from where helpful. Only include real decisions, not open discussions.

    3. What did you commit to do?
       List every action item, promise, or task the note-taker personally committed
       to — things *they* said they will do. Be specific and actionable.
       Do not include tasks assigned to others.

    Rules:
    - Use the exact section headings above, numbered 1–3.
    - Under each heading, use a short bulleted list (- item).
    - If a section has nothing to report, write "Nothing to report."
    - Do not invent or embellish — only use what is in the notes.
    - Output plain text only (no markdown code blocks or extra formatting).
    - Keep bullets concise — one clear sentence each.
""")

_USER_PROMPT_TMPL = textwrap.dedent("""\
    Here are my meeting notes from {date_label}. Please summarize them into the
    three-section brief.

    {notes_block}
""")


# ── Note formatting ────────────────────────────────────────────────────────────

def _format_notes_block(notes: list[dict]) -> str:
    sections: list[str] = []
    for i, note in enumerate(notes, 1):
        title = (note.get("title") or "Meeting").strip()
        content = (note.get("content") or "").strip()
        sections.append(f"--- Meeting {i}: {title} ---\n{content}")
    return "\n\n".join(sections)


# ── Claude extraction ──────────────────────────────────────────────────────────

def extract_tasks(notes: list[dict], notes_date: date) -> str:
    """
    Use Claude Opus 4.6 with adaptive thinking to extract action items.
    Returns the assistant's plain-text response.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    date_label = notes_date.strftime("%A, %B %-d, %Y")
    notes_block = _format_notes_block(notes)

    user_message = _USER_PROMPT_TMPL.format(
        date_label=date_label,
        notes_block=notes_block,
    )

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        final = stream.get_final_message()

    # Return the last text block (thinking blocks come first with adaptive thinking)
    for block in reversed(final.content):
        if block.type == "text":
            return block.text.strip()

    return "(No brief could be generated.)"


# ── Email sending ──────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body    {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              color: #222; max-width: 640px; margin: 40px auto; padding: 0 20px; }}
    h2      {{ color: #1a1a2e; margin-bottom: 4px; }}
    .sub    {{ color: #666; font-size: 14px; margin-bottom: 28px; }}
    pre     {{ white-space: pre-wrap; font-family: inherit; font-size: 15px;
              line-height: 1.7; background: #f7f7f9; border-left: 3px solid #6366f1;
              padding: 20px 24px; border-radius: 4px; }}
    .footer {{ font-size: 12px; color: #aaa; margin-top: 32px; border-top: 1px solid #eee;
              padding-top: 12px; }}
  </style>
</head>
<body>
  <h2>Your daily brief — {date_label}</h2>
  <p class="sub">Here's what happened in your meetings yesterday.</p>
  <pre>{digest_escaped}</pre>
  <p class="footer">Sent by DenseNet · {date_label}</p>
</body>
</html>
"""


def _html_escape(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_digest_email(digest_text: str, notes_date: date) -> None:
    """Send the action-item digest via SMTP (STARTTLS on port 587)."""
    recipient  = config.EMAIL_TO
    sender     = config.EMAIL_FROM
    smtp_host  = config.SMTP_HOST
    smtp_port  = config.SMTP_PORT
    smtp_user  = config.SMTP_USER
    smtp_pass  = config.SMTP_PASSWORD

    date_label = notes_date.strftime("%A, %B %-d")
    subject    = f"Your daily brief — {date_label}"

    plain_body = (
        f"Your daily brief — {date_label}\n\n"
        f"{digest_text}\n\n"
        f"— DenseNet"
    )

    html_body = _HTML_TEMPLATE.format(
        date_label=date_label,
        digest_escaped=_html_escape(digest_text),
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.login(smtp_user, smtp_pass)
        smtp.sendmail(sender, recipient, msg.as_string())


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_digest(
    notes_date: date | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Full digest pipeline. Returns a result dict with keys:
      status       "sent" | "dry_run" | "no_notes"
      date         ISO date string of the notes scanned
      note_count   number of notes found
      digest       the extracted action items text (present unless no_notes)
    """
    if notes_date is None:
        notes_date = date.today() - timedelta(days=1)

    notes = granola.get_notes_for_date(notes_date)

    if not notes:
        return {
            "status":     "no_notes",
            "date":       notes_date.isoformat(),
            "note_count": 0,
        }

    digest_text = extract_tasks(notes, notes_date)

    if not dry_run:
        send_digest_email(digest_text, notes_date)

    return {
        "status":     "dry_run" if dry_run else "sent",
        "date":       notes_date.isoformat(),
        "note_count": len(notes),
        "digest":     digest_text,
    }


# ── Allow running as a script ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Run the Granola task digest.")
    parser.add_argument("--dry-run", action="store_true", help="Print digest, don't send.")
    parser.add_argument("--date", help="Notes date (YYYY-MM-DD). Default: yesterday.")
    args = parser.parse_args()

    target: date | None = None
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid date: {args.date}. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    result = run_digest(notes_date=target, dry_run=args.dry_run)
    print(f"Status: {result['status']}")
    if result["status"] == "no_notes":
        print(f"No notes found for {result['date']}.")
    else:
        print(f"Notes scanned: {result['note_count']}")
        print(f"\n{result['digest']}")
