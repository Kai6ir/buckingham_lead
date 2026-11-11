"""Classify an introducing broker from its website: does it RAISE CAPITAL for CTAs /
managed futures / CPOs, or is it a hedging / execution-only shop?

This is the core of the IB-screening workflow. Keyword matching alone is not enough:
a brokerage can mention "futures" hundreds of times and only do agricultural hedging, and
a real capital-raising IB may never spell out "CTA". So the classifier separates
*investment terminology* from *evidence of capital-raising activity*, and always records the
website excerpt (and page) that supports its decision.

Output per firm:
  ib_type         capital_raiser | hedger | execution_only | uncertain
  raises_for_cta  yes | no | unclear
  hedging_only    yes | no
  mf_relevance    high | medium | low
  client_segment  e.g. "HNW / Institutional", "Ag / Commercial", "Retail", "Unknown"
  recommendation  High-priority review | Review | Exclude
  evidence_text   a short quote from the site
  evidence_url    the page it came from

With an LLM configured the model does the judgement (best at terminology-vs-evidence);
without one, the pattern rules below decide. Either way a column is only set from the page.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .llm import LLM, LLMError, complete_json

# --- Evidence of actually RAISING CAPITAL / distributing to investors ---
CAPITAL_RAISING = [
    r"raise\s+capital", r"capital\s+rais", r"capital\s+introduc", r"cap\s*intro",
    r"fund\s*rais", r"fund\s+marketing", r"third[-\s]?party\s+marketing", r"\bplacement\b",
    r"private\s+placement", r"allocat\w* to", r"raise\s+assets?", r"asset\s+rais",
    r"introduce\s+(?:clients|investors|capital)", r"investor\s+relations",
    r"distribut\w+ (?:partner|managed|fund)", r"feeder\s+fund", r"fund\s+of\s+funds",
    r"separately\s+managed\s+account", r"\bSMA\b", r"managed\s+account\s+program",
    r"access\s+to\s+(?:leading|top|established)\s+(?:CTA|manager|trading advisor)",
]
# --- Managed-futures / alternatives / CTA investment context ---
MANAGED_FUTURES = [
    r"\bCTA\b", r"commodity\s+trading\s+advisor", r"\bCPO\b", r"commodity\s+pool",
    r"managed\s+futures", r"liquid\s+alternative", r"alternative\s+investment",
    r"trend[-\s]following", r"systematic\s+(?:macro|trading)", r"hedge\s+fund",
    r"trading\s+program", r"track\s+record",
]
# --- Who they serve on the investor side ---
INVESTOR_ACCESS = [
    r"high[-\s]?net[-\s]?worth", r"\bHNW\b", r"accredited\s+investor", r"qualified\s+(?:eligible|purchaser|client)",
    r"\bQEP\b", r"family\s+office", r"institutional\s+investor", r"wealth\s+manage",
    r"private\s+client", r"sophisticated\s+investor",
]
# Commercial hedging. These are the businesses to exclude unless the same page also shows
# capital-raising. "Hedge fund" is an investment term and must not count as hedging.
COMMERCIAL_HEDGING = [
    r"agricultur\w+", r"\bgrain\b", r"livestock", r"\bcattle\b", r"\bdairy\b", r"\bcrop\b",
    r"\bproducer\b", r"\bfarmer\b", r"\belevator\b", r"cash\s+grain", r"crush\s+margin",
    r"\bethanol\b", r"\bpropane\b", r"\bdiesel\b", r"fuel\s+price",
    r"energy\s+(?:hedg|risk|procure)", r"(?:agricultur\w+|energy|commodity)\s+hedg\w*",
    r"commodity\s+(?:risk|producer)", r"commercial\s+(?:hedg|client|customer)", r"end[-\s]user",
    r"price\s+risk", r"input\s+cost", r"\bmerchandis", r"supply\s+chain",
    r"risk\s+management\s+(?:for|services|solutions)",
]
# --- Execution-only brokerage ---
EXECUTION_ONLY = [
    r"execution[-\s]only", r"order\s+routing", r"\bDMA\b", r"direct\s+market\s+access",
    r"give[-\s]?up", r"low[-\s]?latency", r"trade\s+execution", r"clearing\s+and\s+execution",
    r"self[-\s]directed", r"online\s+trading\s+platform", r"commission\s+rates?",
]

RECO_HIGH = "High-priority review"
RECO_REVIEW = "Review"
RECO_EXCLUDE = "Exclude"

QUALITY_QUALIFIED = "qualified"    # capital-raising IB with supporting evidence
QUALITY_UNCERTAIN = "uncertain"    # some investment language, not enough to qualify or exclude
QUALITY_IRRELEVANT = "irrelevant"  # hedging-only, execution-only, or no supporting match


@dataclass
class Classification:
    ib_type: str = "uncertain"
    raises_for_cta: str = "unclear"
    hedging_only: str = "no"
    mf_relevance: str = "low"
    client_segment: str = "Unknown"
    recommendation: str = RECO_REVIEW
    evidence_text: str = ""
    evidence_url: str = ""
    lead_quality: str = QUALITY_UNCERTAIN

    def as_fields(self) -> dict:
        return asdict(self)


def _hits(patterns: list[str], text: str) -> list[str]:
    found = []
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            found.append(m.group(0))
    return found


def _excerpt(text: str, needle: str, width: int = 160) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return ""
    start = max(0, i - width // 2)
    end = min(len(text), i + len(needle) + width // 2)
    return " ".join(text[start:end].split())


def recommend(ib_type: str, raises: str, hedging_only: str, mf: str) -> str:
    if hedging_only == "yes" or ib_type in ("hedger", "execution_only"):
        return RECO_EXCLUDE
    if ib_type == "capital_raiser" and raises == "yes" and hedging_only != "yes":
        return RECO_HIGH
    # Terminology without proof of raising stays in the review pile, not the qualified list.
    if ib_type == "capital_raiser" or mf in ("high", "medium"):
        return RECO_REVIEW
    return RECO_EXCLUDE


def lead_quality_for(ib_type: str, raises: str, hedging_only: str, recommendation: str) -> str:
    """Qualified leads are kept apart from uncertain and irrelevant results."""
    if (recommendation == RECO_HIGH and ib_type == "capital_raiser"
            and raises == "yes" and hedging_only != "yes"):
        return QUALITY_QUALIFIED
    if recommendation == RECO_REVIEW:
        return QUALITY_UNCERTAIN
    return QUALITY_IRRELEVANT


def classify_by_patterns(text: str, firm_name: str = "", url: str = "") -> Classification:
    """Rule-based classification used when no LLM is available. Evidence wins over terminology."""
    blob = " ".join((text or "").split())
    name = (firm_name or "").lower()
    raising = _hits(CAPITAL_RAISING, blob)
    mf = _hits(MANAGED_FUTURES, blob)
    investor = _hits(INVESTOR_ACCESS, blob)
    hedging = _hits(COMMERCIAL_HEDGING, blob + " " + name)
    execution = _hits(EXECUTION_ONLY, blob)

    # Capital-raising evidence beats terminology. A hedging or execution shop that also
    # shows this evidence is a partner candidate, not an exclusion.
    has_raising_evidence = bool(raising) or (bool(mf) and bool(investor))

    if has_raising_evidence:
        ib_type = "capital_raiser"
        # "Yes" needs raising language tied to managed futures or investors, or both of those
        # together. A bare "we raise capital" with no CTA context stays unclear.
        if (raising and (mf or investor)) or (mf and investor):
            raises_for_cta = "yes"
        else:
            raises_for_cta = "unclear"
        hedging_only = "no"
    elif hedging:
        ib_type = "hedger"
        raises_for_cta = "no"
        hedging_only = "yes"
    elif execution:
        ib_type = "execution_only"
        raises_for_cta = "no"
        hedging_only = "no"
    elif mf:
        ib_type = "uncertain"        # talks the language, no proof of raising — needs a human look
        raises_for_cta = "unclear"
        hedging_only = "no"
    else:
        ib_type = "uncertain"
        raises_for_cta = "unclear"
        hedging_only = "no"

    mf_relevance = "high" if (mf and (raising or investor)) else "medium" if mf else "low"
    segment = _segment_from(investor, hedging, blob)

    # Pick the most decision-relevant quote for the evidence column.
    needle = (raising or mf or hedging or execution or [""])[0]
    evidence = _excerpt(blob, needle) if needle else (blob[:160] if blob else "")

    reco = recommend(ib_type, raises_for_cta, hedging_only, mf_relevance)
    quality = lead_quality_for(ib_type, raises_for_cta, hedging_only, reco)
    return Classification(ib_type, raises_for_cta, hedging_only, mf_relevance, segment,
                          reco, evidence, url, quality)


def _segment_from(investor: list[str], commercial: list[str], blob: str) -> str:
    if investor:
        bits = []
        if re.search(r"institutional|family\s+office", blob, re.I):
            bits.append("Institutional")
        if re.search(r"high[-\s]?net[-\s]?worth|\bHNW\b|accredited|qualified|private\s+client|wealth", blob, re.I):
            bits.append("HNW")
        return " / ".join(dict.fromkeys(bits)) or "Investors"
    if commercial:
        return "Ag / Commercial"
    if re.search(r"\bretail\b", blob, re.I):
        return "Retail"
    return "Unknown"


def classify_with_llm(text: str, firm_name: str, url: str, llm: LLM) -> Classification:
    system = (
        "You screen introducing brokers for a CTA (managed-futures manager). The goal is to find IBs "
        "that RAISE CAPITAL / introduce investors to CTAs, managed futures, CPOs or liquid alternatives, "
        "and to exclude firms that only do commodity HEDGING (agricultural, energy, commercial) or are "
        "EXECUTION-ONLY brokers. Judge by evidence of capital-raising or investor distribution, NOT by how "
        "often investment words appear. Only use the provided text. Quote the exact sentence that justifies "
        "your decision. Return JSON only."
    )
    user = (
        f"Firm: {firm_name}\nURL: {url}\n\nWebsite text (truncated):\n{text[:5000]}\n\n"
        "Return a JSON object with keys:\n"
        '  "ib_type": "capital_raiser" | "hedger" | "execution_only" | "uncertain",\n'
        '  "raises_for_cta": "yes" | "no" | "unclear",\n'
        '  "hedging_only": "yes" | "no",\n'
        '  "mf_relevance": "high" | "medium" | "low",\n'
        '  "client_segment": short phrase, e.g. "HNW / Institutional", "Ag / Commercial", "Retail", "Unknown",\n'
        '  "evidence": the exact sentence from the text that supports the decision (verbatim, <= 240 chars).'
    )
    result = complete_json(llm, system, user, max_tokens=600)
    ib_type = str(result.get("ib_type", "uncertain")).strip().lower()
    if ib_type not in ("capital_raiser", "hedger", "execution_only", "uncertain"):
        ib_type = "uncertain"
    raises = str(result.get("raises_for_cta", "unclear")).strip().lower()
    raises = raises if raises in ("yes", "no", "unclear") else "unclear"
    hedging_only = "yes" if str(result.get("hedging_only", "")).strip().lower() == "yes" else "no"
    mf = str(result.get("mf_relevance", "low")).strip().lower()
    mf = mf if mf in ("high", "medium", "low") else "low"
    segment = str(result.get("client_segment", "") or "Unknown").strip()[:60] or "Unknown"
    evidence = str(result.get("evidence", "") or "").strip()[:240]
    reco = recommend(ib_type, raises, hedging_only, mf)
    quality = lead_quality_for(ib_type, raises, hedging_only, reco)
    return Classification(ib_type, raises, hedging_only, mf, segment, reco, evidence, url, quality)


def classify(text: str, firm_name: str, url: str, llm: LLM | None) -> Classification:
    """Classify from page text. Falls back to pattern rules if the LLM is absent or fails."""
    if not (text or "").strip():
        return Classification(
            evidence_url=url, recommendation=RECO_REVIEW, lead_quality=QUALITY_UNCERTAIN)
    if llm is not None:
        try:
            c = classify_with_llm(text, firm_name, url, llm)
            if not c.evidence_text:  # borrow a quote from the patterns if the model gave none
                c.evidence_text = classify_by_patterns(text, firm_name, url).evidence_text
            return c
        except LLMError:
            pass
    return classify_by_patterns(text, firm_name, url)
