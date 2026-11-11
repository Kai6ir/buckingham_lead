"""Import the existing contact database from CSV or Excel, with deduplication.

Column names are matched loosely ("Work E-mail", "email address" and "Email" all map to email).
A row is a duplicate when its email, LinkedIn profile, or (name + company) already exists.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .models import linkedin_key
from .storage import Store

ALIASES = {
    "email": ["email", "e-mail", "work email", "work e-mail", "email address", "business email", "e mail"],
    "first_name": ["first name", "firstname", "first", "given name"],
    "last_name": ["last name", "lastname", "surname", "last", "family name"],
    "full_name": ["name", "full name", "contact name", "person"],
    "title": ["title", "job title", "position", "role"],
    "company": ["company", "organisation", "organization", "firm", "company name", "employer"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile", "linkedin link"],
    "country": ["country", "country name", "location country"],
    "region": ["region", "territory"],
    "segment": ["segment", "type", "category", "investor type", "client type"],
    "phone": ["phone", "mobile", "telephone", "phone number"],
    "lawful_basis": ["lawful basis", "basis", "consent basis"],
}

REGION_BY_COUNTRY = {
    "americas": {"united states", "usa", "us", "canada", "mexico", "brazil", "argentina", "chile", "colombia",
                 "peru", "bermuda", "cayman islands", "bahamas"},
    "europe": {"united kingdom", "uk", "england", "scotland", "wales", "ireland", "france", "germany", "italy",
               "spain", "portugal", "netherlands", "belgium", "switzerland", "austria", "sweden", "norway",
               "denmark", "finland", "iceland", "luxembourg", "monaco", "poland", "czech republic", "greece",
               "hungary", "romania", "bulgaria", "croatia", "slovenia", "slovakia", "estonia", "latvia",
               "lithuania", "malta", "cyprus", "russia", "ukraine", "turkey"},
    "asia-pacific": {"japan", "hong kong", "singapore", "china", "south korea", "korea", "taiwan", "australia",
                     "new zealand", "india", "thailand", "malaysia", "indonesia", "philippines", "vietnam"},
    "middle east": {"united arab emirates", "uae", "saudi arabia", "qatar", "kuwait", "bahrain", "israel",
                    "oman", "jordan", "lebanon"},
    "africa": {"south africa", "nigeria", "kenya", "egypt", "morocco", "mauritius", "ghana"},
}


def region_for(country: str, region: str = "") -> str:
    if region:
        return region.strip().lower()
    c = (country or "").strip().lower()
    for name, countries in REGION_BY_COUNTRY.items():
        if c in countries:
            return name
    return "unknown" if not c else "other"


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]", " ", (h or "").strip().lower()))


def map_columns(headers: list[str]) -> dict[str, str]:
    """Return {field: original_header} for the headers we recognise."""
    normalised = {_norm_header(h): h for h in headers if h}  # keys look like "work e mail"
    mapping = {}
    for field, names in ALIASES.items():
        for name in names:
            name = _norm_header(name)
            if name in normalised:
                mapping[field] = normalised[name]
                break
    return mapping


def read_rows(path: Path) -> tuple[list[str], list[dict]]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(h or "").strip() for h in rows[0]]
        data = [{h: ("" if v is None else str(v).strip()) for h, v in zip(headers, r)} for r in rows[1:]]
        return headers, data
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    data = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return headers, data


def import_file(path: Path, store: Store, source_detail: str = "") -> dict:
    headers, rows = read_rows(path)
    mapping = map_columns(headers)
    if "email" not in mapping and "full_name" not in mapping and "first_name" not in mapping:
        raise ValueError("No email or name column found. Headers seen: " + ", ".join(headers[:12]))

    index = store.match_index()
    emails, lkeys, names = index["emails"], index["linkedin"], index["names"]
    result = {"rows": len(rows), "new": 0, "duplicates": 0, "invalid": 0, "suppressed": 0,
              "with_email": 0, "file": path.name}

    for row in rows:
        def get(field: str) -> str:
            return row.get(mapping.get(field, ""), "").strip() if field in mapping else ""

        email = get("email").lower()
        full = get("full_name") or " ".join(x for x in (get("first_name"), get("last_name")) if x)
        if not email and not full:
            result["invalid"] += 1
            continue
        if email and "@" not in email:
            email = ""
        if email and store.is_suppressed(email):
            result["suppressed"] += 1
            continue

        lk = linkedin_key(get("linkedin_url"))
        name_key = (full.lower(), get("company").lower())
        if (email and email in emails) or (lk and lk in lkeys) or (full and name_key in names):
            result["duplicates"] += 1
            continue

        country = get("country")
        data = {
            "email": email or None,
            "first_name": get("first_name") or (full.split(" ")[0] if full else ""),
            "last_name": get("last_name"),
            "full_name": full,
            "title": get("title"), "company": get("company"),
            "linkedin_url": get("linkedin_url"), "linkedin_key": lk or None,
            "phone": get("phone"), "country": country,
            "region": region_for(country, get("region")),
            "segment": get("segment"), "source": "import", "source_detail": source_detail or path.name,
            "channel": "import", "lawful_basis": get("lawful_basis"), "status": "active",
        }
        store.insert_contact(data)
        emails.add(email) if email else None
        lkeys.add(lk) if lk else None
        names.add(name_key) if full else None
        result["new"] += 1
        if email:
            result["with_email"] += 1
    return result
