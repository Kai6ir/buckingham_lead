"""Sending: compliance checks, unsubscribe links, and the two providers (Brevo API or SMTP).

Nothing leaves this module unless sending is enabled in config AND the message is approved
AND the contact passes every compliance check below.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from zoneinfo import ZoneInfo

import requests

from .config import Settings

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

REGION_TZ = {
    "americas": "America/New_York",
    "europe": "Europe/London",
    "asia-pacific": "Asia/Hong_Kong",
    "middle east": "Asia/Dubai",
    "africa": "Africa/Johannesburg",
}


class SendError(RuntimeError):
    pass


# ----- unsubscribe tokens -------------------------------------------------
def unsubscribe_token(contact_id: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"unsub:{contact_id}".encode(), hashlib.sha256).hexdigest()[:40]


def verify_unsubscribe(contact_id: int, token: str, secret: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(contact_id, secret), token or "")


def unsubscribe_url(settings: Settings, contact_id: int, secret: str) -> str:
    return f"{settings.public_base_url}/unsubscribe/{contact_id}/{unsubscribe_token(contact_id, secret)}"


def webhook_token(secret: str) -> str:
    return hmac.new(secret.encode(), b"brevo-webhook", hashlib.sha256).hexdigest()[:40]


def new_secret() -> str:
    return secrets.token_urlsafe(32)


# ----- compliance -------------------------------------------------------
def local_now(region: str, now_utc: datetime | None = None) -> datetime:
    tz = ZoneInfo(REGION_TZ.get((region or "").lower(), "UTC"))
    return (now_utc or datetime.now(ZoneInfo("UTC"))).astimezone(tz)


def within_sending_window(settings: Settings, region: str, now_utc: datetime | None = None) -> bool:
    start, end = settings.sending_window
    return start <= local_now(region, now_utc).hour < end


def compliance_block(settings: Settings, store, contact: dict) -> str:
    """Return a reason the contact must NOT be emailed right now, or '' if it may be."""
    email = (contact.get("email") or "").lower()
    if not email or "@" not in email:
        return "no email address"
    if contact.get("status") in ("do_not_contact", "bounced", "unsubscribed"):
        return f"contact status is {contact.get('status')}"
    if store.is_suppressed(email):
        return "on the suppression list (unsubscribed, complained or bounced)"
    region = (contact.get("region") or "").lower()
    if region in settings.require_lawful_basis_regions and not (contact.get("lawful_basis") or "").strip():
        return f"{region} contact has no lawful basis recorded — add one in the database first"
    return ""


# ----- providers ----------------------------------------------------------
def send_brevo(settings: Settings, to_email: str, to_name: str, subject: str, body: str,
               unsub_url: str, session: requests.Session | None = None) -> str:
    if not settings.brevo_key:
        raise SendError("BREVO_API_KEY is not set in .env")
    http = session or requests.Session()
    payload = {
        "sender": {"name": settings.sender_name or settings.company_name, "email": settings.sender or settings.smtp_user},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "textContent": body,
        "headers": {
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        "tags": ["sequence"],
    }
    resp = http.post(BREVO_URL, json=payload, headers={"api-key": settings.brevo_key,
                                                       "accept": "application/json"}, timeout=30)
    if resp.status_code not in (200, 201, 202):
        raise SendError(f"Brevo HTTP {resp.status_code}: {resp.text[:200]}")
    return str(resp.json().get("messageId", "")).strip("<>")


def send_smtp(settings: Settings, to_email: str, to_name: str, subject: str, body: str,
              unsub_url: str) -> str:
    if not (settings.smtp_user and settings.smtp_password):
        raise SendError("SMTP_USER / SMTP_PASSWORD missing in .env")
    sender = settings.sender or settings.smtp_user
    msg = EmailMessage()
    msgid = make_msgid(domain=sender.split("@")[-1])
    msg["Message-ID"] = msgid
    msg["Subject"] = subject
    msg["From"] = f"{settings.sender_name or settings.company_name} <{sender}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["List-Unsubscribe"] = f"<{unsub_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
    return msgid.strip("<>")


def send(settings: Settings, to_email: str, to_name: str, subject: str, body: str, unsub_url: str) -> tuple[str, str]:
    """Send with the configured provider. Returns (provider_name, provider_message_id)."""
    if settings.provider == "smtp":
        return "smtp", send_smtp(settings, to_email, to_name, subject, body, unsub_url)
    return "brevo", send_brevo(settings, to_email, to_name, subject, body, unsub_url)
