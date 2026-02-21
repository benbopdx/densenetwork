"""
Contact profile enrichment.

Strategy:
  1. Search the web for public info about the contact (Brave Search API).
  2. Pass search snippets + known Airtable data to Claude.
  3. Claude returns a structured JSON profile.
  4. Store the profile in contact_profiles.

If BRAVE_API_KEY is not set, a basic profile is inferred from the
data already stored in Airtable (name, title, company, notes, etc.)
and Claude's general knowledge.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
import requests
from pydantic import BaseModel

from . import config, database


# ── Pydantic schema for structured enrichment ─────────────────────────────────

class WorkItem(BaseModel):
    company: str
    role: str
    period: str | None = None


class EduItem(BaseModel):
    school: str
    degree: str | None = None
    field: str | None = None


class EnrichedProfile(BaseModel):
    summary: str
    skills: list[str]
    interests: list[str]
    expertise_areas: list[str]
    work_history: list[WorkItem]
    education: list[EduItem]
    notable_achievements: list[str]
    social_links: list[str]


# ── Brave Search ───────────────────────────────────────────────────────────────

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _brave_search(query: str, count: int = 5) -> list[dict[str, str]]:
    """
    Query the Brave Search API and return a list of
    {title, url, description} dicts.
    """
    headers = {
        "Accept":               "application/json",
        "Accept-Encoding":      "gzip",
        "X-Subscription-Token": config.BRAVE_API_KEY,
    }
    params = {"q": query, "count": count, "search_lang": "en"}
    try:
        resp = requests.get(BRAVE_ENDPOINT, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append({
                "title":       item.get("title", ""),
                "url":         item.get("url", ""),
                "description": item.get("description", ""),
            })
        return results
    except Exception as exc:
        return []  # graceful degradation – caller will proceed without results


def _gather_search_results(contact: dict) -> tuple[list[dict], list[str]]:
    """
    Run multiple search queries for the contact and return
    (results_list, source_urls).
    """
    name    = contact.get("name", "")
    company = contact.get("company", "")
    title   = contact.get("title", "")

    queries = []
    if name and company:
        queries.append(f"{name} {company}")
    if name and title:
        queries.append(f"{name} {title}")
    if name:
        queries.append(f"{name} site:linkedin.com")

    all_results: list[dict] = []
    seen_urls: set[str]     = set()

    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(0.5)          # be polite to the API
        for r in _brave_search(q, count=5):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    sources = [r["url"] for r in all_results]
    return all_results, sources


# ── Claude enrichment ──────────────────────────────────────────────────────────

_ENRICH_SYSTEM = """\
You are an expert researcher who extracts and structures professional
information about people from web search results and known data.

Your output must be valid JSON matching the schema provided.
Only include information you are reasonably confident about.
Do NOT invent work history, credentials, or personal details.
If you have little information, write a brief neutral summary and leave
lists empty rather than guessing."""


def _build_enrich_prompt(contact: dict, search_results: list[dict]) -> str:
    name    = contact.get("name", "Unknown")
    company = contact.get("company", "")
    title   = contact.get("title", "")
    location = contact.get("location", "")
    linkedin = contact.get("linkedin_url", "")
    notes    = contact.get("notes", "")

    known = f"Name: {name}"
    if title:   known += f"\nTitle: {title}"
    if company: known += f"\nCompany: {company}"
    if location: known += f"\nLocation: {location}"
    if linkedin: known += f"\nLinkedIn: {linkedin}"
    if notes:   known += f"\nNotes from my records: {notes}"

    if search_results:
        snippets = "\n\n".join(
            f"[{r['title']}] ({r['url']})\n{r['description']}"
            for r in search_results[:10]
        )
        search_section = f"\n\n## Web search results\n{snippets}"
    else:
        search_section = "\n\n(No web search results available – use only the data above.)"

    return f"""\
Build a structured professional profile for the person described below.

## Known information
{known}{search_section}

Return a JSON object with these exact keys:
{{
  "summary":              "<2-3 sentence professional bio>",
  "skills":               ["skill1", "skill2", ...],
  "interests":            ["interest1", "interest2", ...],
  "expertise_areas":      ["area1", "area2", ...],
  "work_history":         [{{"company": "...", "role": "...", "period": "..."}}],
  "education":            [{{"school": "...", "degree": "...", "field": "..."}}],
  "notable_achievements": ["achievement1", ...],
  "social_links":         ["url1", ...]
}}"""


def _call_claude_enrich(prompt: str) -> EnrichedProfile:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.parse(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=_ENRICH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=EnrichedProfile,
    )
    return response.parsed_output


# ── Public API ─────────────────────────────────────────────────────────────────

def enrich_contact(contact: dict) -> dict:
    """
    Enrich a single contact dict and return the profile dict.
    Saves the profile to the database automatically.
    """
    contact_id = contact["id"]
    use_brave  = bool(config.BRAVE_API_KEY)

    if use_brave:
        search_results, sources = _gather_search_results(contact)
        method = "web_search"
    else:
        search_results, sources = [], []
        method = "basic"

    prompt  = _build_enrich_prompt(contact, search_results)
    profile = _call_claude_enrich(prompt)

    profile_dict = {
        "summary":               profile.summary,
        "skills":                profile.skills,
        "interests":             profile.interests,
        "expertise_areas":       profile.expertise_areas,
        "work_history":          [w.model_dump() for w in profile.work_history],
        "education":             [e.model_dump() for e in profile.education],
        "notable_achievements":  profile.notable_achievements,
        "social_links":          profile.social_links,
        "sources":               sources,
        "enrichment_method":     method,
    }

    database.save_profile(contact_id, profile_dict)
    return profile_dict


def enrich_all(
    force: bool = False,
    max_age_days: int = 30,
    on_progress: Any = None,
) -> dict[str, int]:
    """
    Enrich all contacts that need it.

    Args:
        force:        Re-enrich even contacts with recent profiles.
        max_age_days: Re-enrich profiles older than this many days.
        on_progress:  Optional callable(contact_name, index, total) for progress.

    Returns {"enriched": N, "skipped": N, "errors": N}.
    """
    if force:
        contacts = database.list_contacts()
    else:
        contacts = database.get_contacts_needing_enrichment(max_age_days)

    enriched = skipped = errors = 0
    total    = len(contacts)

    for i, contact in enumerate(contacts):
        if on_progress:
            on_progress(contact.get("name", "?"), i + 1, total)
        try:
            enrich_contact(contact)
            enriched += 1
            time.sleep(0.3)   # brief pause between API calls
        except Exception as exc:
            errors += 1

    return {"enriched": enriched, "skipped": skipped, "errors": errors}
