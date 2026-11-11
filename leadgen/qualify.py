"""Decide whether a lead is worth working, how strong it is, and draft outreach.

Two layers:
  1. Keyword match — how many query words appear on the lead's own pages (always on, free).
  2. LLM review    — any configured model reads the lead summary, judges fit against the
                     business profile, and (when no work contact was found) drafts a
                     LinkedIn reach-out message from the content it saw.

With no LLM configured, keyword scoring decides and no drafts are written.
"""

from __future__ import annotations

import re

from .llm import LLM, LLMError, complete_json

STOP_WORDS = {"the", "and", "for", "with", "of", "in", "to", "a", "an", "on", "by", "our", "your"}
KEYWORD_ONLY_MIN_PCT = 25   # keyword-only mode: below this the lead is rejected as irrelevant
HOT_MIN, WARM_MIN = 70, 40
MAX_MESSAGE_CHARS = 600     # LinkedIn connection/InMail messages should stay short


class QualifyError(RuntimeError):
    pass


def query_tokens(query: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in STOP_WORDS}


def keyword_match_pct(text: str, query: str) -> int:
    tokens = query_tokens(query)
    if not tokens:
        return 0
    body = text.lower()
    hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}", body))
    return round(hits / len(tokens) * 100)


class LLMQualifier:
    def __init__(self, llm: LLM, profile: dict):
        self.llm = llm
        self.profile = profile  # company_name, sender_name, sender_role, offering, ideal_customer

    def assess(self, lead: dict, need_message: bool) -> dict:
        p = self.profile
        system = (
            f"You are the senior business development lead at {p.get('company_name') or 'our firm'}"
            f" ({p.get('sender_role') or 'business development'}). You qualify prospects and write"
            " short, personal, professional outreach. Never invent facts about the prospect; only"
            " use what the provided material says. Return JSON only."
        )
        draft_rule = (
            f'"outreach_message": a LinkedIn message under {MAX_MESSAGE_CHARS} characters, from'
            f" {p.get('sender_name') or 'the sender'} at {p.get('company_name') or 'our firm'},"
            " referencing one specific thing from the prospect's material, offering a short call,"
            " no hard sell, no emojis, signed with the sender name."
            if need_message else
            '"outreach_message": "" (a work contact was already found, no message needed).'
        )
        user = (
            f"What we offer: {p.get('offering')}\n"
            f"Ideal customer: {p.get('ideal_customer')}\n\n"
            f"Prospect type: {lead.get('kind')} found via {lead.get('channel')} search\n"
            f"Search query: {lead.get('query')}\n"
            f"Name: {lead.get('person_name') or '-'}   Role: {lead.get('role') or '-'}\n"
            f"Company: {lead.get('company_name') or '-'}   Website: {lead.get('website') or '-'}\n"
            f"Search title: {lead.get('title')}\nSearch snippet: {lead.get('snippet')}\n"
            f"Page description: {lead.get('description') or '-'}\n"
            f"Page text (truncated):\n{(lead.get('text') or '')[:4000]}\n\n"
            "Return a JSON object with keys:\n"
            '  "relevant": boolean — true only if this is a real person/company matching the ideal customer;\n'
            '  "score": integer 0-100 — strength of fit;\n'
            '  "company_name": string — clean company name ("" if unknown);\n'
            '  "country": string — the country where the person or firm is based ("" if unknown);\n'
            '  "reason": string — one short sentence explaining the score;\n'
            f"  {draft_rule}"
        )
        try:
            result = complete_json(self.llm, system, user, max_tokens=900)
        except LLMError as exc:
            raise QualifyError(str(exc)) from exc
        message = str(result.get("outreach_message", "") or "").strip()[:MAX_MESSAGE_CHARS]
        return {
            "relevant": bool(result.get("relevant", False)),
            "score": max(0, min(100, int(result.get("score", 0) or 0))),
            "company_name": str(result.get("company_name", "") or "").strip()[:120],
            "country": str(result.get("country", "") or "").strip()[:60],
            "reason": str(result.get("reason", "") or "").strip()[:300],
            "outreach_message": message if need_message else "",
        }


def combine_score(keyword_pct: int, has_work_contact: bool, ai: dict | None) -> tuple[int, str]:
    """Return (score 0-100, tier). Tier is one of HOT / WARM / COLD / REJECT."""
    if ai is not None:
        if not ai["relevant"]:
            return min(keyword_pct, 30), "REJECT"
        score = round(0.7 * ai["score"] + 0.3 * keyword_pct)
    else:
        if keyword_pct < KEYWORD_ONLY_MIN_PCT:
            return keyword_pct, "REJECT"
        score = keyword_pct
    if has_work_contact:
        score = min(100, score + 5)  # a reachable work contact is worth a little more
    tier = "HOT" if score >= HOT_MIN else "WARM" if score >= WARM_MIN else "COLD"
    return score, tier
