"""Plain data objects shared by the pipeline, storage, exports and dashboard.

A lead is either a *company* (found through a Google result, keyed by its domain)
or a *person* (found through a LinkedIn profile result, keyed by the profile URL).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

TIERS = ("HOT", "WARM", "COLD", "REJECT")
OUTREACH_STATUSES = ("none", "draft", "approved", "sent", "skipped")


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    query: str
    channel: str = "google"


@dataclass
class Candidate:
    key: str
    kind: str                 # company | person
    channel: str              # google | linkedin
    query: str
    title: str = ""
    snippet: str = ""
    website: str = ""
    domain: str = ""
    linkedin_url: str = ""
    x_handle: str = ""
    person_name: str = ""
    role: str = ""
    company_name: str = ""


@dataclass
class Lead:
    key: str
    kind: str = "company"
    channel: str = "google"
    website: str = ""
    domain: str = ""
    company_name: str = ""
    person_name: str = ""
    role: str = ""
    linkedin_url: str = ""
    x_handle: str = ""
    country: str = ""
    title: str = ""
    snippet: str = ""
    source_query: str = ""
    emails: list[str] = field(default_factory=list)       # work emails found on the company's site
    phones: list[str] = field(default_factory=list)
    contact_status: str = "unknown"                       # work_contact_found | no_work_contact | unknown
    keyword_pct: int = 0
    ai_score: int | None = None
    ai_reason: str = ""
    score: int = 0
    tier: str = "COLD"
    outreach_message: str = ""
    outreach_status: str = "none"
    fetch_error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def best_email(self) -> str:
        return self.emails[0] if self.emails else ""

    @property
    def accepted(self) -> bool:
        return self.tier != "REJECT"

    def to_row(self) -> dict:
        d = asdict(self)
        d["emails"] = "; ".join(self.emails)
        d["phones"] = "; ".join(self.phones)
        d["best_email"] = self.best_email
        return d


def parse_x_title(title: str) -> tuple[str, str]:
    """'Jane Doe (@janedoe) / X' -> ('Jane Doe', 'janedoe')."""
    m = re.search(r"^(.*?)\s*\(@([A-Za-z0-9_]{1,15})\)", title or "")
    if m:
        return m.group(1).strip()[:80], m.group(2).lower()
    return (title or "").split(" / ")[0].strip()[:80], ""


def parse_linkedin_title(title: str) -> tuple[str, str, str]:
    """'Jane Doe - Head of Allocations - Acme Capital | LinkedIn' -> (name, role, company)."""
    cleaned = re.sub(r"\s*\|\s*LinkedIn.*$", "", title or "", flags=re.I).strip()
    parts = [p.strip() for p in re.split(r"\s[-–—]\s", cleaned) if p.strip()]
    name = parts[0] if parts else ""
    role = parts[1] if len(parts) >= 3 else ""
    company = parts[-1] if len(parts) >= 2 else ""
    return name[:80], role[:120], company[:120]


def linkedin_key(url: str) -> str:
    """Canonical profile key: 'https://uk.linkedin.com/in/jane-doe-123?trk=x' -> 'linkedin.com/in/jane-doe-123'."""
    m = re.search(r"linkedin\.com/(in|pub)/([^/?#]+)", url or "", re.I)
    return f"linkedin.com/in/{m.group(2).lower()}" if m else ""
