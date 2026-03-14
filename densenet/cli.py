"""
DenseNet CLI – entry point for all commands.

Usage:
  densenet sync                          # Pull contacts from Airtable
  densenet enrich [--all] [--force]      # Build rich profiles from public data
  densenet ask [QUESTION]                # AI Q&A (interactive if no QUESTION)
  densenet list [--search TERM]          # Browse contacts
  densenet show ID                       # Detailed view of one contact
  densenet criteria list                 # Show active sync filters
  densenet criteria add                  # Add a sync filter
  densenet criteria remove ID            # Remove a sync filter
  densenet fields                        # List available Airtable field names
  densenet stats                         # Database summary
"""

from __future__ import annotations

import json
import sys
from datetime import date

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.text import Text

from . import config, database, airtable_sync, enricher, ai_search, granola, digest

console = Console()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_config(*keys: str) -> None:
    missing = config.check_required()
    for k in keys:
        if k in missing:
            console.print(
                f"[bold red]Missing config:[/bold red] {k} is not set.\n"
                "Copy [dim].env.example[/dim] → [dim].env[/dim] and fill in your credentials.",
                highlight=False,
            )
            sys.exit(1)


def _require_db() -> None:
    database.init_db()


# ── Root group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="densenet")
def cli() -> None:
    """DenseNet – AI-powered contact network management."""
    pass


# ── sync ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Print Airtable formula used.")
def sync(verbose: bool) -> None:
    """Sync contacts from Airtable into the local database."""
    _require_config("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID")
    _require_db()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Syncing from Airtable…", total=None)
        try:
            result = airtable_sync.sync(verbose=verbose)
        except Exception as exc:
            console.print(f"[bold red]Sync failed:[/bold red] {exc}")
            sys.exit(1)

    console.print(
        f"[bold green]Sync complete.[/bold green] "
        f"Added [cyan]{result['added']}[/cyan], "
        f"updated [cyan]{result['updated']}[/cyan], "
        f"total [cyan]{result['total']}[/cyan] contacts."
    )


# ── enrich ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--all", "enrich_all", is_flag=True,
              help="Enrich all contacts (default: only un-enriched ones).")
@click.option("--force", is_flag=True,
              help="Re-enrich even contacts with fresh profiles.")
@click.option("--max-age", default=30, show_default=True,
              help="Re-enrich profiles older than N days.")
@click.argument("contact_id", required=False, type=int)
def enrich(enrich_all: bool, force: bool, max_age: int, contact_id: int | None) -> None:
    """
    Build rich profiles for contacts using web search + Claude.

    With no arguments, enriches contacts that have no profile yet.
    Provide a CONTACT_ID to enrich a single contact.
    """
    _require_config("ANTHROPIC_API_KEY")
    _require_db()

    if not config.BRAVE_API_KEY:
        console.print(
            "[yellow]Note:[/yellow] BRAVE_API_KEY is not set – "
            "profiles will be built from Airtable data only (no web search).\n"
            "Add your key to [dim].env[/dim] for richer profiles.",
            highlight=False,
        )

    # ── single contact ──
    if contact_id is not None:
        contact = database.get_contact(contact_id)
        if not contact:
            console.print(f"[bold red]Contact {contact_id} not found.[/bold red]")
            sys.exit(1)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task(f"Enriching {contact['name']}…", total=None)
            try:
                enricher.enrich_contact(contact)
            except Exception as exc:
                console.print(f"[bold red]Enrichment failed:[/bold red] {exc}")
                sys.exit(1)
        console.print(f"[bold green]Done.[/bold green] Profile saved for [cyan]{contact['name']}[/cyan].")
        return

    # ── batch ──
    if force:
        contacts = database.list_contacts()
    elif enrich_all:
        contacts = database.list_contacts()
    else:
        contacts = database.get_contacts_needing_enrichment(max_age)

    if not contacts:
        console.print("[dim]All contacts already have up-to-date profiles.[/dim]")
        return

    enriched = errors = 0
    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching…", total=len(contacts))
        for contact in contacts:
            progress.update(task, description=f"Enriching {contact.get('name', '?')}…")
            try:
                enricher.enrich_contact(contact)
                enriched += 1
            except Exception as exc:
                errors += 1
                console.print(
                    f"  [red]⚠[/red] {contact.get('name', '?')}: {exc}",
                    highlight=False,
                )
            progress.advance(task)

    console.print(
        f"[bold green]Enrichment complete.[/bold green] "
        f"[cyan]{enriched}[/cyan] succeeded, "
        f"[{'red' if errors else 'dim'}]{errors}[/{'red' if errors else 'dim'}] failed."
    )


# ── ask ────────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("question", required=False)
def ask(question: str | None) -> None:
    """
    Ask AI anything about your contact network.

    Run without QUESTION for an interactive conversation.

    Examples:
      densenet ask "Who knows about machine learning?"
      densenet ask "Who should I talk to about fundraising?"
      densenet ask "Find me advisors in healthcare"
    """
    _require_config("ANTHROPIC_API_KEY")
    _require_db()

    if database.count_contacts() == 0:
        console.print(
            "[bold yellow]No contacts in database.[/bold yellow] "
            "Run [cyan]densenet sync[/cyan] first."
        )
        sys.exit(1)

    def _stream_and_print(q: str, history: list[dict]) -> str:
        """Stream response, print live, return full text."""
        full_text = ""
        console.print()
        console.print("[bold blue]Assistant:[/bold blue]")
        try:
            for chunk in ai_search.stream_ask(q, history):
                print(chunk, end="", flush=True)
                full_text += chunk
        except anthropic.APIError as exc:
            console.print(f"\n[bold red]API error:[/bold red] {exc}")
        print()  # newline after stream
        return full_text

    # ── one-shot ──
    if question:
        _stream_and_print(question, [])
        return

    # ── interactive REPL ──
    history: list[dict] = []
    console.print(
        Panel.fit(
            "[bold]DenseNet AI Assistant[/bold]\n"
            "[dim]Ask anything about your network. Type [bold]exit[/bold] to quit.[/dim]",
            border_style="blue",
        )
    )

    while True:
        try:
            q = console.input("\n[bold green]You:[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if q.lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Goodbye![/dim]")
            break
        if not q:
            continue

        response_text = _stream_and_print(q, history)

        # Keep last 10 turns to avoid context bloat
        history.append({"role": "user",      "content": q})
        history.append({"role": "assistant",  "content": response_text})
        if len(history) > 20:
            history = history[-20:]


# ── list ───────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--search", "-s", default=None, help="Filter by keyword.")
@click.option("--enriched-only", is_flag=True, help="Show only enriched contacts.")
def list_contacts(search: str | None, enriched_only: bool) -> None:
    """List contacts in the local database."""
    _require_db()

    contacts = database.list_contacts(search)
    if enriched_only:
        contacts = [c for c in contacts if c.get("enriched_at")]

    if not contacts:
        console.print("[dim]No contacts found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", expand=True)
    table.add_column("#",         style="dim",   width=5,  no_wrap=True)
    table.add_column("Name",      style="bold",  min_width=20)
    table.add_column("Title",                    min_width=15)
    table.add_column("Company",                  min_width=15)
    table.add_column("Location",                 min_width=12)
    table.add_column("Enriched",  style="green", width=9,  no_wrap=True)

    for c in contacts:
        table.add_row(
            str(c["id"]),
            c.get("name")     or "",
            c.get("title")    or "",
            c.get("company")  or "",
            c.get("location") or "",
            "✓" if c.get("enriched_at") else "",
        )

    console.print(table)
    console.print(f"[dim]{len(contacts)} contact(s) shown.[/dim]")


# ── show ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("contact_id", type=int)
def show(contact_id: int) -> None:
    """Show full details for a single contact."""
    _require_db()

    contact = database.get_contact(contact_id)
    if not contact:
        console.print(f"[bold red]Contact {contact_id} not found.[/bold red]")
        sys.exit(1)

    profile = database.get_profile(contact_id)

    name = contact.get("name") or "Unknown"

    # ── Basic info ──
    lines: list[str] = []
    for label, key in [
        ("Title",    "title"),
        ("Company",  "company"),
        ("Location", "location"),
        ("Email",    "email"),
        ("Phone",    "phone"),
        ("LinkedIn", "linkedin_url"),
        ("Twitter",  "twitter_url"),
        ("Website",  "website"),
    ]:
        val = contact.get(key)
        if val:
            lines.append(f"**{label}:** {val}")

    tags = json.loads(contact.get("tags") or "[]")
    if tags:
        lines.append(f"**Tags:** {', '.join(tags)}")
    if contact.get("notes"):
        lines.append(f"**Notes:** {contact['notes']}")

    basic_md = "\n".join(lines) if lines else "_No info_"
    console.print(Panel(Markdown(basic_md), title=f"[bold]{name}[/bold]", border_style="blue"))

    # ── Profile ──
    if profile:
        p_lines: list[str] = []

        if profile.get("summary"):
            p_lines.append(f"{profile['summary']}\n")

        def _list_section(title: str, items: list) -> None:
            if items:
                p_lines.append(f"**{title}:** {', '.join(str(x) for x in items)}")

        _list_section("Skills",     profile.get("skills", []))
        _list_section("Interests",  profile.get("interests", []))
        _list_section("Expertise",  profile.get("expertise_areas", []))

        wh = profile.get("work_history", [])
        if wh:
            entries = [
                f"{w.get('role', '?')} @ {w.get('company', '?')}"
                + (f" ({w['period']})" if w.get("period") else "")
                for w in wh
            ]
            p_lines.append("**Work history:**")
            for e in entries:
                p_lines.append(f"  - {e}")

        edu = profile.get("education", [])
        if edu:
            entries = [
                f"{e.get('degree', '')} {e.get('field', '')} @ {e.get('school', '')}".strip()
                for e in edu
            ]
            p_lines.append("**Education:**")
            for e in entries:
                p_lines.append(f"  - {e}")

        achievements = profile.get("notable_achievements", [])
        if achievements:
            p_lines.append("**Achievements:**")
            for a in achievements:
                p_lines.append(f"  - {a}")

        method   = profile.get("enrichment_method", "")
        enriched = profile.get("enriched_at", "")
        p_lines.append(f"\n_Enriched: {enriched} ({method})_")

        profile_md = "\n".join(p_lines)
        console.print(Panel(Markdown(profile_md), title="[bold]Enriched Profile[/bold]", border_style="green"))
    else:
        console.print(
            "[dim]No enriched profile. Run [/dim][cyan]densenet enrich "
            f"{contact_id}[/cyan][dim] to build one.[/dim]"
        )


# ── criteria ───────────────────────────────────────────────────────────────────

@cli.group()
def criteria() -> None:
    """Manage Airtable sync filters (criteria)."""
    pass


@criteria.command("list")
def criteria_list() -> None:
    """Show all sync criteria."""
    _require_db()
    rows = database.list_criteria()
    if not rows:
        console.print("[dim]No criteria set. All Airtable records will be synced.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID",       style="dim", width=5)
    table.add_column("Name",     min_width=15)
    table.add_column("Field",    min_width=15)
    table.add_column("Operator", min_width=12)
    table.add_column("Value",    min_width=15)
    table.add_column("Active",   width=8)

    for r in rows:
        table.add_row(
            str(r["id"]),
            r.get("name") or "",
            r["field"],
            r["operator"],
            r.get("value") or "",
            "[green]✓[/green]" if r.get("active") else "[dim]–[/dim]",
        )
    console.print(table)


@criteria.command("add")
@click.option("--name",     "-n", default="",   help="Human-readable label for this filter.")
@click.option("--field",    "-f", required=True, help="Airtable field name.")
@click.option("--operator", "-o", required=True,
              type=click.Choice(database.VALID_OPERATORS, case_sensitive=False),
              help="Comparison operator.")
@click.option("--value",    "-v", default=None, help="Value to compare against.")
def criteria_add(name: str, field: str, operator: str, value: str | None) -> None:
    """Add a sync filter criterion."""
    _require_db()
    try:
        cid = database.add_criteria(name, field, operator, value)
        console.print(f"[bold green]Criterion #{cid} added.[/bold green]")
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


@criteria.command("remove")
@click.argument("criteria_id", type=int)
def criteria_remove(criteria_id: int) -> None:
    """Remove a sync filter criterion by ID."""
    _require_db()
    if database.remove_criteria(criteria_id):
        console.print(f"[bold green]Criterion #{criteria_id} removed.[/bold green]")
    else:
        console.print(f"[bold red]Criterion #{criteria_id} not found.[/bold red]")
        sys.exit(1)


# ── fields ─────────────────────────────────────────────────────────────────────

@cli.command()
def fields() -> None:
    """List all field names available in your Airtable table."""
    _require_config("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Fetching Airtable fields…", total=None)
        try:
            field_names = airtable_sync.list_airtable_fields()
        except Exception as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            sys.exit(1)

    if not field_names:
        console.print("[dim]No fields found (table may be empty).[/dim]")
        return

    console.print(
        f"\n[bold]{len(field_names)} fields found in "
        f"[cyan]{config.AIRTABLE_TABLE_NAME}[/cyan]:[/bold]"
    )
    for name in field_names:
        console.print(f"  [cyan]{name}[/cyan]")

    console.print(
        "\n[dim]To remap a field, set the corresponding environment variable "
        "(e.g. FIELD_LINKEDIN='LinkedIn Profile') in your .env file.[/dim]"
    )


# ── stats ──────────────────────────────────────────────────────────────────────

@cli.command()
def stats() -> None:
    """Show database statistics."""
    _require_db()

    contacts  = database.list_contacts()
    enriched  = [c for c in contacts if c.get("enriched_at")]
    last_sync = database.get_last_sync()
    criteria  = database.list_criteria()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value",  style="cyan")

    table.add_row("Total contacts",   str(len(contacts)))
    table.add_row("Enriched profiles", f"{len(enriched)} ({len(contacts) and 100*len(enriched)//len(contacts)}%)")
    table.add_row("Sync criteria",    str(len(criteria)))

    if last_sync:
        table.add_row("Last sync",   last_sync.get("synced_at", "–"))
        table.add_row("Last sync added",   str(last_sync.get("contacts_added", 0)))
        table.add_row("Last sync updated", str(last_sync.get("contacts_updated", 0)))

    enrichment_mode = "web search (Brave)" if config.BRAVE_API_KEY else "basic (no web search)"
    table.add_row("Enrichment mode", enrichment_mode)

    console.print(Panel(table, title="[bold]DenseNet Stats[/bold]", border_style="blue"))


# ── digest ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--date", "notes_date_str", default=None,
              help="Scan notes for this date (YYYY-MM-DD). Default: yesterday.")
@click.option("--dry-run", is_flag=True,
              help="Print the digest to the terminal instead of sending email.")
@click.option("--scan-only", is_flag=True,
              help="List notes found without extracting tasks or sending email.")
def digest_cmd(notes_date_str: str | None, dry_run: bool, scan_only: bool) -> None:
    """
    Scan Granola notes and email you your action items.

    Reads notes from Granola's local database (or GRANOLA_NOTES_DIR),
    extracts every commitment you made, and sends a digest email at any
    time you run the command (pair with a 7 AM cron job for morning delivery).

    \b
    Examples:
      densenet digest                   # scan yesterday, send email
      densenet digest --dry-run         # preview digest, no email sent
      densenet digest --date 2025-03-13 # scan a specific day
      densenet digest --scan-only       # just show what notes were found
    """
    # ── parse date ──
    target_date: date | None = None
    if notes_date_str:
        try:
            target_date = date.fromisoformat(notes_date_str)
        except ValueError:
            console.print(f"[bold red]Invalid date:[/bold red] {notes_date_str!r}. Use YYYY-MM-DD.")
            sys.exit(1)

    from datetime import timedelta
    display_date = target_date or (date.today() - timedelta(days=1))

    # ── show notes source ──
    source_desc = granola.notes_source_description()
    console.print(f"[dim]Notes source: {source_desc}[/dim]")

    # ── scan only ──
    if scan_only:
        notes = granola.get_notes_for_date(target_date)
        if not notes:
            console.print(
                f"[yellow]No notes found for {display_date.isoformat()}.[/yellow]\n"
                "Check GRANOLA_DB_PATH or GRANOLA_NOTES_DIR in your .env."
            )
            return
        console.print(f"\n[bold]{len(notes)} note(s) found for {display_date.isoformat()}:[/bold]")
        for i, note in enumerate(notes, 1):
            title = note.get("title") or "Untitled"
            words = len((note.get("content") or "").split())
            console.print(f"  {i}. [cyan]{title}[/cyan] [dim]({words} words)[/dim]")
        return

    # ── require config for full run ──
    if not dry_run:
        missing = config.check_digest_required()
        if missing:
            console.print(
                "[bold red]Missing config for email:[/bold red] "
                + ", ".join(missing)
                + "\nAdd these to your .env file."
            )
            sys.exit(1)
    else:
        # dry-run only needs Anthropic key
        if not config.ANTHROPIC_API_KEY:
            console.print("[bold red]Missing config:[/bold red] ANTHROPIC_API_KEY")
            sys.exit(1)

    # ── run digest pipeline ──
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(
            f"Scanning notes for {display_date.isoformat()}…",
            total=None,
        )
        try:
            result = digest.run_digest(notes_date=target_date, dry_run=dry_run)
        except Exception as exc:
            console.print(f"[bold red]Digest failed:[/bold red] {exc}")
            sys.exit(1)

    if result["status"] == "no_notes":
        console.print(
            f"[yellow]No notes found for {result['date']}.[/yellow]\n"
            "Check GRANOLA_DB_PATH or GRANOLA_NOTES_DIR in your .env."
        )
        return

    count = result["note_count"]

    if dry_run:
        console.print(
            Panel(
                result["digest"],
                title=f"[bold]Digest preview — {result['date']} ({count} note{'s' if count != 1 else ''})[/bold]",
                border_style="blue",
            )
        )
        console.print("[dim]Dry run — no email sent.[/dim]")
    else:
        console.print(
            f"[bold green]Digest sent![/bold green] "
            f"Scanned [cyan]{count}[/cyan] note{'s' if count != 1 else ''} from [cyan]{result['date']}[/cyan]. "
            f"Email sent to [cyan]{config.EMAIL_TO}[/cyan]."
        )


# Register digest under the name "digest" (click uses the function name minus _cmd)
cli.add_command(digest_cmd, name="digest")


if __name__ == "__main__":
    cli()
