"""Part 1 — find firms like the ones on the master list, and fill in its columns.

Queries are built from the master list itself: the registration types it contains, crossed with
the cities, states and countries it is concentrated in, plus the target markets from config.
Each result is turned into the master's columns: from the text of the page (LLM when configured,
pattern matching otherwise). Nothing is guessed: a column stays blank unless the page says it.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from .firms import CATEGORY_KEYWORDS, clean_id
from .llm import LLM, LLMError, complete_json

REG_PHRASES = [
    "commodity trading advisor managed futures",
    "commodity pool operator fund",
    "introducing broker futures",
    "retail forex dealer NFA member",
]

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming",
}

FIRM_FIELDS = [
    "firm_name", "city", "us_state", "province", "country", "nfa_member", "fcm", "introducing_broker",
    "cta", "cpo", "rfed", "forex_dealer", "forex_firm", "swap_dealer", "msp", "swap_firm",
    "floor_trader", "firm_id", "guarantor_name", "contact_name", "contact_title",
]
STATUS_FIELDS = {"nfa_member"}   # values APPROVED / PENDING
FLAG_FIELDS = {"fcm", "introducing_broker", "cta", "cpo", "rfed", "forex_dealer", "forex_firm",
               "swap_dealer", "msp", "swap_firm", "floor_trader"}


def firm_queries(master_rows: list[dict], n: int, target_countries: list[str]) -> list[tuple[str, str]]:
    """Return (channel, query) pairs. Channel 'google' for company sites, 'nfa' for NFA's own pages.

    Locations are taken round-robin, so every city, state and target market gets searched early,
    not just the first location on the list."""
    cities = [c for c, _ in Counter(r["city"].title() for r in master_rows if r.get("city")).most_common(4)]
    states = [s for s, _ in Counter(r["us_state"].title() for r in master_rows if r.get("us_state")).most_common(2)]
    countries = [c.title() for c in target_countries][:6]
    geos: list[str] = []
    for g in countries + cities + states:
        if g and g not in geos:
            geos.append(g)
    per_geo = [[(ch, f"{phrase} {geo}") for phrase in REG_PHRASES for ch in ("google", "nfa")] for geo in geos]
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i in range(max((len(p) for p in per_geo), default=0)):
        for group in per_geo:
            if i < len(group) and group[i] not in seen:
                seen.add(group[i])
                out.append(group[i])
    return out[:n]


def _clean_text(value: str, limit: int = 120) -> str:
    return " ".join((value or "").split())[:limit]


def extract_fields(text: str, title: str, url: str, llm: LLM | None) -> dict:
    """Map a page onto the master list's columns. Returns only the fields the page supports."""
    patterns = _extract_with_patterns(text, title)
    if llm is not None and text:
        try:
            llm_fields = _extract_with_llm(text, title, url, llm)
            # the model's answers win where it gave one; patterns fill any gaps
            return {**patterns, **{k: v for k, v in llm_fields.items() if v}}
        except LLMError:
            pass
    return patterns


def _extract_with_llm(text: str, title: str, url: str, llm: LLM) -> dict:
    system = ("You copy facts from a web page into a registry-style table. Only use what the page says. "
              "Leave a field as an empty string when the page does not say it. Return JSON only.")
    user = (
        f"Page title: {title}\nURL: {url}\n\nPage text (truncated):\n{text[:5000]}\n\n"
        "Return a JSON object with these keys (all strings):\n"
        "firm_name, city, us_state (US state name or ''), province (non-US region or ''), country,\n"
        "firm_id (NFA firm ID, digits only, or ''), nfa_member ('APPROVED' or 'PENDING' or ''),\n"
        "cta, cpo, introducing_broker, fcm, forex_firm, swap_firm ('REGISTERED', 'PENDING' or ''),\n"
        "contact_name, contact_title (only if a named person is shown, else '')."
    )
    raw = complete_json(llm, system, user, max_tokens=700)
    return _clean_fields({k: raw.get(k, "") for k in FIRM_FIELDS})


def _clean_fields(data: dict) -> dict:
    out = {}
    for key in FIRM_FIELDS:
        value = _clean_text(str(data.get(key) or ""), 120)
        if not value:
            continue
        if key == "firm_id":
            value = clean_id(value)
        elif key in STATUS_FIELDS:
            value = value.upper() if value.upper() in ("APPROVED", "PENDING") else ""
        elif key in FLAG_FIELDS:
            value = value.upper() if value.upper() in ("REGISTERED", "PENDING") else ""
        elif key in ("city", "us_state", "province", "country", "guarantor_name"):
            value = value.upper()
        if value:
            out[key] = value
    return out


def _extract_with_patterns(text: str, title: str) -> dict:
    out: dict = {}
    flat = " ".join((text or "").split())
    m = re.search(r"NFA\s*(?:ID|No\.?|Number|#)?\s*[:#]?\s*(\d{6,7})\b", flat, re.I)
    if m:
        out["firm_id"] = m.group(1)
    if title:
        name = re.split(r"\s[|\-–—]\s", title)[0].strip()
        if name and len(name) < 100:
            out["firm_name"] = name.upper()
    low = flat.lower()
    for key, patterns in CATEGORY_KEYWORDS.items():
        if any(re.search(p, flat, re.I) for p in patterns):
            if key in FLAG_FIELDS:
                out[key] = "REGISTERED"
    if re.search(r"NFA member", flat, re.I):
        out["nfa_member"] = "APPROVED"
    state = next((s for s in US_STATES if re.search(rf"\b{s}\b", low)), "")
    city_state = re.search(r"\b([A-Z][a-zA-Z.]+(?:\s[A-Z][a-zA-Z.]+){0,2}),\s*(" + "|".join(
        re.escape(s.title()) for s in US_STATES) + r")\b", flat)
    if city_state:
        out["city"] = city_state.group(1).strip().upper()
        out["us_state"] = city_state.group(2).upper()
    elif state:
        out["us_state"] = state.upper()
    if re.search(r"\bUnited States\b|\bUSA\b|\bU\.S\.", flat):
        out["country"] = "UNITED STATES"
    return out
