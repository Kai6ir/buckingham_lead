"""Decide WHAT to search for.

With an LLM configured, the model reads the business profile and plans a mixed list of
Google and LinkedIn queries (who to look for, in which words). Without one, the seed
keywords in queries.txt are used on every enabled channel.
"""

from __future__ import annotations

from .llm import LLM, LLMError, complete_json


def plan_queries(llm: LLM | None, profile: dict, seeds: list[str], n: int,
                 channels: list[str]) -> list[tuple[str, str]]:
    """Return an ordered list of (channel, query) pairs, no duplicates."""
    planned: list[tuple[str, str]] = []
    if llm is not None and channels:
        system = ("You are a senior business development researcher. You design search queries that find"
                  " real prospects. Return JSON only.")
        user = (
            f"Our offering: {profile.get('offering')}\n"
            f"Ideal customer: {profile.get('ideal_customer')}\n"
            f"Seed keywords we already use: {', '.join(seeds[:40])}\n"
            f"Channels available: {', '.join(channels)}\n\n"
            f"Write {n} search queries in total, spread across the channels. "
            "google queries find firms' websites (use industry terms, no site: operators). "
            "linkedin queries find people's public profiles by role and industry "
            "(use job titles such as 'Head of Allocations', 'Managing Director', 'CIO'; "
            "no site: operator — it is added automatically).\n"
            'Return {"queries": [{"channel": "google" | "linkedin", "query": "..."}]}'
        )
        try:
            result = complete_json(llm, system, user, max_tokens=1500)
            for item in result.get("queries", []):
                channel = str(item.get("channel", "")).lower()
                query = str(item.get("query", "")).strip()
                if channel in channels and query:
                    planned.append((channel, query))
        except LLMError:
            planned = []  # fall back to seeds below

    if not planned:
        for seed in seeds:
            for channel in channels:
                planned.append((channel, seed))

    seen, unique = set(), []
    for pair in planned:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return _interleave(unique)[:n] if llm is not None else _interleave(unique)


def _interleave(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Round-robin across channels, so every source is searched early, not only the first."""
    buckets: dict[str, list] = {}
    for channel, query in pairs:
        buckets.setdefault(channel, []).append((channel, query))
    out = []
    while any(buckets.values()):
        for channel in list(buckets):
            if buckets[channel]:
                out.append(buckets[channel].pop(0))
    return out
