"""Run the two parts of the NFA-list bot.

  Part 1  plan → search Google and NFA's own pages → for each new firm: read its page, fill the
          master columns, check for a work email → score it (Part 2a) → add it as a new row.
  Part 2  export the master list in its original layout with the new rows → for the firms at or above
          the outreach tier with a work email, write a tailored email and queue it in the Outbox
          (Part 2b). Nothing is sent unless sending is enabled and the email is approved.
"""

from __future__ import annotations

import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from . import firm_email, firm_scoring, ib_classify
from .firm_scoring import rescore_all
from .config import Settings
from .enrich import person_emails, work_emails
from .firm_search import extract_fields, firm_queries
from .firms import clean_id, default_export_name, firm_key, write_master
from .importer import region_for
from .llm import LLM
from .pipeline import Cancelled, RunControl, Services
from .search import SearchError, is_blocked, normalize_domain
from .storage import Store, now_iso


class FirmPipeline:
    def __init__(self, settings: Settings, services: Services, ctl: RunControl,
                 target_firms: int = 50, engine=None):
        self.s = settings
        self.svc = services
        self.ctl = ctl
        self.target = target_firms
        self.engine = engine
        self.llm: LLM | None = services.llm

    @property
    def out_dir(self) -> Path:
        return self.s.output_dir if self.svc.mode == "live" else self.s.output_dir / "demo"

    # ----- entry point ---------------------------------------------------------
    def run(self) -> None:
        ctl = self.ctl
        ctl.run_id = ctl.store.start_run(now_iso())
        with ctl.lock:
            ctl.status = "running"
            ctl.started_at = now_iso()
        ctl.log(f"IB screening #{ctl.run_id} started — classify firms and find up to {self.target} new ones")
        status = "completed"
        try:
            master = ctl.store.firms(limit=100000)
            queries = self._plan(master)
            candidates = self._search(queries)
            added = self._enrich(candidates)
            self._screen_existing()
            rescore_all(ctl.store, self.s)
            self._export()
            self._queue_outreach()
            counts = ctl.store.firm_counts()
            ctl.log(f"Finished — {added} new firms added; "
                    f"{counts['capital_raiser']} capital-raisers, {counts['hedger']} hedgers, "
                    f"{counts['high_priority']} high-priority to review", "success")
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

    # ----- Part 1 -----------------------------------------------------------------
    def _plan(self, master: list[dict]) -> list[tuple[str, str]]:
        ctl, s = self.ctl, self.s
        ctl.stage("plan", status="running", done=0, total=1)
        queries = firm_queries(master, s.planned_queries, s.firm_target_countries)
        ctl.log(f"{len(queries)} searches built from the master list "
                f"(types and locations it is concentrated in, plus target markets)")
        with ctl.lock:
            ctl.plan = [{"channel": c, "query": q} for c, q in queries]
        ctl.stage("plan", status="done", done=1, total=1)
        return queries

    def _search(self, plan: list[tuple[str, str]]) -> list[dict]:
        ctl, s = self.ctl, self.s
        ctl.stage("search", status="running", done=0, total=len(plan))
        store = ctl.store
        known = store.firm_keys()
        known_domains = {normalize_domain(f["website"]) for f in store.firms(limit=100000) if f.get("website")}
        seen_domains: set[str] = set()
        seen_keys: set[str] = set()
        candidates: list[dict] = []
        pool = self.target * 2
        for qi, (channel, query) in enumerate(plan, start=1):
            ctl.checkpoint()
            ctl.set_activity(f"Searching {channel}: “{query}”")
            for page in range(s.pages_per_query):
                ctl.checkpoint()
                try:
                    hits = self.svc.search.search(query, page, channel)
                except SearchError as exc:
                    ctl.bump("errors")
                    ctl.log(f"Search failed for “{query}”: {exc}", "error")
                    break
                ctl.bump("search_pages")
                if not hits:
                    break
                for hit in hits:
                    domain = normalize_domain(hit.url)
                    if not domain or is_blocked(domain, s.blocklist_domains):
                        ctl.bump("filtered")
                        continue
                    nfa_id = clean_id(_nfa_id_in(hit.url + " " + hit.snippet + " " + hit.title))
                    if domain in known_domains or domain in seen_domains or (nfa_id and f"nfa:{nfa_id}" in known):
                        ctl.bump("duplicates")
                        continue
                    seen_domains.add(domain)
                    candidates.append({"domain": domain, "url": hit.url, "title": hit.title,
                                       "snippet": hit.snippet, "query": query, "nfa_id": nfa_id})
                    ctl.bump("candidates")
                    if len(candidates) >= pool:
                        break
                if len(candidates) >= pool:
                    break
            ctl.bump("queries")
            ctl.stage("search", done=qi)
            ctl.log(f"“{query}” → {len(candidates)} candidate firms so far")
            if len(candidates) >= pool:
                ctl.stage("search", total=qi)
                break
        ctl.stage("search", status="done")
        ctl.set_activity("")
        ctl.log(f"Search complete: {len(candidates)} firms not yet on the list")
        return candidates

    def _enrich(self, candidates: list[dict]) -> int:
        ctl, s = self.ctl, self.s
        total = len(candidates)
        ctl.stage("enrich", status="running", done=0, total=total)
        added, processed = 0, 0
        chunk = max(1, s.enrich_workers * 2)
        idx = 0
        with ThreadPoolExecutor(max_workers=s.enrich_workers) as pool:
            while idx < total and added < self.target:
                ctl.checkpoint()
                batch = candidates[idx: idx + chunk]
                idx += len(batch)
                futures = {pool.submit(self._build_row, c): c for c in batch}
                for fut in as_completed(futures):
                    ctl.checkpoint()
                    processed += 1
                    ctl.stage("enrich", done=processed)
                    ctl.set_activity(f"Reading {futures[fut]['domain']}")
                    try:
                        row = fut.result()
                    except Exception as exc:
                        ctl.bump("errors")
                        ctl.log(f"Skipped {futures[fut]['domain']}: {exc}", "warn")
                        continue
                    if row is None:
                        ctl.bump("rejected")
                        continue
                    if self._store_row(row):
                        added += 1
                        ctl.bump("added")
                        ctl.bump(row["tier"])
                        if row.get("contact_email"):
                            ctl.bump("with_email")
                    if added >= self.target:
                        for f in futures:
                            f.cancel()
                        break
                ctl.log(f"Progress: {added}/{self.target} new firms")
        ctl.stage("enrich", status="done", done=processed, total=total)
        ctl.set_activity("")
        return added

    def _screen_existing(self) -> None:
        """Screen firms already on the master list: find each firm's website (verified against the
        name), read it, classify whether it RAISES CAPITAL for CTAs or is a hedger / execution-only
        shop, record the supporting evidence, fill contact details, and score it."""
        ctl, s = self.ctl, self.s
        store = ctl.store
        todo = [f for f in store.firms(limit=100000) if not (f.get("ib_type") or "").strip()]
        todo = todo[: s.firm_enrich_existing]
        ctl.log(f"Screening {len(todo)} firm(s) on the list: capital-raiser for CTAs, or hedger / execution-only?")
        ctl.stage("enrich", status="running", done=0, total=max(1, len(todo)))
        done = 0
        for i, f in enumerate(todo, start=1):
            ctl.checkpoint()
            name = f.get("firm_name", "").title()
            ctl.set_activity(f"Screening {name}")
            domain, hit_url = self._resolve_website(f)
            if not domain:
                ctl.bump("unverified")
                ctl.stage("enrich", done=i)
                continue
            site = self.svc.fetcher.fetch_site(domain)
            text = " ".join([site.contact.text, site.contact.description]) if site.pages_fetched else ""
            fields = extract_fields(text, name, hit_url, self.llm)
            emails = work_emails(site.contact.emails, domain) if site.pages_fetched else []
            contact_name = fields.get("contact_name", "")
            if contact_name and emails:
                emails = person_emails(emails, contact_name) or emails
            update = {"website": f"https://{domain}", "source_url": hit_url or f"https://{domain}",
                      "date_verified": date.today().isoformat()}
            if emails:
                update["contact_email"] = emails[0]
            if site.contact.phones:
                update["contact_phone"] = site.contact.phones[0]
            if contact_name:
                update["contact_name"] = contact_name
            if fields.get("contact_title"):
                update["contact_title"] = fields["contact_title"]
            for key in ("firm_id", "city", "us_state", "country"):
                if fields.get(key) and not f.get(key):
                    update[key] = fields[key]
            merged = {**f, **update}
            update.update(self._classify_and_score(merged, text, f"https://{domain}"))
            store.update_firm(f["firm_key"], **update)
            done += 1
            ctl.bump("screened")
            ctl.bump(update.get("ib_type", "uncertain"))
            if emails:
                ctl.bump("with_email")
            ctl.stage("enrich", done=i)
        ctl.log(f"Screened {done} firm(s); {ctl.counters.get('unverified', 0)} had no website we could match")
        ctl.stage("enrich", status="done", done=len(todo), total=max(1, len(todo)))
        ctl.set_activity("")

    def _resolve_website(self, f: dict) -> tuple[str, str]:
        """Return (domain, source_url) for a firm, using its stored website or one web search.
        A looked-up website is only accepted when its domain contains a significant word of the name."""
        if (f.get("website") or "").strip():
            d = normalize_domain(f["website"])
            if d:
                return d, f.get("source_url", "")
        name = f.get("firm_name", "").title()
        query = f"{name} {f.get('city', '').title()} introducing broker futures"
        try:
            hits = self.svc.search.search(query, 0, "google")
        except SearchError as exc:
            self.ctl.bump("errors")
            self.ctl.log(f"Lookup failed for {name}: {exc}", "warn")
            return "", ""
        significant = [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) >= 4]
        for hit in hits:
            domain = normalize_domain(hit.url)
            if not domain or is_blocked(domain, self.s.blocklist_domains):
                continue
            if any(w in domain.replace("-", "") for w in significant):
                return domain, hit.url
        return "", ""

    def _classify_and_score(self, firm: dict, text: str, url: str) -> dict:
        """Classify the IB from its page text and score it. Returns the fields to store."""
        c = self.svc.llm if self.s.use_llm else None
        klass = ib_classify.classify(text, firm.get("firm_name", ""), url, c)
        merged = {**firm, **klass.as_fields()}
        scored = firm_scoring.score_firm(merged, self.s.firm_weights, self.s.firm_target_countries,
                                         self.s.firm_tiers)
        return {**klass.as_fields(),
                "lead_score": scored.score, "tier": scored.tier,
                "score_reasons": "; ".join(scored.reasons)}

    def _build_row(self, c: dict) -> dict | None:
        """Turn one search hit into a master-list row, or None if it is not a registered firm."""
        domain = c["domain"]
        site = self.svc.fetcher.fetch_site(domain)
        text = " ".join([site.contact.text, site.contact.description]) if site.pages_fetched else ""
        if not text:
            text = " ".join([c["title"], c["snippet"]])
        fields = extract_fields(text, c["title"], c["url"], self.llm)
        if not any(fields.get(k) for k in ("cta", "cpo", "introducing_broker", "fcm", "forex_firm", "swap_firm", "nfa_member")):
            return None  # the page does not show any registration: not a list-type firm
        emails = work_emails(site.contact.emails, domain) if site.pages_fetched else []
        contact_name = fields.pop("contact_name", "")
        contact_title = fields.pop("contact_title", "")
        if contact_name and emails:
            matched = person_emails(emails, contact_name)
            if matched:
                emails = matched
        row = {col: "" for col in firm_key_columns()}
        row.update(fields)
        row.update({
            "firm_name": (fields.get("firm_name") or site.contact.company_name or domain).upper()[:120],
            "website": f"https://{domain}",
            "contact_name": contact_name,
            "contact_title": contact_title,
            "contact_email": emails[0] if emails else "",
            "contact_phone": site.contact.phones[0] if site.contact.phones else "",
            "source_url": c["url"],
            "source_query": c["query"],
            "date_added": date.today().isoformat(),
            "date_verified": date.today().isoformat(),
            "outreach_status": "none",
        })
        if c["nfa_id"] and not row.get("firm_id"):
            row["firm_id"] = c["nfa_id"]
        row["country"] = row.get("country") or ""
        row.update(self._classify_and_score(row, text, f"https://{domain}"))
        return row

    def _store_row(self, row: dict) -> bool:
        store = self.ctl.store
        key = firm_key(row.get("firm_id", ""), row.get("firm_name", ""), row.get("city", ""))
        if key in store.firm_keys():
            return False
        store.upsert_firm({**row, "firm_key": key})
        store.mark_seen(f"firm:{key}", "added")
        return True

    # ----- Part 2a: export -------------------------------------------------------------
    def _export(self) -> None:
        ctl = self.ctl
        ctl.stage("export", status="running", done=0, total=1)
        rows = ctl.store.firms(limit=100000)
        path = write_master(rows, self.out_dir / default_export_name())
        write_master(rows, self.out_dir / "NFA_Master_IB_List_latest.xlsx")
        ctl.log(f"Master list written: {path.name} ({len(rows)} firms, same 20 columns plus ours)")
        ctl.stage("export", status="done", done=1)

    # ----- Part 2b: tailored emails -----------------------------------------------------
    def _queue_outreach(self) -> None:
        ctl, s = self.ctl, self.s
        ctl.stage("deliver", status="running", done=0, total=1)
        store = ctl.store
        queued = 0
        for f in store.firms(limit=100000):
            decision = (f.get("decision") or "").lower()
            if decision == "exclude":
                continue
            # Only capital-raising IBs flagged high-priority, or firms a person marked "keep".
            if decision != "keep" and (f.get("recommendation") or "") != "High-priority review":
                continue
            if not f.get("contact_email"):
                continue
            if f.get("outreach_status") not in ("none", "", None):
                continue
            email = f["contact_email"].lower()
            if store.is_suppressed(email):
                store.update_firm(f["firm_key"], outreach_status="suppressed")
                continue
            contact_id = self._contact_for(f, email)
            secret = self.engine._secret() if self.engine else s.outreach_secret or "unset"
            from . import mailer
            unsub = mailer.unsubscribe_url(s, contact_id, secret)
            contact = store.get_contact(contact_id)
            drafted = firm_email.draft(f, s, contact, self.llm, unsub)
            store.create_message({
                "enrollment_id": None, "contact_id": contact_id, "step": 1, "variant": "nfa-firm",
                "to_email": email, "subject": drafted["subject"], "body": drafted["body"],
                "status": "draft" if s.approval_required else "approved",
                "scheduled_for": now_iso(),
            })
            store.update_firm(f["firm_key"], outreach_status="draft",
                              email_subject=drafted["subject"], email_body=drafted["body"])
            queued += 1
        ctl.log(f"Queued {queued} tailored email(s) in the Outbox for approval "
                f"(tier {s.firm_outreach_tier} and above, with a work email)")
        ctl.stage("deliver", status="done", done=1)
        ctl.set_activity("")

    def _contact_for(self, firm: dict, email: str) -> int:
        store = self.ctl.store
        existing = store.find_contact(email=email)
        if existing:
            return existing["id"]
        name = firm.get("contact_name", "")
        parts = name.split()
        return store.insert_contact({
            "email": email, "full_name": name or firm.get("firm_name", "").title(),
            "first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]),
            "title": firm.get("contact_title", ""), "company": firm.get("firm_name", "").title(),
            "domain": normalize_domain(firm.get("website", "")), "country": firm.get("country", "").title(),
            "region": region_for(firm.get("country", "")), "source": "nfa_list", "source_detail": "NFA firm search",
            "channel": "web", "fit_score": firm.get("lead_score"), "tier": firm.get("tier"),
            "fit_reason": firm.get("score_reasons", "")[:300], "status": "active",
        })


def _nfa_id_in(text: str) -> str:
    m = re.search(r"NFA\s*(?:ID|No\.?|Number|#)?\s*[:#]?\s*(\d{6,7})\b", text or "", re.I)
    return m.group(1) if m else ""


def firm_key_columns() -> list[str]:
    from .firms import DB_COLUMNS
    return DB_COLUMNS
