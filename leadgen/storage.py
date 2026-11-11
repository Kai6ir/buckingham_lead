"""SQLite store: contacts, sequences, enrolments, messages, engagement events, suppression,
runs, activity log and settings. One database holds everything the bot knows.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    first_name TEXT, last_name TEXT, full_name TEXT,
    title TEXT, company TEXT, domain TEXT,
    linkedin_url TEXT, linkedin_key TEXT, x_handle TEXT,
    phone TEXT, country TEXT, region TEXT, segment TEXT,
    source TEXT, source_detail TEXT, channel TEXT,
    lawful_basis TEXT,
    fit_score INTEGER, tier TEXT, fit_reason TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT, updated_at TEXT, last_contacted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_email ON contacts(email) WHERE email IS NOT NULL AND email != '';
CREATE INDEX IF NOT EXISTS ix_contacts_linkedin ON contacts(linkedin_key);
CREATE INDEX IF NOT EXISTS ix_contacts_domain ON contacts(domain);

CREATE TABLE IF NOT EXISTS sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    steps TEXT,               -- JSON list: [{"day": 0, "template": "..."}, ...]
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    sequence_id INTEGER NOT NULL,
    step INTEGER DEFAULT 0,               -- emails already created for this enrolment
    status TEXT DEFAULT 'active',         -- active | completed | replied | unsubscribed | bounced | suppressed | paused
    next_action_at TEXT,
    created_at TEXT, updated_at TEXT,
    UNIQUE(contact_id, sequence_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER,
    contact_id INTEGER NOT NULL,
    step INTEGER,
    variant TEXT,                         -- default | engaged | silent | manual
    to_email TEXT,
    subject TEXT, body TEXT,
    status TEXT DEFAULT 'draft',          -- draft | approved | sent | rejected | failed
    provider TEXT, provider_message_id TEXT,
    error TEXT,
    created_at TEXT, approved_at TEXT, sent_at TEXT, scheduled_for TEXT
);
CREATE INDEX IF NOT EXISTS ix_messages_provider ON messages(provider_message_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    message_id INTEGER,
    type TEXT,                            -- delivered | open | click | reply | bounce | unsubscribe | complaint
    occurred_at TEXT,
    source TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_message ON events(message_id);

CREATE TABLE IF NOT EXISTS firms (
    firm_key TEXT PRIMARY KEY,
    firm_id TEXT, firm_name TEXT, city TEXT, us_state TEXT, province TEXT, country TEXT,
    nfa_member TEXT, fcm TEXT, introducing_broker TEXT, cta TEXT, cpo TEXT, rfed TEXT,
    forex_dealer TEXT, forex_firm TEXT, swap_dealer TEXT, msp TEXT, swap_firm TEXT,
    floor_trader TEXT, guarantor_id TEXT, guarantor_name TEXT,
    website TEXT, contact_name TEXT, contact_title TEXT, contact_email TEXT, contact_phone TEXT,
    linkedin_url TEXT, source_url TEXT, source_query TEXT,
    ib_type TEXT, raises_for_cta TEXT, hedging_only TEXT, mf_relevance TEXT, client_segment TEXT,
    recommendation TEXT, lead_quality TEXT, decision TEXT, evidence_text TEXT, evidence_url TEXT,
    lead_score INTEGER, tier TEXT, score_reasons TEXT, outreach_status TEXT,
    email_subject TEXT, email_body TEXT, date_added TEXT, date_verified TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,                 -- discovery candidates already judged (accepted or rejected)
    decision TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS suppression (
    email TEXT PRIMARY KEY,
    reason TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, status TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, ts TEXT, level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    name TEXT PRIMARY KEY,
    value TEXT
);
"""

CONTACT_FIELDS = [
    "email", "first_name", "last_name", "full_name", "title", "company", "domain",
    "linkedin_url", "linkedin_key", "x_handle", "phone", "country", "region", "segment",
    "source", "source_detail", "channel", "lawful_basis", "fit_score", "tier", "fit_reason",
    "status",
]


def now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add firms columns introduced after a database was first created (keeps existing rows).

        Derived from the current firms column list, so any column added later is migrated too."""
        from .firms import DB_COLUMNS
        wanted = [(c, "INTEGER" if c == "lead_score" else "TEXT") for c in DB_COLUMNS]
        wanted += [("created_at", "TEXT"), ("updated_at", "TEXT")]
        with self._lock:
            have = {r["name"] for r in self._conn.execute("PRAGMA table_info(firms)")}
            for name, decl in wanted:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE firms ADD COLUMN {name} {decl}")
            self._conn.commit()

    def _q(self, sql: str, args=(), one=False):
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]

    def _x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return int(cur.lastrowid or 0)

    # ----- contacts ------------------------------------------------------
    def match_index(self) -> dict:
        """Lookup sets used to deduplicate a whole batch quickly."""
        with self._lock:
            emails = {r[0] for r in self._conn.execute("SELECT lower(email) FROM contacts WHERE email IS NOT NULL AND email != ''")}
            lkeys = {r[0] for r in self._conn.execute("SELECT linkedin_key FROM contacts WHERE linkedin_key IS NOT NULL AND linkedin_key != ''")}
            names = {(r[0] or "", r[1] or "") for r in self._conn.execute(
                "SELECT lower(full_name), lower(company) FROM contacts WHERE full_name IS NOT NULL AND full_name != ''")}
            domains = {r[0] for r in self._conn.execute("SELECT lower(domain) FROM contacts WHERE domain IS NOT NULL AND domain != ''")}
            handles = {r[0] for r in self._conn.execute("SELECT lower(x_handle) FROM contacts WHERE x_handle IS NOT NULL AND x_handle != ''")}
        return {"emails": emails, "linkedin": lkeys, "names": names, "domains": domains, "handles": handles}

    def find_contact(self, email: str = "", linkedin_key: str = "") -> dict | None:
        if email:
            hit = self._q("SELECT * FROM contacts WHERE lower(email)=?", (email.lower(),), one=True)
            if hit:
                return hit
        if linkedin_key:
            return self._q("SELECT * FROM contacts WHERE linkedin_key=?", (linkedin_key,), one=True)
        return None

    def insert_contact(self, data: dict) -> int:
        ts = now_iso()
        row = {k: data.get(k) for k in CONTACT_FIELDS}
        if row.get("email"):
            row["email"] = row["email"].strip().lower()
        row["status"] = row.get("status") or "active"
        cols = list(row) + ["created_at", "updated_at"]
        vals = list(row.values()) + [ts, ts]
        return self._x(
            f"INSERT INTO contacts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)

    def update_contact(self, contact_id: int, data: dict | None = None, **kw) -> None:
        merged = {**(data or {}), **kw}
        fields = {k: v for k, v in merged.items() if k in CONTACT_FIELDS and v not in (None, "")}
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields) + ",updated_at=?"
        self._x(f"UPDATE contacts SET {sets} WHERE id=?", list(fields.values()) + [now_iso(), contact_id])

    def get_contact(self, contact_id: int) -> dict | None:
        return self._q("SELECT * FROM contacts WHERE id=?", (contact_id,), one=True)

    def contacts(self, region: str = "", source: str = "", status: str = "", q: str = "",
                 limit: int = 200, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM contacts WHERE 1=1"
        args: list = []
        for col, val in (("region", region), ("source", source), ("status", status)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if q:
            sql += " AND (lower(full_name) LIKE ? OR lower(company) LIKE ? OR lower(email) LIKE ?)"
            like = f"%{q.lower()}%"
            args += [like, like, like]
        sql += " ORDER BY COALESCE(fit_score, 0) DESC, id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return self._q(sql, args)

    # ----- firms (NFA list) ----------------------------------------------------
    def firm_keys(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT firm_key FROM firms")}

    def firm_by_key(self, key: str) -> dict | None:
        return self._q("SELECT * FROM firms WHERE firm_key=?", (key,), one=True)

    def upsert_firm(self, data: dict) -> str:
        from .firms import DB_COLUMNS, clean_id, firm_key
        row = {c: ("" if data.get(c) is None else data.get(c)) for c in DB_COLUMNS}
        row["firm_id"] = clean_id(row["firm_id"])
        row["guarantor_id"] = clean_id(row["guarantor_id"])
        key = data.get("firm_key") or firm_key(row["firm_id"], row["firm_name"], row["city"])
        row["firm_key"] = key
        ts = now_iso()
        cols = ["firm_key"] + DB_COLUMNS[:]
        vals = [row["firm_key"]] + [row[c] for c in DB_COLUMNS]
        cols += ["created_at", "updated_at"]
        vals += [ts, ts]
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("firm_key", "created_at"))
        with self._lock:
            self._conn.execute(
                f"INSERT INTO firms ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                f"ON CONFLICT(firm_key) DO UPDATE SET {updates}", vals)
            self._conn.commit()
        return key

    def update_firm(self, key: str, **fields) -> None:
        from .firms import DB_COLUMNS
        fields = {k: v for k, v in fields.items() if k in DB_COLUMNS or k in ("firm_key",)}
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields) + ",updated_at=?"
        self._x(f"UPDATE firms SET {sets} WHERE firm_key=?", list(fields.values()) + [now_iso(), key])

    def firms(self, tier: str = "", outreach: str = "", ib_type: str = "",
              recommendation: str = "", decision: str = "", lead_quality: str = "",
              limit: int = 2000) -> list[dict]:
        sql = "SELECT * FROM firms WHERE 1=1"
        args: list = []
        for col, val in (("tier", tier), ("outreach_status", outreach), ("ib_type", ib_type),
                         ("recommendation", recommendation), ("decision", decision),
                         ("lead_quality", lead_quality)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        sql += " ORDER BY COALESCE(lead_score,-1) DESC, firm_name LIMIT ?"
        args.append(limit)
        return self._q(sql, args)

    def firm_counts(self) -> dict:
        rows = self._q("SELECT COALESCE(tier,'') AS tier, COUNT(*) AS n FROM firms GROUP BY tier")
        out = {"total": sum(r["n"] for r in rows), "A": 0, "B": 0, "C": 0}
        for r in rows:
            if r["tier"] in out:
                out[r["tier"]] = r["n"]
        out["with_email"] = self._q("SELECT COUNT(*) AS n FROM firms WHERE contact_email!=''", one=True)["n"]
        out["queued"] = self._q("SELECT COUNT(*) AS n FROM firms WHERE outreach_status='draft'", one=True)["n"]
        ib = {r["ib_type"] or "unclassified": r["n"] for r in
              self._q("SELECT COALESCE(ib_type,'') AS ib_type, COUNT(*) AS n FROM firms GROUP BY ib_type")}
        out["capital_raiser"] = ib.get("capital_raiser", 0)
        out["hedger"] = ib.get("hedger", 0)
        out["execution_only"] = ib.get("execution_only", 0)
        out["uncertain"] = ib.get("uncertain", 0)
        out["unclassified"] = ib.get("", 0) + ib.get("unclassified", 0)
        out["high_priority"] = self._q(
            "SELECT COUNT(*) AS n FROM firms WHERE recommendation='High-priority review'", one=True)["n"]
        out["kept"] = self._q("SELECT COUNT(*) AS n FROM firms WHERE decision='keep'", one=True)["n"]
        quality = {r["lead_quality"] or "": r["n"] for r in
                   self._q("SELECT COALESCE(lead_quality,'') AS lead_quality, COUNT(*) AS n "
                           "FROM firms GROUP BY lead_quality")}
        out["qualified"] = quality.get("qualified", 0)
        out["quality_uncertain"] = quality.get("uncertain", 0)
        out["irrelevant"] = quality.get("irrelevant", 0)
        return out

    def set_firm_decision(self, firm_key: str, decision: str) -> bool:
        if decision not in ("keep", "exclude", ""):
            return False
        with self._lock:
            cur = self._conn.execute("UPDATE firms SET decision=?, updated_at=? WHERE firm_key=?",
                                     (decision, now_iso(), firm_key))
            self._conn.commit()
            return cur.rowcount > 0

    def count_missing_basis(self, region: str = "") -> int:
        """Contacts with no lawful basis recorded (optionally for one region), that have an email."""
        sql = ("SELECT COUNT(*) AS n FROM contacts WHERE (lawful_basis IS NULL OR TRIM(lawful_basis)='') "
               "AND email IS NOT NULL AND email!=''")
        args: list = []
        if region:
            sql += " AND region=?"
            args.append(region)
        return self._q(sql, args, one=True)["n"]

    def set_lawful_basis(self, contact_ids: list[int], basis: str) -> int:
        if not contact_ids:
            return 0
        marks = ",".join("?" * len(contact_ids))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE contacts SET lawful_basis=?, updated_at=? WHERE id IN ({marks})",
                [basis, now_iso(), *contact_ids])
            self._conn.commit()
            return cur.rowcount

    def contact_counts(self) -> dict:
        total = self._q("SELECT COUNT(*) AS n FROM contacts", one=True)["n"]
        with_email = self._q("SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL AND email!=''", one=True)["n"]
        by_region = {r["region"] or "unknown": r["n"] for r in self._q(
            "SELECT region, COUNT(*) AS n FROM contacts GROUP BY region")}
        by_source = {r["source"] or "unknown": r["n"] for r in self._q(
            "SELECT source, COUNT(*) AS n FROM contacts GROUP BY source")}
        new_discovered = self._q("SELECT COUNT(*) AS n FROM contacts WHERE source='discovery'", one=True)["n"]
        return {"total": total, "with_email": with_email, "by_region": by_region,
                "by_source": by_source, "discovered": new_discovered}

    # ----- sequences & enrolments -----------------------------------------
    def upsert_sequence(self, name: str, steps: list[dict], active: bool = True) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sequences (name, steps, active, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET steps=excluded.steps, active=excluded.active",
                (name, json.dumps(steps), 1 if active else 0, now_iso()))
            self._conn.commit()
            row = self._conn.execute("SELECT id FROM sequences WHERE name=?", (name,)).fetchone()
            return int(row[0])

    def sequences(self) -> list[dict]:
        rows = self._q("SELECT * FROM sequences ORDER BY id")
        for r in rows:
            r["steps"] = json.loads(r["steps"] or "[]")
        return rows

    def sequence_by_id(self, sequence_id: int) -> dict:
        row = self._q("SELECT * FROM sequences WHERE id=?", (sequence_id,), one=True)
        row["steps"] = row["steps"]
        return row

    def enroll(self, contact_id: int, sequence_id: int, next_action_at: str) -> int:
        """Returns the enrolment id, or 0 if the contact is already enrolled in this sequence."""
        ts = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO enrollments (contact_id, sequence_id, step, status, next_action_at, created_at, updated_at) "
                "VALUES (?,?,0,'active',?,?,?)", (contact_id, sequence_id, next_action_at, ts, ts))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_enrollment(self, enrollment_id: int) -> dict | None:
        return self._q("SELECT * FROM enrollments WHERE id=?", (enrollment_id,), one=True)

    def pending_for_enrollment(self, enrollment_id: int) -> bool:
        return self._q("SELECT 1 AS x FROM messages WHERE enrollment_id=? AND status IN ('draft','approved') LIMIT 1",
                       (enrollment_id,), one=True) is not None

    def enrollments(self, status: str = "", limit: int = 500) -> list[dict]:
        sql = ("SELECT e.*, c.full_name, c.email, c.company, c.region, s.name AS sequence_name "
               "FROM enrollments e JOIN contacts c ON c.id=e.contact_id JOIN sequences s ON s.id=e.sequence_id")
        args: list = []
        if status:
            sql += " WHERE e.status=?"
            args.append(status)
        sql += " ORDER BY e.updated_at DESC LIMIT ?"
        args.append(limit)
        return self._q(sql, args)

    def due_enrollments(self, now: str, limit: int = 500) -> list[dict]:
        return self._q(
            "SELECT e.*, s.steps AS steps FROM enrollments e JOIN sequences s ON s.id=e.sequence_id "
            "WHERE e.status='active' AND s.active=1 AND e.next_action_at IS NOT NULL AND e.next_action_at<=? "
            "ORDER BY e.next_action_at LIMIT ?", (now, limit))

    def update_enrollment(self, enrollment_id: int, **fields) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields) + ",updated_at=?"
        self._x(f"UPDATE enrollments SET {sets} WHERE id=?", list(fields.values()) + [now_iso(), enrollment_id])

    def stop_enrollments_for(self, contact_id: int, status: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE enrollments SET status=?, next_action_at=NULL, updated_at=? "
                "WHERE contact_id=? AND status IN ('active','paused')", (status, now_iso(), contact_id))
            self._conn.commit()
            return cur.rowcount

    def enrollment_for(self, contact_id: int) -> list[dict]:
        return self._q("SELECT * FROM enrollments WHERE contact_id=?", (contact_id,))

    # ----- messages -------------------------------------------------------
    def create_message(self, data: dict) -> int:
        cols = ["enrollment_id", "contact_id", "step", "variant", "to_email", "subject", "body",
                "status", "scheduled_for"]
        row = {c: data.get(c) for c in cols}
        row["status"] = row["status"] or "draft"
        row["created_at"] = now_iso()
        return self._x(
            f"INSERT INTO messages ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))

    def messages(self, status: str = "", limit: int = 300) -> list[dict]:
        sql = ("SELECT m.*, c.full_name, c.company, c.region, c.title, c.linkedin_url, c.fit_reason "
               "FROM messages m JOIN contacts c ON c.id=m.contact_id")
        args: list = []
        if status:
            sql += " WHERE m.status=?"
            args.append(status)
        sql += " ORDER BY m.id DESC LIMIT ?"
        args.append(limit)
        return self._q(sql, args)

    def get_message(self, message_id: int) -> dict | None:
        return self._q("SELECT * FROM messages WHERE id=?", (message_id,), one=True)

    def update_message(self, message_id: int, **fields) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE messages SET {sets} WHERE id=?", list(fields.values()) + [message_id])

    def message_by_provider_id(self, provider_id: str) -> dict | None:
        pid = (provider_id or "").strip().strip("<>")
        if not pid:
            return None
        return self._q("SELECT * FROM messages WHERE provider_message_id=?", (pid,), one=True)

    def last_sent_message(self, enrollment_id: int) -> dict | None:
        return self._q("SELECT * FROM messages WHERE enrollment_id=? AND status='sent' ORDER BY id DESC LIMIT 1",
                       (enrollment_id,), one=True)

    def sent_since(self, since_iso: str) -> int:
        return self._q("SELECT COUNT(*) AS n FROM messages WHERE status='sent' AND sent_at>=?",
                       (since_iso,), one=True)["n"]

    # ----- events & funnel --------------------------------------------------
    def add_event(self, contact_id: int | None, message_id: int | None, etype: str,
                  occurred_at: str, source: str, detail: str = "") -> int:
        return self._x(
            "INSERT INTO events (contact_id, message_id, type, occurred_at, source, detail) VALUES (?,?,?,?,?,?)",
            (contact_id, message_id, etype, occurred_at, source, detail[:500]))

    def events_for_contact(self, contact_id: int) -> list[dict]:
        return self._q("SELECT * FROM events WHERE contact_id=? ORDER BY id", (contact_id,))

    def has_event(self, message_id: int, etype: str) -> bool:
        return self._q("SELECT 1 AS x FROM events WHERE message_id=? AND type=? LIMIT 1",
                       (message_id, etype), one=True) is not None

    def funnel(self) -> dict:
        def count(sql: str) -> int:
            return self._q(sql, one=True)["n"]
        return {
            "sent": count("SELECT COUNT(*) AS n FROM messages WHERE status='sent'"),
            "delivered": count("SELECT COUNT(DISTINCT message_id) AS n FROM events WHERE type='delivered'"),
            "opened": count("SELECT COUNT(DISTINCT message_id) AS n FROM events WHERE type='open'"),
            "clicked": count("SELECT COUNT(DISTINCT message_id) AS n FROM events WHERE type='click'"),
            "replied": count("SELECT COUNT(DISTINCT contact_id) AS n FROM events WHERE type='reply'"),
            "bounced": count("SELECT COUNT(DISTINCT message_id) AS n FROM events WHERE type='bounce'"),
            "unsubscribed": count("SELECT COUNT(DISTINCT contact_id) AS n FROM events WHERE type='unsubscribe'"),
            "pending_approval": count("SELECT COUNT(*) AS n FROM messages WHERE status='draft'"),
        }

    # ----- suppression -------------------------------------------------------
    def suppress(self, email: str, reason: str) -> None:
        if not email:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO suppression (email, reason, created_at) VALUES (?,?,?) "
                "ON CONFLICT(email) DO NOTHING", (email.lower(), reason, now_iso()))
            self._conn.commit()

    def seen_keys(self) -> set[str]:
        return {r["key"] for r in self._q("SELECT key FROM seen")}

    def mark_seen(self, key: str, decision: str) -> None:
        if key:
            self._x("INSERT INTO seen (key, decision, created_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO NOTHING", (key, decision, now_iso()))

    def is_suppressed(self, email: str) -> bool:
        return self._q("SELECT 1 AS x FROM suppression WHERE email=?", ((email or "").lower(),), one=True) is not None

    # ----- settings -----------------------------------------------------------
    def get_setting(self, name: str, default: str = "") -> str:
        row = self._q("SELECT value FROM settings WHERE name=?", (name,), one=True)
        return row["value"] if row else default

    def set_setting(self, name: str, value: str) -> None:
        self._x("INSERT INTO settings (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, value))

    # ----- runs & logs ----------------------------------------------------------
    def start_run(self, started_at: str) -> int:
        return self._x("INSERT INTO runs (started_at, status, stats) VALUES (?, 'running', '{}')", (started_at,))

    def finish_run(self, run_id: int, finished_at: str, status: str, stats: dict) -> None:
        self._x("UPDATE runs SET finished_at=?, status=?, stats=? WHERE id=?",
                (finished_at, status, json.dumps(stats), run_id))

    def mark_interrupted_runs(self, finished_at: str) -> None:
        self._x("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (finished_at,))

    def recent_runs(self, n: int = 10) -> list[dict]:
        rows = self._q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,))
        for r in rows:
            r["stats"] = json.loads(r["stats"] or "{}")
        return rows

    def add_log(self, run_id: int | None, ts: str, level: str, message: str) -> int:
        return self._x("INSERT INTO logs (run_id, ts, level, message) VALUES (?, ?, ?, ?)",
                       (run_id, ts, level, message))

    def logs_since(self, after_id: int, limit: int = 300) -> list[dict]:
        if after_id <= 0:
            rows = self._q("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
            return list(reversed(rows))
        return self._q("SELECT * FROM logs WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
