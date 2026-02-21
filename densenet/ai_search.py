"""
Claude-powered search and Q&A over the contact network.

Uses:
  • claude-opus-4-6 with adaptive thinking for deep reasoning
  • Prompt caching on the contact context (large, static per session)
  • Streaming so responses appear token-by-token
"""

from __future__ import annotations

from collections.abc import Iterator

import anthropic

from . import config, database

# ── Contact context formatting ─────────────────────────────────────────────────

def _format_contact(c: dict) -> str:
    """Render a single contact (with optional enriched profile) as plain text."""
    lines: list[str] = []

    name = c.get("name") or "Unknown"
    lines.append(f"## {name}")

    meta: list[str] = []
    if c.get("title"):
        meta.append(c["title"])
    if c.get("company"):
        meta.append(c["company"])
    if meta:
        lines.append("Role: " + " @ ".join(meta))

    if c.get("location"):
        lines.append(f"Location: {c['location']}")
    if c.get("email"):
        lines.append(f"Email: {c['email']}")
    if c.get("linkedin_url"):
        lines.append(f"LinkedIn: {c['linkedin_url']}")
    if c.get("twitter_url"):
        lines.append(f"Twitter: {c['twitter_url']}")
    if c.get("website"):
        lines.append(f"Website: {c['website']}")

    tags = c.get("tags")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")

    # Enriched fields
    if c.get("summary"):
        lines.append(f"\nBio: {c['summary']}")

    skills = c.get("skills")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")

    interests = c.get("interests")
    if interests:
        lines.append(f"Interests: {', '.join(interests)}")

    expertise = c.get("expertise_areas")
    if expertise:
        lines.append(f"Expertise: {', '.join(expertise)}")

    work = c.get("work_history")
    if work:
        wh = [f"{w.get('role', '?')} at {w.get('company', '?')}"
              + (f" ({w['period']})" if w.get("period") else "")
              for w in work]
        lines.append(f"Work history: {' → '.join(wh)}")

    edu = c.get("education")
    if edu:
        ed = [f"{e.get('degree', '')} {e.get('field', '')} @ {e.get('school', '')}".strip()
              for e in edu]
        lines.append(f"Education: {'; '.join(ed)}")

    achievements = c.get("notable_achievements")
    if achievements:
        lines.append("Achievements: " + "; ".join(achievements))

    if c.get("notes"):
        lines.append(f"Notes: {c['notes']}")

    return "\n".join(lines)


def _build_context(contacts: list[dict]) -> str:
    blocks = [_format_contact(c) for c in contacts]
    return "\n\n---\n\n".join(blocks)


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are an AI assistant helping a professional manage and search their contact network.
You have access to {n} contacts, including their work history, skills, interests, and expertise.

Your capabilities:
- Answer questions about specific contacts or groups of contacts
- Suggest who to connect with for a given goal, topic, or opportunity
- Find domain experts, advisors, co-founders, collaborators, or investors
- Identify people with shared interests or complementary skills
- Help prioritize networking opportunities

When suggesting contacts always:
1. Name the person and their current role / company
2. Give a *specific* reason why they are a great match
3. Suggest what to say or ask when reaching out
4. Rank suggestions from most to least relevant

Be concise, direct, and practical.

────────────────────────
CONTACT NETWORK DATA
────────────────────────
{contact_data}"""


# ── Public streaming interface ─────────────────────────────────────────────────

def stream_ask(
    question: str,
    conversation_history: list[dict],
) -> Iterator[str]:
    """
    Stream Claude's response to a question about the contact network.

    Args:
        question:             The user's question or search query.
        conversation_history: Previous turns as [{"role": ..., "content": ...}].

    Yields:
        Text chunks as they arrive from the API.
    """
    contacts = database.get_all_contacts_with_profiles()
    if not contacts:
        yield (
            "No contacts found in the local database. "
            "Run `densenet sync` first to import contacts from Airtable."
        )
        return

    contact_data = _build_context(contacts)
    system_text  = _SYSTEM_TEMPLATE.format(n=len(contacts), contact_data=contact_data)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    messages = list(conversation_history) + [{"role": "user", "content": question}]

    # Wrap the system text in a cache-breakpoint block so repeated calls
    # (within the same session or close together) benefit from prompt caching.
    system = [
        {
            "type":          "text",
            "text":          system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def one_shot_ask(question: str) -> str:
    """Convenience wrapper that collects the full response as a string."""
    return "".join(stream_ask(question, []))
