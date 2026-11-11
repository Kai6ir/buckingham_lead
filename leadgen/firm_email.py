"""Part 2b — write a tailored email for a firm, from the attributes in its columns.

The email may only use facts that are in the firm's row (registrations, location, NFA status,
guarantor, contact name). The LLM phrases them; when no LLM is configured a fixed template with the
same facts is used. The footer (sender, address, unsubscribe link) is always added in code.
"""

from __future__ import annotations

from .composer import contact_context, footer
from .firms import location, registrations
from .llm import LLM, LLMError, complete_json

MAX_BODY_WORDS = 130


def facts(firm: dict) -> list[str]:
    """Plain statements taken from the firm's own row. These are the only claims allowed about them."""
    out = []
    if (firm.get("raises_for_cta") or "").lower() == "yes":
        out.append("Raises capital / introduces investors to CTAs or managed-futures programmes")
    elif (firm.get("ib_type") or "").lower() == "capital_raiser":
        out.append("A capital-raising introducing broker")
    if (firm.get("client_segment") or "").strip() and firm["client_segment"] != "Unknown":
        out.append(f"Serves {firm['client_segment']} investors")
    regs = registrations(firm)
    if regs:
        out.append("Registered with the NFA as: " + ", ".join(regs))
    if (firm.get("guarantor_name") or "").strip():
        out.append(f"Clears through / is guaranteed by {firm['guarantor_name'].title()}")
    if location(firm):
        out.append(f"Based in {location(firm)}")
    return out


def angle(firm: dict) -> str:
    """How we'd frame the partnership, based on what the IB does."""
    if (firm.get("raises_for_cta") or "").lower() == "yes" or (firm.get("ib_type") or "").lower() == "capital_raiser":
        return "adding our managed-futures program to the CTAs you raise capital for"
    return "introducing our managed-futures program to your clients"


def _greeting(contact: dict | None) -> str:
    first = ((contact or {}).get("first_name") or "").strip()
    return f"Hi {first}," if first else "Hello,"


def fallback_draft(firm: dict, settings, contact: dict | None) -> dict:
    name = firm.get("firm_name", "").title() or "your firm"
    company = settings.company_name or "our firm"
    subject = f"Partnership — managed futures for {name}'s clients"
    body = (
        f"{_greeting(contact)}\n\n"
        f"I'm with {company}, a commodity trading advisor running a managed-futures program. "
        f"I came across {name} and thought there may be a fit on {angle(firm)}.\n\n"
        "If your clients look at managed-futures or alternative allocations, I'd welcome a short "
        "introductory call to see whether a partnership makes sense.\n\n"
        "Would a 20-minute call in the next few weeks suit you?\n\n"
        f"Best regards,\n{settings.sender_name or company}"
    )
    return {"subject": subject, "body": body}


def draft(firm: dict, settings, contact: dict | None, llm: LLM | None, unsub_url: str) -> dict:
    base = fallback_draft(firm, settings, contact)
    subject, body = base["subject"], base["body"]
    if llm is not None:
        system = (
            "You write short, professional partnership-outreach emails from a CTA (managed-futures "
            "manager) to an introducing broker that raises capital for managers. The aim is an "
            "introductory call about distributing our program to their clients. Use ONLY the facts "
            "provided. Do NOT state or imply any performance figures, win rates, returns, rankings or "
            "guarantees — none are provided and they must never be invented. Return JSON."
        )
        user = (
            f"We are: {settings.company_name}, a commodity trading advisor. Our offering: {settings.offering}\n"
            f"Recipient (an introducing broker): {(contact or {}).get('full_name') or 'unknown'}, "
            f"{(contact or {}).get('title') or 'title unknown'} at {firm.get('firm_name', '').title()}\n"
            f"Facts about the IB (the only facts you may use about them):\n- " + "\n- ".join(facts(firm)) + "\n"
            f"Framing: a partnership — {angle(firm)}.\n\n"
            f'Return {{"subject": "...", "body": "..."}}. Start the body with: {_greeting(contact)} '
            f"Under {MAX_BODY_WORDS} words, one clear call to action (a short call), "
            f"sign off as {settings.sender_name or settings.company_name}. "
            "No performance claims, no unsubscribe text, no emojis."
        )
        try:
            out = complete_json(llm, system, user, max_tokens=800)
            if len(str(out.get("body", "")).split()) >= 25:
                subject = str(out.get("subject") or subject).strip()[:200]
                body = str(out["body"]).strip()
        except LLMError:
            pass
    ctx = contact_context(contact or {"company": firm.get("firm_name", "").title()}, settings)
    return {"subject": subject, "body": body.rstrip() + "\n" + footer(settings, unsub_url, ctx)}
