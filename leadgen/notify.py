"""Pick the top-N leads and deliver the list to the recipient — by email or by SMS.

The recipient and channel are chosen by the user (dashboard or config). Nothing is
sent unless delivery.enabled is true; otherwise the files are written and you can
review them first.
"""

from __future__ import annotations

import re
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import requests

from .config import Settings
from .export import write_csv

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
SMS_CHUNK = 1500          # keep each text well inside Twilio's multi-segment limit
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class DeliveryError(RuntimeError):
    pass


def top_leads(rows: list[dict], n: int) -> list[dict]:
    """Highest fit score first; ties go to contacts that have an email."""
    ranked = sorted(rows, key=lambda r: (-(r.get("fit_score") or 0), not bool(r.get("email"))))
    return ranked[:n]


def _line(i: int, r: dict) -> str:
    who = r.get("full_name") or r.get("company") or r.get("domain") or "?"
    where = r.get("company") if r.get("full_name") else ""
    contact = r.get("email") or r.get("linkedin_url") or r.get("x_handle") or ""
    head = f"{i}. {who}" + (f" ({where})" if where else "")
    return f"{head} — {r.get('tier') or '-'} {r.get('fit_score') or 0} — {contact}"


def build_text(top: list[dict], n: int) -> str:
    lines = [f"Top {len(top)} leads (of {n} requested), ranked by fit:"]
    lines += [_line(i, r) for i, r in enumerate(top, start=1)]
    return "\n".join(lines)


def build_email(top: list[dict], sender: str, recipient: str, csv_path: Path, subject_prefix: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"{subject_prefix} — top {len(top)} leads ({date.today():%d %b %Y})"
    msg["From"] = sender or "lead-bot@localhost"
    msg["To"] = recipient or "(recipient not set)"
    body = ("Hi,\n\nThis week's top contacts for review:\n\n" + build_text(top, len(top))
            + "\n\nThe full details are attached as CSV.\n")
    msg.set_content(body)
    msg.add_attachment(csv_path.read_bytes(), maintype="text", subtype="csv", filename=csv_path.name)
    return msg


def sms_chunks(text: str, size: int = SMS_CHUNK) -> list[str]:
    """Split text into SMS bodies on line boundaries."""
    chunks, current = [], ""
    for line in text.splitlines():
        if len(current) + len(line) + 1 > size and current:
            chunks.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def send_sms(to: str, text: str, settings: Settings, session: requests.Session | None = None) -> int:
    if not E164.match(to):
        raise DeliveryError("SMS recipient must be a phone number in E.164 format, e.g. +447700900123")
    if not (settings.twilio_sid and settings.twilio_token and settings.twilio_from):
        raise DeliveryError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER missing in .env")
    http = session or requests.Session()
    chunks = sms_chunks(text)
    for body in chunks:
        resp = http.post(
            TWILIO_URL.format(sid=settings.twilio_sid),
            data={"To": to, "From": settings.twilio_from, "Body": body},
            auth=(settings.twilio_sid, settings.twilio_token),
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise DeliveryError(f"Twilio HTTP {resp.status_code}: {resp.text[:200]}")
    return len(chunks)


def send_email_msg(msg: EmailMessage, settings: Settings) -> None:
    if not (settings.smtp_user and settings.smtp_password):
        raise DeliveryError("SMTP_USER / SMTP_PASSWORD missing in .env")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def deliver_top_leads(rows: list[dict], settings: Settings, out_dir: Path,
                      channel: str, recipient: str, subject_prefix: str) -> dict:
    top = top_leads(rows, settings.top_n)
    stamp = date.today().isoformat()
    csv_path = write_csv(top, out_dir / f"top_{settings.top_n}_contacts_{stamp}.csv")
    result = {"count": len(top), "channel": channel, "recipient": recipient,
              "csv": csv_path.name, "sent": False, "note": ""}

    if channel == "sms":
        (out_dir / f"top_{settings.top_n}_sms_{stamp}.txt").write_text(build_text(top, len(top)), encoding="utf-8")
    else:
        msg = build_email(top, settings.sender or settings.smtp_user, recipient, csv_path, subject_prefix)
        eml_path = out_dir / f"top_{settings.top_n}_email_{stamp}.eml"
        eml_path.write_bytes(bytes(msg))
        result["eml"] = eml_path.name

    if not settings.delivery_enabled:
        result["note"] = "Sending is off (delivery.enabled: false) — files written only."
        return result
    if not recipient:
        result["note"] = "Sending is on but no recipient is set."
        return result

    if channel == "sms":
        n = send_sms(recipient, build_text(top, len(top)), settings)
        result["sent"] = True
        result["note"] = f"Sent {n} SMS to {recipient}."
    else:
        send_email_msg(msg, settings)
        result["sent"] = True
        result["note"] = f"Emailed to {recipient}."
    return result
