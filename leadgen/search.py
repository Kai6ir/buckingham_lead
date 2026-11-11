"""Google search through SerpApi (official API — no direct Google scraping)."""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from .models import SearchHit

SERPAPI_URL = "https://serpapi.com/search.json"


class SearchError(RuntimeError):
    pass


def normalize_domain(url: str) -> str:
    """'https://www.Example.com/about' -> 'example.com'."""
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_blocked(domain: str, blocklist: list[str]) -> bool:
    """True if the domain is, or is a subdomain of, a blocklisted domain."""
    return any(domain == b or domain.endswith("." + b) for b in blocklist)


class SerpApiSearch:
    """Fetches organic Google results, one page (10 results) at a time."""

    def __init__(self, api_key: str, gl: str = "us", hl: str = "en",
                 timeout: float = 20.0, session: requests.Session | None = None):
        if not api_key:
            raise SearchError("SERPAPI_KEY is not set in .env")
        self.api_key = api_key
        self.gl = gl
        self.hl = hl
        self.timeout = timeout
        self.session = session or requests.Session()

    def search(self, query: str, page: int, channel: str = "google") -> list[SearchHit]:
        """channel='linkedin' or 'x' restricts Google results to public profile pages on that site."""
        if channel == "linkedin":
            q = f"site:linkedin.com/in {query}"
        elif channel == "x":
            q = f"site:x.com {query}"
        elif channel == "nfa":
            q = f"site:nfa.futures.org {query}"
        else:
            q = query
        params = {
            "engine": "google",
            "q": q,
            "api_key": self.api_key,
            "num": 10,
            "start": page * 10,
            "gl": self.gl,
            "hl": self.hl,
        }
        try:
            resp = self.session.get(SERPAPI_URL, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SearchError(f"SerpApi request failed: {exc}") from exc
        if resp.status_code != 200:
            raise SearchError(f"SerpApi HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get("error"):
            raise SearchError(f"SerpApi error: {data['error']}")

        hits = []
        for item in data.get("organic_results", []) or []:
            url = item.get("link", "")
            if not url:
                continue
            hits.append(SearchHit(
                title=item.get("title", "") or "",
                url=url,
                snippet=item.get("snippet", "") or "",
                query=query,
                channel=channel,
            ))
        return hits
