"""Offline stand-ins for SerpApi, website fetching and the LLM.

Used by the dashboard's "Demo" mode so you can watch the whole bot — planning, Google and
LinkedIn search, work-contact checks, drafting, export and delivery — before any API keys
exist. Demo runs use their own database (data/demo.db) and output folder (outputs/demo/),
so they never mix with real leads.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time

from .enrich import ContactInfo, SiteData
from .models import SearchHit

WORDS = ["managed", "futures", "trend", "following", "allocation", "liquid", "alternatives",
         "pension", "strategies", "crisis", "offset", "systematic", "macro", "family", "office",
         "overlay", "allocators", "investment", "private", "equity", "fund", "manager", "asset",
         "management", "multi", "strategy", "portfolio", "advisory", "capital", "partners"]
NAME_A = ["Northgate", "Harbor", "Meridian", "Cobalt", "Ashford", "Brightline", "Summit",
          "Keystone", "Larkspur", "Vantage", "Ironwood", "Silverline", "Crescent", "Atlas"]
NAME_B = ["Capital", "Advisors", "Partners", "Asset Management", "Investments", "Strategies"]
FIRST = ["Jane", "Michael", "Priya", "Tom", "Sofia", "Daniel", "Amara", "Lucas"]
LAST = ["Doe", "Okafor", "Lindqvist", "Bennett", "Moreau", "Tanaka", "Reyes", "Walsh"]
ROLES = ["Head of Allocations", "Managing Director", "Chief Investment Officer",
         "Portfolio Manager", "Director of Alternatives", "Partner, Investments"]
DEMO_BLOCKED = ["www.bloomberg.com", "en.wikipedia.org"]


def _seed(text: str) -> random.Random:
    return random.Random(int(hashlib.md5(text.encode()).hexdigest()[:8], 16))


def _company_domain(rng: random.Random) -> str:
    return f"{rng.choice(NAME_A).lower()}{rng.choice(WORDS)}{rng.randint(1, 60)}.com"


class DemoSearch:
    def search(self, query: str, page: int, channel: str = "google") -> list[SearchHit]:
        time.sleep(0.3)
        rng = _seed(f"{channel}|{query}|{page}")
        hits = []
        for _ in range(10):
            if channel == "x":
                first, last = rng.choice(FIRST), rng.choice(LAST)
                handle = f"{first.lower()}{last.lower()}{rng.randint(1, 99)}"
                hits.append(SearchHit(
                    title=f"{first} {last} (@{handle}) / X",
                    url=f"https://x.com/{handle}",
                    snippet=f"Allocator in {' '.join(query.split()[:3])}. Views on markets.",
                    query=query, channel="x"))
                continue
            if channel == "linkedin":
                first, last = rng.choice(FIRST), rng.choice(LAST)
                role, company = rng.choice(ROLES), _company_domain(rng).split(".")[0].title()
                slug = f"{first}-{last}-{rng.randint(100, 999)}".lower()
                hits.append(SearchHit(
                    title=f"{first} {last} - {role} - {company} | LinkedIn",
                    url=f"https://www.linkedin.com/in/{slug}",
                    snippet=f"{role} at {company}. Focused on {' '.join(query.split()[:3])}.",
                    query=query, channel="linkedin"))
                continue
            if rng.random() < 0.08:
                domain = rng.choice(DEMO_BLOCKED)
            else:
                domain = _company_domain(rng)
            hits.append(SearchHit(
                title=f"{domain.replace('www.', '').split('.')[0].title()} — {query.title()}",
                url=f"https://{domain}/",
                snippet=f"Leading firm in {' '.join(query.split()[:3])} with a team focused on clients.",
                query=query, channel="google"))
        return hits


class DemoFetcher:
    def fetch_site(self, domain: str) -> SiteData:
        time.sleep(0.2)
        rng = _seed(domain)
        name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}"
        if rng.random() < 0.12:
            return SiteData(domain=domain, error="homepage unreachable")
        if rng.random() < 0.2:  # off-topic site -> should be rejected
            text = ("Tips for a better garden this spring. Recipes, travel and lifestyle stories. "
                    "Subscribe to our newsletter.")
            return SiteData(domain=domain, contact=ContactInfo(company_name=f"{name} Blog", text=text),
                            pages_fetched=1)
        emails = []
        # Work emails on the company domain; sometimes a person's own address is published.
        if rng.random() < 0.6:
            emails.append(f"{rng.choice(['info', 'contact', 'invest', 'ir', 'sales'])}@{domain}")
        if rng.random() < 0.35:
            emails.append(f"{rng.choice(FIRST).lower()}.{rng.choice(LAST).lower()}@{domain}")
        if rng.random() < 0.1:
            emails.append(f"{rng.choice(FIRST).lower()}@gmail.com")  # personal -> not a work contact
        phones = []
        if rng.random() < 0.6:
            phones.append(f"+1 (212) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}")
        cities = [("Chicago", "Illinois"), ("New York", "New York"), ("Houston", "Texas"), ("Miami", "Florida")]
        city, state = rng.choice(cities)
        # Three kinds of introducing broker, so the classifier has something to separate:
        kind = rng.choices(["raiser", "hedger", "execution"], weights=[0.4, 0.45, 0.15])[0]
        if kind == "raiser":
            text = (f"{name} is an introducing broker that raises capital for leading CTAs and "
                    "managed futures programs. We introduce accredited and high-net-worth investors "
                    "and family offices to third-party commodity trading advisors and liquid "
                    "alternative strategies. Our capital introduction team markets managed accounts "
                    "and fund structures to qualified investors. Contact us today.")
            desc = f"{name} raises capital for CTAs and managed futures managers."
        elif kind == "hedger":
            text = (f"{name} provides agricultural and energy hedging and price risk management for "
                    "producers, farmers, grain elevators and commercial end-users. We help clients "
                    "manage basis and input costs with futures and options. Execution for cash grain "
                    "and livestock. Contact our risk management desk.")
            desc = f"{name} is an agricultural and energy hedging brokerage."
            emails = [f"info@{domain}"] if rng.random() < 0.7 else []
        else:
            text = (f"{name} offers low-latency trade execution and direct market access for active "
                    "futures traders. Execution-only order routing, give-up and clearing, competitive "
                    "commission rates. Self-directed online trading platform.")
            desc = f"{name} is an execution-only futures broker."
        if rng.random() < 0.85:
            regs = rng.sample(["a commodity trading advisor", "a commodity pool operator",
                               "an introducing broker"], k=rng.randint(1, 2))
            text += (f" We are registered with the NFA as {' and '.join(regs)}. NFA member. "
                     f"NFA ID: {rng.randint(100000, 999999)}. Offices in {city}, {state}, United States.")
        contact = ContactInfo(emails=emails, phones=phones, company_name=name,
                              description=desc, text=text)
        return SiteData(domain=domain, contact=contact, pages_fetched=rng.randint(1, 3))


class DemoLLM:
    """Deterministic stand-in for any chat model: answers the planner and qualifier prompts."""

    provider = "demo"
    model = "demo-model"

    def complete_text(self, system: str, user: str, max_tokens: int = 1200) -> str:
        time.sleep(0.1)
        if '"ib_type"' in user:  # IB classification prompt — judge from the page text in the prompt
            from .ib_classify import classify_by_patterns
            m = re.search(r"Website text \(truncated\):\n(.*)", user, re.S)
            text = m.group(1) if m else user
            name_m = re.search(r"Firm: ([^\n]+)", user)
            k = classify_by_patterns(text, name_m.group(1) if name_m else "")
            return json.dumps({
                "ib_type": k.ib_type, "raises_for_cta": k.raises_for_cta,
                "hedging_only": k.hedging_only, "mf_relevance": k.mf_relevance,
                "client_segment": k.client_segment, "evidence": k.evidence_text,
            })
        if '"queries"' in user:
            queries = [
                {"channel": "google", "query": "managed futures fund manager"},
                {"channel": "google", "query": "CTA allocation investment firm"},
                {"channel": "linkedin", "query": "Head of Allocations managed futures"},
                {"channel": "linkedin", "query": "Chief Investment Officer liquid alternatives"},
                {"channel": "linkedin", "query": "Director of Alternatives family office"},
            ]
            return json.dumps({"queries": queries})
        if '"relevant"' in user:
            seed = int(hashlib.md5(user.encode()).hexdigest()[:6], 16)
            rng = random.Random(seed)
            relevant = rng.random() > 0.15
            want_msg = "no message needed" not in user
            name_match = re.search(r"Name: ([^\n]+)", user)
            first = (name_match.group(1).split()[0] if name_match and name_match.group(1) != "-" else "there")
            message = (f"Hi {first}, your work on allocation strategy caught my eye. We run managed "
                       "futures and trend-following solutions for institutions and would value a short "
                       "call to compare notes. Best regards") if want_msg and relevant else ""
            return json.dumps({
                "relevant": relevant,
                "score": rng.randint(45, 95) if relevant else rng.randint(5, 30),
                "company_name": "",
                "reason": "Matches the ideal customer on allocation and alternatives." if relevant
                          else "Not an investment decision-maker in scope.",
                "outreach_message": message,
            })
        return "{}"
