"""Coordinates everything the dashboard and CLI do:

  * discovery runs (Google / LinkedIn / X) in a background thread, with pause / stop
  * the contact database: import, search, enrol into sequences
  * the outbox: approve, edit, reject, send
  * engagement: Brevo webhooks, reply polling, unsubscribes
  * an optional autopilot that drafts, sends and polls on a timer
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Thread

from . import events, mailer
from .config import Settings
from .demo import DemoFetcher, DemoLLM, DemoSearch
from .engine import SequenceEngine
from .enrich import SiteFetcher
from .importer import import_file
from .llm import LLM, LLMError, build_llm
from .firm_pipeline import FirmPipeline
from .firms import ALL_COLUMNS, parse_master, firm_key, write_master
from .pipeline import STAGES, Pipeline, RunControl, Services
from .search import SerpApiSearch
from .storage import Store, now_iso

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def build_services(settings: Settings, mode: str) -> tuple[Services, list[str]]:
    warnings: list[str] = []
    if mode == "demo":
        return Services(search=DemoSearch(), fetcher=DemoFetcher(), llm=DemoLLM(), mode="demo"), warnings
    search = SerpApiSearch(settings.serpapi_key, gl=settings.gl, hl=settings.hl)  # raises if no key
    fetcher = SiteFetcher(timeout=settings.request_timeout, delay_range=settings.delay_range,
                          respect_robots=settings.respect_robots,
                          contact_paths=settings.contact_paths,
                          max_contact_pages=settings.max_contact_pages)
    llm = build_llm_for(settings, warnings)
    return Services(search=search, fetcher=fetcher, llm=llm, mode="live"), warnings


def build_llm_for(settings: Settings, warnings: list[str] | None = None) -> LLM | None:
    if not settings.use_llm:
        if warnings is not None:
            warnings.append("LLM disabled in config — keyword scoring, queries.txt, plain templates.")
        return None
    try:
        return build_llm(settings.llm_provider, settings.llm_model, settings.llm_base_url, settings.llm_key)
    except LLMError as exc:
        if warnings is not None:
            warnings.append(f"{exc} — continuing without an LLM.")
        return None


class BotController:
    def __init__(self, settings: Settings):
        self.base = settings
        self._lock = Lock()
        self._stores: dict[str, Store] = {}
        self._engines: dict[str, SequenceEngine] = {}
        self._thread: Thread | None = None
        self._autopilot: Thread | None = None
        self.ctl: RunControl | None = None
        self.mode = "live"
        self.last_message = ""
        self.store_for("live").mark_interrupted_runs(now_iso())

    # ----- plumbing -------------------------------------------------------------
    def settings_for(self, mode: str) -> Settings:
        if mode == "demo":
            return dataclasses.replace(self.base, db_path=self.base.db_path.parent / "demo.db")
        return self.base

    def store_for(self, mode: str) -> Store:
        key = "demo" if mode == "demo" else "live"
        if key not in self._stores:
            self._stores[key] = Store(self.settings_for(key).db_path)
        return self._stores[key]

    def engine_for(self, mode: str = "live") -> SequenceEngine:
        key = "demo" if mode == "demo" else "live"
        if key not in self._engines:
            store = self.store_for(key)
            settings = self.settings_for(key)
            llm = DemoLLM() if key == "demo" else build_llm_for(settings)
            engine = SequenceEngine(settings, store, llm=llm, log=self._engine_log)
            engine.sync_sequences()
            self._engines[key] = engine
        return self._engines[key]

    def _engine_log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {level.upper():5} {message}", flush=True)
        self.store_for("live").add_log(None, ts, level, message)

    # ----- delivery settings (weekly list) ---------------------------------------
    def delivery(self) -> dict:
        store = self.store_for("live")
        return {
            "channel": store.get_setting("delivery_channel", self.base.delivery_channel),
            "recipient": store.get_setting("delivery_recipient", self.base.delivery_recipient),
            "enabled": self.base.delivery_enabled,
        }

    def set_delivery(self, channel: str, recipient: str) -> tuple[bool, str]:
        channel = (channel or "").strip().lower()
        recipient = (recipient or "").strip()
        if channel not in ("email", "sms"):
            return False, "Channel must be email or sms."
        if channel == "email" and recipient and not EMAIL_RE.match(recipient):
            return False, "That email address does not look valid."
        if channel == "sms" and recipient and not E164_RE.match(recipient):
            return False, "Phone numbers must be in international format, e.g. +447700900123."
        store = self.store_for("live")
        store.set_setting("delivery_channel", channel)
        store.set_setting("delivery_recipient", recipient)
        return True, "Delivery settings saved."

    # ----- discovery runs ---------------------------------------------------------
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str = "live", kind: str = "contacts") -> tuple[bool, str]:
        """kind='contacts': discovery into the contact database. kind='firms': the NFA firm workflow."""
        with self._lock:
            if self.is_running():
                return False, "A run is already in progress."
            settings = self.settings_for(mode)
            try:
                services, warnings = build_services(settings, mode)
            except Exception as exc:
                return False, str(exc)
            delivery = self.delivery()
            store = self.store_for(mode)
            self.mode = mode
            engine = self.engine_for(mode)
            if kind == "firms":
                self.ctl = RunControl(store, mode, settings.firm_target)
                runner = FirmPipeline(settings, services, self.ctl,
                                      target_firms=settings.firm_target, engine=engine)
                name = "leadgen-firms"
            else:
                self.ctl = RunControl(store, mode, settings.target_leads)
                runner = Pipeline(settings, services, self.ctl,
                                  delivery_channel=delivery["channel"],
                                  delivery_recipient=delivery["recipient"], engine=engine)
                name = "leadgen-discovery"
            self._thread = Thread(target=runner.run, name=name, daemon=True)
            self._thread.start()
            self.last_message = " ".join(warnings)
            return True, self.last_message or "Run started."

    # ----- NFA firm list -----------------------------------------------------------
    def import_master(self, path: Path) -> tuple[bool, str, dict]:
        """Load the boss's master list. Firms already present are left as they are."""
        try:
            rows = parse_master(path)
        except Exception as exc:
            return False, f"Could not read the master list: {exc}", {}
        store = self.store_for("live")
        existing = store.firm_keys()
        new = 0
        for r in rows:
            key = firm_key(r.get("firm_id", ""), r.get("firm_name", ""), r.get("city", ""))
            if key in existing:
                continue
            store.upsert_firm({**r, "firm_key": key, "date_added": "imported"})
            existing.add(key)
            new += 1
        from .firm_scoring import rescore_all
        rescore_all(store, self.base)
        result = {"rows": len(rows), "new": new, "already": len(rows) - new}
        msg = f"Loaded {new} firms; {len(rows) - new} were already on the list."
        self._engine_log(f"Imported master list {path.name}: {msg}")
        return True, msg, result

    def firms(self, tier: str = "", outreach: str = "", ib_type: str = "",
              recommendation: str = "", decision: str = "", lead_quality: str = "",
              mode: str = "live") -> list[dict]:
        return self.store_for(mode).firms(tier=tier, outreach=outreach, ib_type=ib_type,
                                          recommendation=recommendation, decision=decision,
                                          lead_quality=lead_quality)

    def set_firm_decision(self, firm_key: str, decision: str, mode: str = "live") -> tuple[bool, str]:
        ok = self.store_for(mode).set_firm_decision(firm_key, decision)
        if not ok:
            return False, "Could not update that firm."
        label = {"keep": "kept", "exclude": "excluded", "": "cleared"}.get(decision, decision)
        self._engine_log(f"Firm {firm_key} marked {label}")
        return True, f"Firm {label}."

    def export_master(self, mode: str = "live") -> Path:
        out = self.output_dir(mode)
        path = write_master(self.store_for(mode).firms(limit=100000), out / "NFA_Master_IB_List_latest.xlsx")
        return path

    def pause(self) -> tuple[bool, str]:
        if not self.is_running() or self.ctl is None:
            return False, "Nothing is running."
        self.ctl.pause_event.set()
        self.ctl.log("Pause requested")
        return True, "Pausing after the current lead."

    def resume(self) -> tuple[bool, str]:
        if not self.is_running() or self.ctl is None:
            return False, "Nothing is running."
        self.ctl.pause_event.clear()
        self.ctl.log("Resumed")
        return True, "Resumed."

    def stop(self) -> tuple[bool, str]:
        if not self.is_running() or self.ctl is None:
            return False, "Nothing is running."
        self.ctl.pause_event.clear()
        self.ctl.stop_event.set()
        with self.ctl.lock:
            self.ctl.status = "stopping"
        return True, "Stopping…"

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ----- contact database ---------------------------------------------------
    def import_contacts(self, path: Path, filename: str) -> tuple[bool, str, dict]:
        try:
            result = import_file(path, self.store_for("live"), source_detail=filename)
        except Exception as exc:
            return False, f"Import failed: {exc}", {}
        msg = (f"{result['new']} new, {result['duplicates']} already in database, "
               f"{result['invalid']} invalid, {result['suppressed']} suppressed.")
        self._engine_log(f"Imported {filename}: {msg}")
        return True, msg, result

    def contacts(self, **filters) -> list[dict]:
        return self.store_for("live").contacts(**filters)

    def enroll(self, contact_ids: list[int], sequence: str = "") -> tuple[bool, str]:
        engine = self.engine_for("live")
        done = 0
        for cid in contact_ids:
            try:
                if engine.enroll(int(cid), sequence or None):
                    done += 1
            except ValueError as exc:
                return False, str(exc)
        return True, f"Enrolled {done} of {len(contact_ids)} contacts (others had no email, or were suppressed)."

    def enroll_by_filter(self, region: str = "", source: str = "", sequence: str = "") -> tuple[bool, str]:
        store = self.store_for("live")
        rows = store.contacts(region=region, source=source, status="active", limit=100000)
        rows = [r for r in rows if r.get("email")]
        return self.enroll([r["id"] for r in rows], sequence)

    LAWFUL_BASES = (
        "existing relationship",
        "legitimate interest (B2B)",
        "consent",
        "contract",
    )

    def missing_basis(self, region: str = "") -> int:
        return self.store_for("live").count_missing_basis(region)

    def record_lawful_basis(self, basis: str, contact_ids: list[int] | None = None,
                            region: str = "", only_missing: bool = True) -> tuple[bool, str]:
        """Record the lawful basis for contacts, either the ones ticked or everyone in a region."""
        basis = (basis or "").strip()
        if basis not in self.LAWFUL_BASES:
            return False, "Choose one of: " + ", ".join(self.LAWFUL_BASES) + "."
        store = self.store_for("live")
        if contact_ids:
            ids = [int(i) for i in contact_ids]
        else:
            rows = store.contacts(region=region, limit=100000)
            ids = [r["id"] for r in rows if r.get("email")
                   and (not only_missing or not (r.get("lawful_basis") or "").strip())]
        if not ids:
            return False, "No matching contacts need a lawful basis."
        n = store.set_lawful_basis(ids, basis)
        self._engine_log(f"Recorded lawful basis '{basis}' for {n} contact(s)")
        return True, f"Recorded '{basis}' for {n} contact(s)."

    # ----- outbox -------------------------------------------------------------------
    def outbox(self, status: str = "", mode: str = "live") -> list[dict]:
        return self.store_for(mode).messages(status=status, limit=500)

    def approve(self, message_id: int, mode: str = "live") -> tuple[bool, str]:
        return self.engine_for(mode).approve(message_id)

    def approve_all_drafts(self, mode: str = "live") -> tuple[bool, str]:
        drafts = self.store_for(mode).messages(status="draft", limit=2000)
        n = sum(1 for m in drafts if self.engine_for(mode).approve(m["id"])[0])
        return True, f"Approved {n} draft(s)."

    def reject(self, message_id: int, mode: str = "live") -> tuple[bool, str]:
        return self.engine_for(mode).reject(message_id)

    def edit(self, message_id: int, subject: str, body: str, mode: str = "live") -> tuple[bool, str]:
        return self.engine_for(mode).edit(message_id, subject, body)

    def tick(self) -> dict:
        engine = self.engine_for("live")
        result = engine.tick()
        if result.get("note"):
            self._engine_log(result["note"], "warn")
        return result

    def poll_replies(self) -> tuple[bool, str]:
        s = self.base
        try:
            counts = events.poll_replies(self.store_for("live"), s.imap_host, s.imap_user, s.imap_password)
        except Exception as exc:
            return False, f"Reply check failed: {exc}"
        self._engine_log(f"Reply check: {counts['replies']} new repl(ies) in {counts['checked']} message(s)")
        return True, f"{counts['replies']} new repl(ies) found."

    # ----- engagement -------------------------------------------------------------------
    def webhook(self, payload, token: str) -> tuple[bool, dict]:
        if not events.valid_webhook(token, self.base.outreach_secret):
            return False, {"error": "bad token"}
        store = self.store_for("live")
        applied = [events.apply_event(store, ev) for ev in events.parse_brevo(payload)]
        return True, {"processed": len(applied)}

    def unsubscribe(self, contact_id: int, token: str) -> tuple[bool, str]:
        secret = self.ensure_outreach_secret()
        if not secret or not mailer.verify_unsubscribe(contact_id, token, secret):
            return False, "This unsubscribe link is not valid."
        store = self.store_for("live")
        contact = store.get_contact(contact_id)
        if contact is None:
            return False, "Contact not found."
        if contact.get("email"):
            store.suppress(contact["email"], "unsubscribed via link")
        store.update_contact(contact_id, status="unsubscribed")
        store.stop_enrollments_for(contact_id, "unsubscribed")
        store.add_event(contact_id, None, "unsubscribe", now_iso(), "link", "")
        return True, "You have been unsubscribed. Sorry for the interruption."

    def ensure_outreach_secret(self) -> str:
        store = self.store_for("live")
        secret = self.base.outreach_secret or store.get_setting("outreach_secret", "")
        if not secret:
            secret = mailer.new_secret()
            store.set_setting("outreach_secret", secret)
        return secret

    def webhook_url(self) -> str:
        secret = self.ensure_outreach_secret()
        return f"{self.base.public_base_url}/webhooks/brevo?token={mailer.webhook_token(secret)}"

    # ----- autopilot -------------------------------------------------------------------
    def autopilot_on(self) -> bool:
        return self.store_for("live").get_setting("autopilot", "off") == "on"

    def set_autopilot(self, on: bool) -> tuple[bool, str]:
        self.store_for("live").set_setting("autopilot", "on" if on else "off")
        if on:
            self._start_autopilot()
        return True, "Autopilot on: drafts, sends and reply checks run every 10 minutes." if on else "Autopilot off."

    def _start_autopilot(self) -> None:
        if self._autopilot is not None and self._autopilot.is_alive():
            return

        def loop():
            while self.autopilot_on():
                try:
                    self.tick()
                    if self.base.imap_host:
                        self.poll_replies()
                except Exception as exc:
                    self._engine_log(f"Autopilot error: {exc}", "error")
                for _ in range(600):
                    if not self.autopilot_on():
                        break
                    time.sleep(1)

        self._autopilot = threading.Thread(target=loop, name="autopilot", daemon=True)
        self._autopilot.start()

    def resume_autopilot_if_set(self) -> None:
        if self.autopilot_on():
            self._start_autopilot()

    # ----- reads ------------------------------------------------------------------------
    def snapshot(self) -> dict:
        store = self.store_for(self.mode)
        if self.ctl is not None:
            state = self.ctl.snapshot()
        else:
            state = {"status": "idle", "mode": self.mode, "run_id": None, "started_at": "",
                     "finished_at": "", "elapsed_seconds": None, "activity": "", "error": "",
                     "target": self.base.target_leads,
                     "stages": [{"key": k, "label": l, "status": "pending", "done": 0, "total": 0}
                                for k, l in STAGES],
                     "counters": {}, "recent": [], "plan": [], "delivery": {}}
        live = self.store_for("live")
        state["counts"] = store.contact_counts()
        state["funnel"] = live.funnel()
        state["runs"] = store.recent_runs(8)
        state["message"] = self.last_message
        state["delivery_settings"] = self.delivery()
        state["top_n"] = self.base.top_n
        state["company"] = self.base.company_name
        state["sequences"] = live.sequences()
        state["autopilot"] = self.autopilot_on()
        state["send_enabled"] = self.base.send_enabled
        state["approval_required"] = self.base.approval_required
        state["provider"] = self.base.provider
        state["daily_cap"] = self.base.daily_cap
        state["sent_last_24h"] = live.sent_since((datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds"))
        state["llm"] = {"provider": self.base.llm_provider, "model": self.base.llm_model,
                        "enabled": self.base.use_llm,
                        "has_key": bool(self.base.llm_key) or self.base.llm_provider == "openai_compatible"}
        state["sources"] = {"google": self.base.use_google, "linkedin": self.base.use_linkedin, "x": self.base.use_x}
        state["has_serpapi_key"] = bool(self.base.serpapi_key)
        state["brevo_configured"] = bool(self.base.brevo_key)
        state["imap_configured"] = bool(self.base.imap_host and self.base.imap_user and self.base.imap_password)
        state["sms_configured"] = bool(self.base.twilio_sid and self.base.twilio_token and self.base.twilio_from)
        state["email_configured"] = bool(self.base.smtp_user and self.base.smtp_password)
        state["delivery_enabled"] = self.base.delivery_enabled
        state["postal_address_set"] = bool(self.base.postal_address)
        state["generated_at"] = datetime.now().isoformat(timespec="seconds")
        return state

    def logs_since(self, after_id: int) -> list[dict]:
        return self.store_for("live" if self.mode == "live" else "demo").logs_since(after_id)

    def output_dir(self, mode: str | None = None) -> Path:
        m = mode or self.mode
        return self.base.output_dir if m == "live" else self.base.output_dir / "demo"
