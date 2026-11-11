"""Discovery: plan → search Google / LinkedIn / X → de-duplicate against the database →
check work contacts → qualify → add NEW contacts → enrol strong ones → export → weekly top-N.

RunControl is the thread-safe bridge between the worker thread and the dashboard.
"""

from __future__ import annotations

import re
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, RLock
from typing import Any

from .config import Settings
from .enrich import person_emails, work_emails
from .export import sync_google_sheet, write_csv, write_xlsx
from .importer import region_for
from .llm import LLM
from .models import Candidate, Lead, linkedin_key, parse_linkedin_title, parse_x_title
from .notify import deliver_top_leads
from .planner import plan_queries
from .qualify import LLMQualifier, QualifyError, combine_score, keyword_match_pct
from .search import SearchError, is_blocked, normalize_domain
from .storage import Store, now_iso

STAGES = [
    ("plan", "Plan searches"),
    ("search", "Search"),
    ("enrich", "Check & qualify"),
    ("export", "Export files"),
    ("deliver", "Weekly list / email drafts"),
]


class Cancelled(Exception):
    """Raised inside the worker when the user presses Stop."""


def load_queries(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


@dataclass
class Services:
    search: Any                 # .search(query, page, channel) -> list[SearchHit]
    fetcher: Any                # .fetch_site(domain) -> SiteData
    llm: LLM | None
    mode: str                   # "live" | "demo"


class RunControl:
    """Shared progress state. Everything here is guarded by one lock."""

    def __init__(self, store: Store, mode: str, target: int):
        self.store = store
        self.lock = RLock()
        self.stop_event = Event()
        self.pause_event = Event()
        self.run_id: int | None = None
        self.mode = mode
        self.status = "idle"  # idle | running | paused | stopping | completed | stopped | error
        self.started_at = ""
        self.finished_at = ""
        self.activity = ""
        self.error = ""
        self.target = target
        self.stages = {key: {"key": key, "label": label, "status": "pending", "done": 0, "total": 0}
                       for key, label in STAGES}
        self.counters = {"queries": 0, "search_pages": 0, "candidates": 0, "duplicates": 0,
                         "filtered": 0, "checked": 0, "added": 0, "rejected": 0, "enrolled": 0,
                         "with_email": 0, "errors": 0, "HOT": 0, "WARM": 0, "COLD": 0}
        self.recent: deque = deque(maxlen=40)
        self.plan: list[dict] = []
        self.delivery_result: dict = {}

    # ----- writers (called by the pipeline) -------------------------------
    def log(self, message: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {level.upper():5} {message}", flush=True)
        self.store.add_log(self.run_id, ts, level, message)

    def set_activity(self, text: str) -> None:
        with self.lock:
            self.activity = text

    def stage(self, key: str, **fields) -> None:
        with self.lock:
            self.stages[key].update(fields)

    def bump(self, name: str, n: int = 1) -> None:
        with self.lock:
            self.counters[name] = self.counters.get(name, 0) + n

    def add_recent(self, lead: Lead, contact_id: int) -> None:
        with self.lock:
            self.recent.appendleft({
                "contact_id": contact_id,
                "name": lead.person_name or lead.company_name or lead.domain,
                "company": lead.company_name, "role": lead.role,
                "link": lead.linkedin_url or lead.website or (f"https://x.com/{lead.x_handle}" if lead.x_handle else ""),
                "tier": lead.tier, "score": lead.score,
                "contact": lead.best_email or "no work email yet",
                "query": lead.source_query, "channel": lead.channel,
            })

    def checkpoint(self) -> None:
        """Block while paused; raise Cancelled if stop was requested."""
        if self.stop_event.is_set():
            raise Cancelled()
        if self.pause_event.is_set():
            with self.lock:
                self.status = "paused"
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.stop_event.wait(0.3)
            if self.stop_event.is_set():
                raise Cancelled()
            with self.lock:
                self.status = "running"

    # ----- readers (called by the dashboard) ------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            elapsed = None
            if self.started_at:
                end = datetime.fromisoformat(self.finished_at) if self.finished_at else datetime.now()
                elapsed = int((end - datetime.fromisoformat(self.started_at)).total_seconds())
            return {
                "status": self.status, "mode": self.mode, "run_id": self.run_id,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "elapsed_seconds": elapsed, "activity": self.activity, "error": self.error,
                "target": self.target,
                "stages": [dict(self.stages[k]) for k, _ in STAGES],
                "counters": dict(self.counters),
                "recent": list(self.recent),
                "plan": list(self.plan),
                "delivery": dict(self.delivery_result),
            }


class Pipeline:
    def __init__(self, settings: Settings, services: Services, ctl: RunControl,
                 delivery_channel: str, delivery_recipient: str, engine=None):
        self.s = settings
        self.svc = services
        self.ctl = ctl
        self.channel = delivery_channel
        self.recipient = delivery_recipient
        self.engine = engine
        self._company_domains: dict[str, str] = {}
        self._domain_lock = Lock()
        self.qualifier = LLMQualifier(services.llm, {
            "company_name": settings.company_name, "sender_name": settings.sender_name,
            "sender_role": settings.sender_role, "offering": settings.offering,
            "ideal_customer": settings.ideal_customer,
        }) if services.llm is not None else None

    @property
    def out_dir(self) -> Path:
        return self.s.output_dir if self.svc.mode == "live" else self.s.output_dir / "demo"

    # ----- entry point ----------------------------------------------------
    def run(self) -> None:
        ctl = self.ctl
        ctl.run_id = ctl.store.start_run(now_iso())
        with ctl.lock:
            ctl.status = "running"
            ctl.started_at = now_iso()
        ctl.log(f"Discovery run #{ctl.run_id} started ({self.svc.mode} mode, up to {self.s.target_leads} new contacts, "
                f"model {self.svc.llm.model if self.svc.llm else 'none — keyword scoring'})")
        status = "completed"
        try:
            channels = self._channels()
            plan = self._plan(channels)
            candidates = self._search(plan)
            added = self._enrich(candidates)
            self._export()
            self._deliver()
            if added < self.s.target_leads:
                ctl.log(f"Only {added} of {self.s.target_leads} new contacts found. "
                        "Add more keywords, raise run.pages_per_query, or enable another source.", "warn")
            ctl.log(f"Run finished — {added} new contacts added", "success")
        except Cancelled:
            status = "stopped"
            ctl.log("Run stopped by user", "warn")
        except Exception as exc:
            status = "error"
            with ctl.lock:
                ctl.error = f"{type(exc).__name__}: {exc}"
            ctl.log(f"Run failed: {ctl.error}", "error")
            ctl.log(traceback.format_exc(limit=3), "error")
        finally:
            with ctl.lock:
                ctl.status = status
                ctl.finished_at = now_iso()
                ctl.activity = ""
                stats = dict(ctl.counters)
            ctl.store.finish_run(ctl.run_id, ctl.finished_at, status, stats)

    def _channels(self) -> list[str]:
        out = []
        if self.s.use_google:
            out.append("google")
        if self.s.use_linkedin:
            out.append("linkedin")
        if self.s.use_x:
            out.append("x")
        if not out:
            raise RuntimeError("No discovery source enabled (sources in config.yaml)")
        return out

    # ----- stage 0: plan -----------------------------------------------------
    def _plan(self, channels: list[str]) -> list[tuple[str, str]]:
        ctl, s = self.ctl, self.s
        ctl.stage("plan", status="running", done=0, total=1)
        ctl.set_activity("Planning search queries")
        seeds = load_queries(s.queries_file)
        plan = plan_queries(self.svc.llm if s.use_llm else None, {
            "offering": s.offering, "ideal_customer": s.ideal_customer,
        }, seeds, s.planned_queries, channels)
        if not plan:
            raise RuntimeError("No search queries: add lines to queries.txt or configure an LLM")
        with ctl.lock:
            ctl.plan = [{"channel": c, "query": q} for c, q in plan]
        ctl.log(f"{len(plan)} searches across {', '.join(channels)}")
        ctl.stage("plan", status="done", done=1, total=1)
        ctl.set_activity("")
        return plan

    # ----- stage 1: search + de-duplicate -------------------------------------
    def _search(self, plan: list[tuple[str, str]]) -> list[Candidate]:
        ctl, s = self.ctl, self.s
        ctl.stage("search", status="running", done=0, total=len(plan))
        index = ctl.store.match_index()
        seen_keys = ctl.store.seen_keys()
        batch: set[str] = set()
        candidates: list[Candidate] = []
        pool = s.candidate_pool_size

        for qi, (channel, query) in enumerate(plan, start=1):
            ctl.checkpoint()
            ctl.set_activity(f"Searching {channel}: “{query}”")
            for page in range(s.pages_per_query):
                ctl.checkpoint()
                try:
                    hits = self.svc.search.search(query, page, channel)
                except SearchError as exc:
                    ctl.bump("errors")
                    ctl.log(f"{channel} search failed for “{query}” (page {page + 1}): {exc}", "error")
                    break
                ctl.bump("search_pages")
                if not hits:
                    break
                for hit in hits:
                    cand = self._candidate_from_hit(hit)
                    if cand is None:
                        ctl.bump("filtered")
                        continue
                    if self._is_known(cand, index, seen_keys, batch):
                        ctl.bump("duplicates")
                        continue
                    batch.add(cand.key)
                    candidates.append(cand)
                    ctl.bump("candidates")
                    if len(candidates) >= pool:
                        break
                if len(candidates) >= pool:
                    break
            ctl.bump("queries")
            ctl.stage("search", done=qi)
            ctl.log(f"{channel} “{query}” → {len(candidates)} new candidates so far")
            if len(candidates) >= pool:
                ctl.log(f"Candidate pool full ({pool}) — moving on to checks")
                ctl.stage("search", total=qi)
                break

        ctl.stage("search", status="done")
        ctl.set_activity("")
        ctl.log(f"Search complete: {len(candidates)} candidates not yet in the database")
        return candidates

    def _is_known(self, c: Candidate, index: dict, seen_keys: set, batch: set) -> bool:
        if c.key in batch or c.key in seen_keys:
            return True
        if c.kind == "company":
            return c.domain in index["domains"]
        if c.channel == "linkedin":
            if c.key in index["linkedin"]:
                return True
        if c.channel == "x" and c.x_handle in index["handles"]:
            return True
        return bool(c.person_name and (c.person_name.lower(), c.company_name.lower()) in index["names"])

    def _candidate_from_hit(self, hit) -> Candidate | None:
        if hit.channel == "linkedin":
            key = linkedin_key(hit.url)
            if not key:
                return None
            name, role, company = parse_linkedin_title(hit.title)
            if not name:
                return None
            return Candidate(key=key, kind="person", channel="linkedin", query=hit.query,
                             title=hit.title, snippet=hit.snippet, linkedin_url=hit.url,
                             person_name=name, role=role, company_name=company)
        if hit.channel == "x":
            name, handle = parse_x_title(hit.title)
            if not handle:
                return None
            return Candidate(key=f"x.com/{handle}", kind="person", channel="x", query=hit.query,
                             title=hit.title, snippet=hit.snippet, person_name=name,
                             x_handle=handle)
        domain = normalize_domain(hit.url)
        if not domain or is_blocked(domain, self.s.blocklist_domains):
            return None
        return Candidate(key=domain, kind="company", channel="google", query=hit.query,
                         title=hit.title, snippet=hit.snippet, website=f"https://{domain}", domain=domain)

    # ----- stage 2: check work contacts, qualify, store -------------------------
    def _company_domain(self, company: str) -> str:
        """Find a person's employer website via one Google search (cached per company)."""
        if not company:
            return ""
        with self._domain_lock:
            if company in self._company_domains:
                return self._company_domains[company]
        domain = ""
        try:
            for hit in self.svc.search.search(f"{company} official website", 0, "google"):
                candidate = normalize_domain(hit.url)
                if candidate and not is_blocked(candidate, self.s.blocklist_domains):
                    domain = candidate
                    break
        except SearchError as exc:
            self.ctl.bump("errors")
            self.ctl.log(f"Could not resolve website for {company}: {exc}", "warn")
        with self._domain_lock:
            self._company_domains[company] = domain
        return domain

    def _process(self, c: Candidate) -> Lead:
        domain = c.domain if c.kind == "company" else self._company_domain(c.company_name)
        site = self.svc.fetcher.fetch_site(domain) if domain else None
        contact = site.contact if site else None
        pages = site.pages_fetched if site else 0

        if c.kind == "company":
            emails = work_emails(contact.emails, domain) if contact else []
            phones = contact.phones if contact else []
        else:
            emails = person_emails(work_emails(contact.emails, domain), c.person_name) if contact else []
            phones = []
        contact_status = "work_contact_found" if emails else "no_work_contact"

        if pages and contact:
            basis = " ".join([contact.company_name, contact.description, contact.text])
        else:
            basis = " ".join([c.title, c.snippet])
        kw = keyword_match_pct(basis, c.query)

        company = (contact.company_name if contact else "") or c.company_name or c.title or domain
        company = re.split(r"\s[|\-–—]\s", company)[0][:120] if company else ""

        ai = None
        country = ""
        if self.qualifier is not None:
            try:
                ai = self.qualifier.assess({
                    "kind": c.kind, "channel": c.channel, "query": c.query,
                    "person_name": c.person_name, "role": c.role, "company_name": company,
                    "website": f"https://{domain}" if domain else "", "title": c.title,
                    "snippet": c.snippet, "description": contact.description if contact else "",
                    "text": contact.text if contact else "",
                }, need_message=False)
                company = ai["company_name"] or company
                country = ai["country"]
            except QualifyError as exc:
                self.ctl.bump("errors")
                self.ctl.log(f"LLM unavailable for {c.key}, using keyword score: {exc}", "warn")

        score, tier = combine_score(kw, bool(emails), ai)
        return Lead(
            key=c.key, kind=c.kind, channel=c.channel,
            website=f"https://{domain}" if domain else c.website, domain=domain,
            company_name=company, person_name=c.person_name, role=c.role,
            linkedin_url=c.linkedin_url, x_handle=c.x_handle, country=country,
            title=c.title, snippet=c.snippet, source_query=c.query,
            emails=emails, phones=phones, contact_status=contact_status,
            keyword_pct=kw, ai_score=ai["score"] if ai else None,
            ai_reason=ai["reason"] if ai else "", score=score, tier=tier,
            fetch_error=(site.error if site else "no company website found") if not pages else "",
        )

    def _store(self, lead: Lead, query: str) -> int:
        """Add a new contact (or skip if it turned out to exist). Returns the contact id or 0."""
        ctl = self.ctl
        key = lead.domain if lead.kind == "company" else (lead.linkedin_url and linkedin_key(lead.linkedin_url)) or f"x.com/{lead.x_handle}"
        if lead.best_email and ctl.store.find_contact(email=lead.best_email):
            ctl.bump("duplicates")
            ctl.store.mark_seen(key, "duplicate")
            return 0
        if lead.tier == "REJECT":
            ctl.bump("rejected")
            ctl.store.mark_seen(key, "rejected")
            return 0
        name_parts = (lead.person_name or "").split()
        contact_id = ctl.store.insert_contact({
            "email": lead.best_email or None,
            "first_name": name_parts[0] if name_parts else "",
            "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
            "full_name": lead.person_name or lead.company_name,
            "title": lead.role, "company": lead.company_name, "domain": lead.domain,
            "linkedin_url": lead.linkedin_url or None,
            "linkedin_key": (linkedin_key(lead.linkedin_url) or None) if lead.linkedin_url else None,
            "x_handle": lead.x_handle or None, "phone": lead.phones[0] if lead.phones else "",
            "country": lead.country, "region": region_for(lead.country),
            "source": "discovery", "source_detail": query, "channel": lead.channel,
            "fit_score": lead.score, "tier": lead.tier, "fit_reason": lead.ai_reason,
            "status": "active",
        })
        ctl.store.mark_seen(key, "added")
        ctl.bump("added")
        ctl.bump(lead.tier)
        if lead.best_email:
            ctl.bump("with_email")
            if (self.engine is not None and lead.tier in ("HOT", "WARM")
                    and lead.score >= self.s.auto_enroll_min_score):
                if self.engine.enroll(contact_id):
                    ctl.bump("enrolled")
        ctl.add_recent(lead, contact_id)
        return contact_id

    def _enrich(self, candidates: list[Candidate]) -> int:
        ctl, s = self.ctl, self.s
        total = len(candidates)
        ctl.stage("enrich", status="running", done=0, total=total)
        added = 0
        processed = 0
        idx = 0
        chunk_size = max(1, s.enrich_workers * 2)

        with ThreadPoolExecutor(max_workers=s.enrich_workers) as pool:
            while idx < total and added < s.target_leads:
                ctl.checkpoint()
                chunk = candidates[idx: idx + chunk_size]
                idx += len(chunk)
                futures = {pool.submit(self._process, c): c for c in chunk}
                for fut in as_completed(futures):
                    ctl.checkpoint()
                    c = futures[fut]
                    processed += 1
                    ctl.bump("checked")
                    ctl.set_activity(f"Checking {c.person_name or c.key}")
                    try:
                        lead = fut.result()
                    except Exception as exc:
                        ctl.bump("errors")
                        ctl.log(f"Skipped {c.key}: {exc}", "warn")
                        ctl.stage("enrich", done=processed)
                        continue
                    if self._store(lead, c.query):
                        added += 1
                    ctl.stage("enrich", done=processed)
                    if added >= s.target_leads:
                        for pending in futures:
                            pending.cancel()
                        break
                ctl.log(f"Progress: {added}/{s.target_leads} new contacts")

        ctl.stage("enrich", status="done", done=processed, total=total)
        ctl.set_activity("")
        return added

    # ----- stage 3: export ----------------------------------------------------
    def _export(self) -> None:
        ctl, s = self.ctl, self.s
        ctl.stage("export", status="running", done=0, total=3)
        ctl.set_activity("Writing Excel / CSV")
        rows = ctl.store.contacts(limit=100000)
        out = self.out_dir
        write_xlsx(rows, out / "contacts_latest.xlsx")
        ctl.stage("export", done=1)
        write_csv(rows, out / "contacts_latest.csv")
        ctl.stage("export", done=2)
        ctl.log(f"Exported {len(rows)} contacts to {out}")
        if s.sheets_enabled and self.svc.mode == "live":
            try:
                n = sync_google_sheet(rows, s.sheets_spreadsheet_id, s.sheets_worksheet, s.sheets_credentials)
                ctl.log(f"Mirrored {n} contacts to Google Sheets")
            except Exception as exc:
                ctl.bump("errors")
                ctl.log(f"Google Sheets sync failed: {exc}", "error")
        ctl.stage("export", status="done", done=3)
        ctl.set_activity("")

    # ----- stage 4: weekly top-N ------------------------------------------------
    def _deliver(self) -> None:
        ctl, s = self.ctl, self.s
        ctl.stage("deliver", status="running", done=0, total=1)
        ctl.set_activity("Preparing weekly list")
        scored = [r for r in ctl.store.contacts(limit=100000) if r.get("fit_score") is not None
                  and r.get("tier") != "REJECT"]
        try:
            result = deliver_top_leads(scored, s, self.out_dir, self.channel, self.recipient,
                                       subject_prefix=s.company_name or "Weekly contacts")
        except Exception as exc:
            ctl.bump("errors")
            ctl.log(f"Weekly top-{s.top_n} failed: {exc}", "error")
            result = {"error": str(exc), "channel": self.channel, "recipient": self.recipient}
        with ctl.lock:
            ctl.delivery_result = result
        ctl.log(f"Top {s.top_n}: {result.get('note', result.get('error', ''))}")
        ctl.stage("deliver", status="done", done=1)
        ctl.set_activity("")
