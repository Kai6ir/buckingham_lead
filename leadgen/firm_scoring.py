"""Part 2a — score a firm from the attributes in its master-list columns.

Every point has a reason, written to SCORE REASONS, so the boss can see why a firm ranked where it did.
Weights and tier cut-offs live in config.yaml under `firms:`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .firms import is_pending

SENIOR_TITLE = re.compile(
    r"\b(chief|cio|ceo|coo|cfo|founder|president|partner|principal|managing|director|head|owner)\b", re.I)

DEFAULT_WEIGHTS = {
    # IB-screening signals (website evidence) — these dominate the ranking
    "raises_for_cta": 45,       # evidence the IB raises capital for CTAs / managed futures
    "raises_unclear": 8,        # capital-raiser, but CTA fit not yet proven
    "mf_high": 20,              # strong managed-futures / alternatives relevance
    "mf_medium": 10,
    "investor_access": 8,       # serves HNW / institutional investors
    "capital_raiser": 10,       # classified as a capital-raising IB at all
    "hedging_only": -45,        # agricultural / energy / commercial hedging only — exclude
    "execution_only": -25,      # execution-only brokerage — exclude
    # NFA registration signals (secondary)
    "cta": 25,              # registered commodity trading advisor: runs managed-futures programmes
    "cpo": 20,              # registered commodity pool operator: runs funds we could allocate to
    "introducing_broker": 8,
    "fcm": 10,              # futures commission merchant: a larger, clearing-level firm
    "forex": 5,             # retail forex dealer / forex firm
    "swap": 5,              # swap dealer / major swap participant / swap firm
    "nfa_approved": 10,
    "nfa_pending": -8,
    "independent": 4,       # independent introducing broker (not guaranteed by a clearing firm)
    "target_country": 10,   # country in firms.target_countries
    "has_website": 5,
    "has_work_email": 10,
    "has_contact_name": 4,
    "senior_title": 8,
}
DEFAULT_TIERS = {"A": 60, "B": 40}  # A >= 60, B >= 40, else C


@dataclass
class Scored:
    score: int
    tier: str
    reasons: list[str]


def _registered(value: str) -> bool:
    return bool((value or "").strip()) and not is_pending(value)


def score_firm(firm: dict, weights: dict | None = None, target_countries: list[str] | None = None,
               tiers: dict | None = None) -> Scored:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    cut = {**DEFAULT_TIERS, **(tiers or {})}
    targets = {c.strip().upper() for c in (target_countries or [])}
    points = 0
    reasons: list[str] = []

    def add(pts: int, why: str) -> None:
        nonlocal points
        points += pts
        reasons.append(f"{why} ({pts:+d})")

    # ---- IB-screening signals from the website (dominant) ----
    ib_type = (firm.get("ib_type") or "").strip().lower()
    raises = (firm.get("raises_for_cta") or "").strip().lower()
    hedging_only = (firm.get("hedging_only") or "").strip().lower()
    mf = (firm.get("mf_relevance") or "").strip().lower()
    segment = (firm.get("client_segment") or "")
    force_c = False
    if raises == "yes":
        add(w["raises_for_cta"], "Evidence of raising capital for CTAs")
    elif ib_type == "capital_raiser" and raises == "unclear":
        add(w["raises_unclear"], "Capital-raiser, CTA fit unproven")
    if ib_type == "capital_raiser":
        add(w["capital_raiser"], "Classified as a capital-raising IB")
    if mf == "high":
        add(w["mf_high"], "Strong managed-futures relevance")
    elif mf == "medium":
        add(w["mf_medium"], "Some managed-futures relevance")
    if re.search(r"HNW|institutional|family office|wealth|investor", segment, re.I):
        add(w["investor_access"], f"Serves {segment}")
    if hedging_only == "yes":
        add(w["hedging_only"], "Hedging-only — exclude")
        force_c = True
    if ib_type == "execution_only":
        add(w["execution_only"], "Execution-only — exclude")
        force_c = True

    if _registered(firm.get("cta")):
        add(w["cta"], "CTA registered")
    elif is_pending(firm.get("cta")):
        reasons.append("CTA registration pending (+0)")
    if _registered(firm.get("cpo")):
        add(w["cpo"], "CPO registered")
    elif is_pending(firm.get("cpo")):
        reasons.append("CPO registration pending (+0)")
    if _registered(firm.get("introducing_broker")):
        add(w["introducing_broker"], "Introducing broker")
    if _registered(firm.get("fcm")):
        add(w["fcm"], "Futures commission merchant")
    if any(_registered(firm.get(k)) for k in ("rfed", "forex_dealer", "forex_firm")):
        add(w["forex"], "Forex registration")
    if any(_registered(firm.get(k)) for k in ("swap_dealer", "msp", "swap_firm")):
        add(w["swap"], "Swap registration")

    nfa = (firm.get("nfa_member") or "").upper()
    if nfa == "APPROVED":
        add(w["nfa_approved"], "NFA member approved")
    elif "PENDING" in nfa:
        add(w["nfa_pending"], "NFA membership pending")

    ib = (firm.get("introducing_broker") or "").upper()
    if "INDEPENDENT" in ib and "PENDING" not in ib:
        add(w["independent"], "Independent (not guaranteed)")

    if (firm.get("country") or "").strip().upper() in targets:
        add(w["target_country"], f"Target market: {firm.get('country').title()}")
    if (firm.get("website") or "").strip():
        add(w["has_website"], "Website found")
    if (firm.get("contact_email") or "").strip():
        add(w["has_work_email"], "Work email found")
    if (firm.get("contact_name") or "").strip():
        add(w["has_contact_name"], "Named contact")
    if SENIOR_TITLE.search(firm.get("contact_title") or ""):
        add(w["senior_title"], "Senior contact")

    score = max(0, min(100, points))
    tier = "A" if score >= cut["A"] else "B" if score >= cut["B"] else "C"
    if force_c:  # hedging-only / execution-only are never A or B, whatever else they have
        tier = "C"
    return Scored(score=score, tier=tier, reasons=reasons)


def rescore_all(store, settings) -> int:
    """Recompute score, tier and reasons for every firm from its current columns.

    Also keeps LEAD QUALITY in step with the classification columns, so firms screened
    before that column existed are sorted into qualified / uncertain / irrelevant too."""
    from . import ib_classify
    n = 0
    for f in store.firms(limit=100000):
        scored = score_firm(f, settings.firm_weights, settings.firm_target_countries, settings.firm_tiers)
        update = {"lead_score": scored.score, "tier": scored.tier,
                  "score_reasons": "; ".join(scored.reasons)}
        ib_type = (f.get("ib_type") or "").strip().lower()
        if ib_type:  # only screened firms get a quality; unscreened stay blank
            raises = (f.get("raises_for_cta") or "unclear").strip().lower()
            hedging = (f.get("hedging_only") or "no").strip().lower()
            mf = (f.get("mf_relevance") or "low").strip().lower()
            reco = (f.get("recommendation") or "").strip() or ib_classify.recommend(
                ib_type, raises, hedging, mf)
            update["recommendation"] = reco
            update["lead_quality"] = ib_classify.lead_quality_for(ib_type, raises, hedging, reco)
        store.update_firm(f["firm_key"], **update)
        n += 1
    return n
