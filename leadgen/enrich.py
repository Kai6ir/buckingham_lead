"""Visit a company website and pull out emails, phone numbers and page text.

Polite by default: honours robots.txt (configurable), uses a real User-Agent,
short timeouts, a random delay per request, and only a handful of pages per site.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from urllib import robotparser
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; BuckinghamLeadBot/1.0; +contact: sales research)"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])?\d{2,4}[\s.-]\d{3,4}(?:[\s.-]\d{3,4})?")
JUNK_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
JUNK_EMAIL_DOMAINS = {
    "example.com", "example.org", "domain.com", "email.com", "yourdomain.com",
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "godaddy.com",
}
JUNK_EMAIL_LOCALS = {"noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster"}


@dataclass
class ContactInfo:
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    company_name: str = ""
    description: str = ""
    text: str = ""


@dataclass
class SiteData:
    domain: str
    contact: ContactInfo = field(default_factory=ContactInfo)
    pages_fetched: int = 0
    error: str = ""


def _clean_emails(candidates: list[str]) -> list[str]:
    seen, out = set(), []
    for raw in candidates:
        email = raw.strip().strip(".").lower()
        if email in seen or "@" not in email:
            continue
        local, _, dom = email.partition("@")
        if email.endswith(JUNK_EMAIL_SUFFIXES) or dom in JUNK_EMAIL_DOMAINS or local in JUNK_EMAIL_LOCALS:
            continue
        seen.add(email)
        out.append(email)
    return out


def _clean_phones(candidates: list[str]) -> list[str]:
    seen, out = set(), []
    for raw in candidates:
        digits = re.sub(r"\D", "", raw)
        if not 10 <= len(digits) <= 15 or len(set(digits)) < 4:
            continue  # too short, too long, or a repeated-digit placeholder
        key = digits[-10:]
        if key in seen:
            continue
        seen.add(key)
        out.append(raw.strip())
    return out


def extract_contacts(html: str) -> ContactInfo:
    """Pure function: parse one HTML page into emails, phones and text."""
    soup = BeautifulSoup(html, "lxml")

    mailtos = [a["href"][len("mailto:"):].split("?")[0] for a in soup.select('a[href^="mailto:"]')]
    tels = [a["href"][len("tel:"):] for a in soup.select('a[href^="tel:"]')]

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())

    emails = _clean_emails(mailtos + EMAIL_RE.findall(text))
    phones = _clean_phones(tels + PHONE_RE.findall(text)[:40])

    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    meta_desc = soup.find("meta", attrs={"name": "description"})
    title_tag = soup.title.string.strip() if soup.title and soup.title.string else ""
    company = (og_site["content"].strip() if og_site and og_site.get("content") else "") or title_tag
    company = re.split(r"\s[|\-–—]\s", company)[0][:120]
    description = (meta_desc["content"].strip() if meta_desc and meta_desc.get("content") else "")[:400]

    return ContactInfo(emails=emails, phones=phones, company_name=company,
                       description=description, text=text[:20000])


class SiteFetcher:
    def __init__(self, timeout: float = 8.0, delay_range=(0.5, 1.5), respect_robots: bool = True,
                 contact_paths=None, max_contact_pages: int = 3,
                 session: requests.Session | None = None):
        self.timeout = timeout
        self.delay_range = delay_range
        self.respect_robots = respect_robots
        self.contact_paths = contact_paths or ["/contact", "/about"]
        self.max_contact_pages = max_contact_pages
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._robots_lock = Lock()

    def _allowed(self, base: str, url: str) -> bool:
        if not self.respect_robots:
            return True
        with self._robots_lock:
            if base not in self._robots:
                rp = robotparser.RobotFileParser()
                try:
                    resp = self.session.get(base + "/robots.txt", timeout=self.timeout)
                    if resp.status_code == 200:
                        rp.parse(resp.text.splitlines())
                        self._robots[base] = rp
                    else:
                        self._robots[base] = None  # no robots file -> allowed
                except requests.RequestException:
                    self._robots[base] = None
            rp = self._robots[base]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _get(self, url: str) -> requests.Response | None:
        time.sleep(random.uniform(*self.delay_range))
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException:
            return None
        if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            return None
        return resp

    def fetch_page_text(self, url: str) -> str:
        """Visible text of one page (robots.txt respected). Empty string if it cannot be read."""
        from urllib.parse import urlparse
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if not self._allowed(base, url):
            return ""
        resp = self._get(url)
        if resp is None:
            return ""
        return extract_contacts(resp.text).text

    def fetch_site(self, domain: str) -> SiteData:
        data = SiteData(domain=domain)
        home = None
        for scheme in ("https://", "http://"):
            base = f"{scheme}{domain}"
            if not self._allowed(base, base + "/"):
                data.error = "disallowed by robots.txt"
                return data
            home = self._get(base + "/")
            if home is not None:
                break
        if home is None:
            data.error = "homepage unreachable"
            return data

        base = home.url.rstrip("/")
        contact = extract_contacts(home.text)
        data.pages_fetched = 1
        extra_pages = 0
        for path in self.contact_paths:
            if extra_pages >= self.max_contact_pages:
                break
            url = urljoin(base + "/", path.lstrip("/"))
            if not self._allowed(base, url):
                continue
            resp = self._get(url)
            if resp is None:
                continue
            extra_pages += 1
            data.pages_fetched += 1
            page = extract_contacts(resp.text)
            contact.emails = _clean_emails(contact.emails + page.emails)
            contact.phones = _clean_phones(contact.phones + page.phones)
            contact.text = (contact.text + " " + page.text)[:40000]
        data.contact = contact
        return data


FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.de", "mail.com", "qq.com", "163.com",
}


def work_emails(emails: list[str], domain: str) -> list[str]:
    """Keep only addresses on the company's own domain (drops personal free-mail accounts)."""
    domain = domain.lower()
    out = []
    for e in emails:
        dom = e.rsplit("@", 1)[-1].lower()
        if dom in FREE_MAIL_DOMAINS:
            continue
        if dom == domain or dom.endswith("." + domain) or domain.endswith("." + dom):
            out.append(e)
    return out


def person_emails(emails: list[str], person_name: str) -> list[str]:
    """Work emails that contain the person's first or last name (e.g. jane.doe@, jdoe@)."""
    parts = [re.sub(r"[^a-z]", "", p.lower()) for p in person_name.split() if len(p) > 1]
    if not parts:
        return []
    first, last = parts[0], parts[-1]
    hits = []
    for e in emails:
        local = e.split("@", 1)[0].lower()
        if (last and last in re.sub(r"[^a-z]", "", local)) or (first and local.startswith(first[:1] + last)):
            hits.append(e)
    return hits
