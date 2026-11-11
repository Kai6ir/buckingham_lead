"""The sequence engine: email 1, 2, 3 on schedule, branching on what the contact did.

Per enrolment:
  * step 1 is drafted as soon as the contact is enrolled;
  * the next step is drafted only after the previous email was actually sent, and only once its
    delay has passed (sequence days are counted from the previous send);
  * the wording of step 2 and 3 depends on the previous email: "engaged" if it was opened or
    clicked, "silent" if not;
  * a reply, bounce, unsubscribe or complaint stops the sequence at once (see events.py).

Drafts go to the Outbox. With approval_required on, a person approves each email before it can
be sent. Sending itself happens only in send_due(), and only when send_enabled is true.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import mailer
from .composer import compose
from .llm import LLM
from .mailer import SendError, compliance_block, within_sending_window
from .storage import Store, now_iso


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class SequenceEngine:
    def __init__(self, settings, store: Store, llm: LLM | None = None, log=None):
        self.s = settings
        self.store = store
        self.llm = llm
        self.log = log or (lambda msg, level="info": None)

    # ----- setup ------------------------------------------------------------
    def sync_sequences(self) -> None:
        for seq in self.s.sequences:
            self.store.upsert_sequence(seq["name"], seq["steps"], active=True)

    def enroll(self, contact_id: int, sequence_name: str | None = None, now: str | None = None) -> int:
        name = sequence_name or self.s.default_sequence
        seq = next((x for x in self.store.sequences() if x["name"] == name), None)
        if seq is None:
            raise ValueError(f"unknown sequence '{name}'")
        contact = self.store.get_contact(contact_id)
        if contact is None:
            raise ValueError("contact not found")
        if compliance_block(self.s, self.store, contact):
            return 0  # no email, suppressed, or unsubscribed: nothing to enrol
        return self.store.enroll(contact_id, seq["id"], now or now_iso())

    # ----- drafting -----------------------------------------------------------
    def _variant(self, enrollment: dict) -> str:
        last = self.store.last_sent_message(enrollment["id"])
        if last is None:
            return "default"
        if self.store.has_event(last["id"], "open") or self.store.has_event(last["id"], "click"):
            return "engaged"
        return "silent"

    def create_drafts(self, now: str | None = None, limit: int = 200) -> int:
        now = now or now_iso()
        created = 0
        for e in self.store.due_enrollments(now, limit=limit):
            contact = self.store.get_contact(e["contact_id"])
            steps = json.loads(e["steps"])
            if contact is None:
                self.store.update_enrollment(e["id"], status="paused", next_action_at=None)
                continue
            if self.store.is_suppressed(contact.get("email") or ""):
                self.store.update_enrollment(e["id"], status="suppressed", next_action_at=None)
                continue
            if e["step"] >= len(steps):
                self.store.update_enrollment(e["id"], status="completed", next_action_at=None)
                continue
            if self.store.pending_for_enrollment(e["id"]):
                continue  # previous email still waiting for approval or sending
            idx = e["step"]
            variant = "default" if idx == 0 else self._variant(e)
            unsub = mailer.unsubscribe_url(self.s, contact["id"], self._secret())
            composed = compose(self.s, contact, steps[idx]["template"], variant, unsub, self.llm,
                               step_note=f"email {idx + 1} of {len(steps)} in the sequence")
            approved = not self.s.approval_required
            message_id = self.store.create_message({
                "enrollment_id": e["id"], "contact_id": contact["id"], "step": idx + 1,
                "variant": variant, "to_email": contact["email"],
                "subject": composed["subject"], "body": composed["body"],
                "status": "approved" if approved else "draft", "scheduled_for": now,
            })
            if approved:
                self.store.update_message(message_id, approved_at=now)
            self.store.update_enrollment(e["id"], step=idx + 1, next_action_at=None)
            created += 1
        if created:
            self.log(f"Drafted {created} sequence email(s) for approval")
        return created

    def _secret(self) -> str:
        """One signing secret for unsubscribe links: from .env, else generated once and stored."""
        if self.s.outreach_secret:
            return self.s.outreach_secret
        secret = self.store.get_setting("outreach_secret", "")
        if not secret:
            secret = mailer.new_secret()
            self.store.set_setting("outreach_secret", secret)
        return secret

    # ----- approval -------------------------------------------------------------
    def approve(self, message_id: int) -> tuple[bool, str]:
        m = self.store.get_message(message_id)
        if not m or m["status"] != "draft":
            return False, "Only drafts can be approved."
        self.store.update_message(message_id, status="approved", approved_at=now_iso())
        return True, "Approved — it will go out in the next send window."

    def reject(self, message_id: int) -> tuple[bool, str]:
        m = self.store.get_message(message_id)
        if not m or m["status"] not in ("draft", "approved"):
            return False, "Only drafts or approved messages can be rejected."
        self.store.update_message(message_id, status="rejected")
        if m["enrollment_id"]:
            self.store.update_enrollment(m["enrollment_id"], status="paused", next_action_at=None)
        return True, "Rejected. The sequence for this contact is paused."

    def edit(self, message_id: int, subject: str, body: str) -> tuple[bool, str]:
        m = self.store.get_message(message_id)
        if not m or m["status"] != "draft":
            return False, "Only drafts can be edited."
        if not subject.strip() or not body.strip():
            return False, "Subject and body cannot be empty."
        self.store.update_message(message_id, subject=subject.strip()[:200], body=body.strip())
        return True, "Draft saved."

    # ----- sending --------------------------------------------------------------
    def send_due(self, now: datetime | None = None) -> dict:
        now = now or datetime.now()  # naive local time, same format as stored timestamps
        result = {"sent": 0, "skipped": 0, "failed": 0, "note": ""}
        if not self.s.send_enabled:
            result["note"] = "Sending is off (outreach.send_enabled: false)."
            return result
        last_day = (now - timedelta(hours=24)).isoformat(timespec="seconds")  # local, as stored
        cap = self.s.daily_cap - self.store.sent_since(last_day)
        for m in self.store.messages(status="approved", limit=500)[::-1]:  # oldest first
            if cap <= 0:
                result["note"] = f"Daily cap of {self.s.daily_cap} reached."
                break
            contact = self.store.get_contact(m["contact_id"])
            enrollment = self.store.get_enrollment(m["enrollment_id"]) if m["enrollment_id"] else None
            if enrollment and enrollment["status"] not in ("active", "completed"):
                self.store.update_message(m["id"], status="rejected", error=f"sequence {enrollment['status']}")
                result["skipped"] += 1
                continue
            reason = compliance_block(self.s, self.store, contact) if contact else "contact missing"
            if reason:
                self.store.update_message(m["id"], error=reason)
                result["skipped"] += 1
                continue
            if not within_sending_window(self.s, contact.get("region", ""), now):
                result["skipped"] += 1
                continue
            try:
                unsub = mailer.unsubscribe_url(self.s, contact["id"], self._secret())
                provider, pid = mailer.send(self.s, contact["email"], contact.get("full_name") or "",
                                            m["subject"], m["body"], unsub)
            except SendError as exc:
                self.store.update_message(m["id"], status="failed", error=str(exc)[:300])
                self.log(f"Send failed for {contact['email']}: {exc}", "error")
                result["failed"] += 1
                continue
            except Exception as exc:  # network errors etc.
                self.store.update_message(m["id"], status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
                self.log(f"Send failed for {contact['email']}: {exc}", "error")
                result["failed"] += 1
                continue
            sent_at = now.isoformat(timespec="seconds")
            self.store.update_message(m["id"], status="sent", provider=provider,
                                      provider_message_id=(pid or "").strip("<>"), sent_at=sent_at, error="")
            self.store.update_contact(contact["id"], last_contacted_at=sent_at)
            if enrollment:
                self._schedule_next(enrollment, m["step"], sent_at)
            cap -= 1
            result["sent"] += 1
        if result["sent"]:
            self.log(f"Sent {result['sent']} email(s)", "success")
        return result

    def _schedule_next(self, enrollment: dict, sent_step: int, sent_at: str) -> None:
        steps = json.loads(self.store.sequence_by_id(enrollment["sequence_id"])["steps"])
        if sent_step < len(steps):
            delta = steps[sent_step]["day"] - steps[sent_step - 1]["day"]
            nxt = (_parse(sent_at) + timedelta(days=max(delta, 0))).isoformat(timespec="seconds")
            self.store.update_enrollment(enrollment["id"], next_action_at=nxt, status="active")
        else:
            self.store.update_enrollment(enrollment["id"], status="completed", next_action_at=None)

    def tick(self) -> dict:
        drafted = self.create_drafts()
        sent = self.send_due()
        return {"drafted": drafted, **sent}
