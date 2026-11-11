"""Write each sequence email for one contact.

  1. Fill the template with the contact's details (name, company, role, segment).
  2. Optionally let the configured LLM personalise it for that person and region,
     keeping every fact from the template. The LLM never writes the footer.
  3. The footer is added in code: sender identity, postal address and unsubscribe link.
     Compliance text is never left to the model.
"""

from __future__ import annotations

import re

from .llm import LLM, LLMError, complete_json

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def contact_context(contact: dict, settings) -> dict:
    first = (contact.get("first_name") or (contact.get("full_name") or "").split(" ")[0] or "there").strip()
    return {
        "first_name": first,
        "full_name": contact.get("full_name") or first,
        "company": contact.get("company") or "your firm",
        "title": contact.get("title") or "",
        "region": contact.get("region") or "",
        "segment_hint": contact.get("segment") or "allocation strategy",
        "company_name": settings.company_name or "Buckingham",
        "sender_name": settings.sender_name or settings.company_name or "The team",
        "sender_role": settings.sender_role or "",
    }


def fill(text: str, ctx: dict) -> str:
    """Replace {placeholders} we know; leave unknown ones visible so a human notices them."""
    return PLACEHOLDER.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), text)


def footer(settings, unsubscribe_url: str, ctx: dict) -> str:
    who = ctx["company_name"] if ctx["sender_name"] == ctx["company_name"] else f"{ctx['sender_name']}, {ctx['company_name']}"
    lines = ["", "--", who]
    if settings.postal_address:
        lines.append(settings.postal_address)
    lines.append(f"Not interested? Unsubscribe: {unsubscribe_url}")
    return "\n".join(lines)


def personalise(llm: LLM | None, subject: str, body: str, ctx: dict, step_note: str) -> tuple[str, str]:
    """Let the model adapt the wording to this contact. Falls back to the filled template."""
    if llm is None:
        return subject, body
    system = ("You write short, professional B2B emails for a senior business development team. "
              "Keep every fact from the draft; do not add claims, figures or promises. Return JSON only.")
    user = (
        f"Recipient: {ctx['full_name']}, {ctx['title'] or 'role unknown'} at {ctx['company']} "
        f"(region: {ctx['region'] or 'unknown'}).\n"
        f"Email purpose: {step_note}\n"
        f"Draft subject: {subject}\nDraft body:\n{body}\n\n"
        'Return {"subject": "...", "body": "..."}. Keep the body under 120 words, keep the sign-off '
        f"with the sender's name ({ctx['sender_name']}), no unsubscribe text, no emojis."
    )
    try:
        out = complete_json(llm, system, user, max_tokens=800)
        new_subject = str(out.get("subject") or subject).strip()[:200]
        new_body = str(out.get("body") or body).strip()
        if len(new_body) < 40:
            return subject, body
        return new_subject, new_body
    except LLMError:
        return subject, body


def compose(settings, contact: dict, template_name: str, variant: str, unsubscribe_url: str,
            llm: LLM | None = None, step_note: str = "") -> dict:
    """Return {'subject', 'body', 'variant'} ready for approval."""
    tpl_group = settings.templates.get(template_name) or {}
    tpl = tpl_group.get(variant) or tpl_group.get("default")
    if not tpl:
        raise ValueError(f"template '{template_name}' has no '{variant}' or 'default' variant")
    ctx = contact_context(contact, settings)
    subject, body = fill(tpl["subject"], ctx), fill(tpl["body"], ctx).rstrip()
    subject, body = personalise(llm, subject, body, ctx, step_note or template_name)
    body = body + "\n" + footer(settings, unsubscribe_url, ctx)
    return {"subject": subject, "body": body, "variant": variant}
