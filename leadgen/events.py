"""Engagement tracking: what happened after each email.

  * Brevo webhooks   -> delivered, open, click, bounce, unsubscribe, complaint
  * IMAP reply watch -> replies (matched to the sent email by its Message-ID, else by sender address)

Every event is stored. Some events also change the sequence:
  reply                -> sequence stops ("replied"), the team is alerted
  hard bounce          -> address suppressed, sequence stops
  unsubscribe/complaint-> address suppressed for good, sequence stops
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from .mailer import verify_unsubscribe, webhook_token
from .storage import Store, now_iso

BREVO_EVENTS = {
    "delivered": "delivered",
    "opened": "open", "unique_opened": "open", "proxy_open": "open",
    "click": "click",
    "hard_bounce": "bounce", "invalid_email": "bounce", "blocked": "bounce", "soft_bounce": "soft_bounce",
    "unsubscribed": "unsubscribe",
    "spam": "complaint", "complaint": "complaint",
}
SUPPRESSING = {"bounce", "unsubscribe", "complaint"}
STOPPING = {"reply", "bounce", "unsubscribe", "complaint"}


def normalise_provider_id(value: str) -> str:
    return (value or "").strip().strip("<>")


def parse_brevo(payload) -> list[dict]:
    items = payload if isinstance(payload, list) else [payload]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        etype = BREVO_EVENTS.get(str(item.get("event", "")).lower())
        if not etype:
            continue  # request, deferred, etc. are not needed
        out.append({
            "type": etype,
            "email": str(item.get("email", "")).lower(),
            "provider_id": normalise_provider_id(str(item.get("message-id", ""))),
            "occurred_at": str(item.get("date") or item.get("ts_event") or now_iso())[:19],
            "detail": str(item.get("link") or item.get("reason") or item.get("event") or ""),
        })
    return out


def valid_webhook(token: str, secret: str) -> bool:
    return not secret or token == webhook_token(secret)


def apply_event(store: Store, ev: dict) -> dict:
    """Store one event and apply its effect on the sequence. Returns what was done."""
    message = store.message_by_provider_id(ev["provider_id"]) if ev.get("provider_id") else None
    contact_id = message["contact_id"] if message else None
    if contact_id is None and ev.get("email"):
        contact = store.find_contact(email=ev["email"])
        contact_id = contact["id"] if contact else None
    if contact_id is None:
        return {"stored": False, "reason": "unknown contact"}
    message_id = message["id"] if message else None
    store.add_event(contact_id, message_id, ev["type"], ev["occurred_at"], "brevo", ev.get("detail", ""))
    contact = store.get_contact(contact_id)
    address = (contact or {}).get("email") or ev.get("email") or ""

    effect = "recorded"
    if ev["type"] in SUPPRESSING:
        if address:
            store.suppress(address, ev["type"])
        status = {"bounce": "bounced", "unsubscribe": "unsubscribed", "complaint": "do_not_contact"}[ev["type"]]
        store.update_contact(contact_id, status=status)
        store.stop_enrollments_for(contact_id, {"bounce": "bounced", "unsubscribe": "unsubscribed",
                                                "complaint": "suppressed"}[ev["type"]])
        effect = f"suppressed ({ev['type']})"
    return {"stored": True, "contact_id": contact_id, "effect": effect}


def record_reply(store: Store, contact_id: int, message_id: int | None, occurred_at: str, detail: str) -> None:
    store.add_event(contact_id, message_id, "reply", occurred_at, "imap", detail)
    store.update_contact(contact_id, status="replied")
    store.stop_enrollments_for(contact_id, "replied")


def _clean_headers(msg) -> tuple[list[str], str]:
    refs = []
    for h in ("In-Reply-To", "References"):
        refs += re.findall(r"<[^>]+>", msg.get(h, "") or "")
    return [normalise_provider_id(r) for r in refs], (msg.get("Auto-Submitted", "") or "").lower()


def poll_replies(store: Store, host: str, user: str, password: str, days: int = 7) -> dict:
    """Look at unread inbox mail from the last few days and record replies to our sequence emails."""
    if not (host and user and password):
        raise RuntimeError("IMAP_HOST / IMAP_USER / IMAP_PASSWORD missing in .env")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    counts = {"replies": 0, "checked": 0}
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select("INBOX", readonly=True)
        _, data = conn.search(None, f'(UNSEEN SINCE "{since}")')
        for num in (data[0].split() if data and data[0] else []):
            _, raw = conn.fetch(num, "(BODY.PEEK[HEADER])")
            if not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            counts["checked"] += 1
            refs, auto = _clean_headers(msg)
            if auto and auto != "no":
                continue  # out-of-office and other automatic replies are not interest
            sender = parseaddr(msg.get("From", ""))[1].lower()
            when = now_iso()
            matched = None
            for ref in refs:
                matched = store.message_by_provider_id(ref)
                if matched:
                    break
            contact = store.get_contact(matched["contact_id"]) if matched else store.find_contact(email=sender)
            if not contact:
                continue
            if store.events_for_contact(contact["id"]) and any(
                    e["type"] == "reply" for e in store.events_for_contact(contact["id"])):
                continue  # already recorded
            record_reply(store, contact["id"], matched["id"] if matched else None, when,
                         (msg.get("Subject", "") or "")[:200])
            counts["replies"] += 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return counts
