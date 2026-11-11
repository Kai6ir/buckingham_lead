"""The NFA firm list: the column layout of the master file, import and export.

The master file is an NFA (National Futures Association) list of registered firms. The bot keeps
exactly these 20 columns, in the same order, and appends its own columns after them. Exporting
gives back a workbook in the same layout, so the boss's list can be extended without reformatting.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SHEET_NAME = "NFA Master IB List"

# (database column, header exactly as in the master file)
MASTER_COLUMNS = [
    ("firm_id", "FIRM ID"),
    ("firm_name", "FIRM NAME"),
    ("city", "CITY"),
    ("us_state", "US STATE DESCRIPTION"),
    ("province", "PROVINCE"),
    ("country", "COUNTRY DESCRIPTION"),
    ("nfa_member", "NFA MEMBER"),
    ("fcm", "FUTURES COMMISSION MERCHANT"),
    ("introducing_broker", "INTRODUCING BROKER"),
    ("cta", "COMMODITY TRADING ADVISOR"),
    ("cpo", "COMMODITY POOL OPERATOR"),
    ("rfed", "RETAIL FOREIGN EXCHANGE DEALER"),
    ("forex_dealer", "FOREX DEALER MEMBER"),
    ("forex_firm", "FOREX FIRM"),
    ("swap_dealer", "SWAP DEALER"),
    ("msp", "MAJOR SWAP PARTICIPANT"),
    ("swap_firm", "SWAP FIRM"),
    ("floor_trader", "FLOOR TRADER FIRM"),
    ("guarantor_id", "GUARANTOR FIRM ID"),
    ("guarantor_name", "GUARANTOR NAME"),
]

# Columns the bot adds after the master columns.
EXTRA_COLUMNS = [
    ("website", "WEBSITE"),
    ("contact_name", "CONTACT NAME"),
    ("contact_title", "CONTACT TITLE"),
    ("contact_email", "CONTACT EMAIL"),
    ("contact_phone", "CONTACT PHONE"),
    ("linkedin_url", "LINKEDIN"),
    ("source_url", "SOURCE URL"),
    ("source_query", "SOURCE QUERY"),
    # ---- IB classification (does this introducing broker raise capital for CTAs?) ----
    ("ib_type", "IB TYPE"),                  # capital_raiser | hedger | execution_only | uncertain
    ("raises_for_cta", "RAISES FOR CTA"),    # yes | no | unclear
    ("hedging_only", "HEDGING ONLY"),        # yes | no
    ("mf_relevance", "MF RELEVANCE"),        # high | medium | low
    ("client_segment", "CLIENT SEGMENT"),    # e.g. "HNW / Institutional", "Ag / Commercial"
    ("recommendation", "RECOMMENDATION"),    # High-priority review | Review | Exclude
    ("lead_quality", "LEAD QUALITY"),        # qualified | uncertain | irrelevant (blank = not screened)
    ("decision", "DECISION (MANUAL)"),       # keep | exclude (set by a person; overrides the bot)
    ("evidence_text", "EVIDENCE"),           # website excerpt that supports the classification
    ("evidence_url", "EVIDENCE URL"),        # page the evidence came from
    ("lead_score", "LEAD SCORE"),
    ("tier", "TIER"),
    ("score_reasons", "SCORE REASONS"),
    ("outreach_status", "OUTREACH STATUS"),
    ("email_subject", "EMAIL SUBJECT"),
    ("email_body", "EMAIL BODY"),
    ("date_added", "DATE ADDED"),
    ("date_verified", "DATE VERIFIED"),
]

ALL_COLUMNS = MASTER_COLUMNS + EXTRA_COLUMNS
DB_COLUMNS = [db for db, _ in ALL_COLUMNS]
FLAG_COLUMNS = ["fcm", "introducing_broker", "cta", "cpo", "rfed", "forex_dealer", "forex_firm",
                "swap_dealer", "msp", "swap_firm", "floor_trader"]

# Words that tell us which registration a web page describes.
CATEGORY_KEYWORDS = {
    "cta": [r"commodity trading advisor", r"\bCTA\b", r"trading advisor"],
    "cpo": [r"commodity pool operator", r"\bCPO\b", r"pool operator"],
    "introducing_broker": [r"introducing broker", r"\bIB\b"],
    "fcm": [r"futures commission merchant", r"\bFCM\b"],
    "swap_dealer": [r"swap dealer"],
    "forex_firm": [r"retail forex", r"forex dealer", r"\bforex\b"],
}


def firm_key(firm_id: str, name: str, city: str) -> str:
    """Stable identity for a firm: the NFA firm ID when we have it, else normalised name + city."""
    fid = clean_id(firm_id)
    if fid:
        return f"nfa:{fid}"
    return "name:" + re.sub(r"[^a-z0-9]", "", (name or "").lower()) + "|" + re.sub(r"[^a-z0-9]", "", (city or "").lower())


def clean_id(value) -> str:
    """Excel stores IDs as floats (556980.0). Keep digits only."""
    if value is None:
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    return text if re.fullmatch(r"\d{2,10}", text) else ""


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def parse_master(path: Path) -> list[dict]:
    """Read a master workbook into dicts keyed by database column. Unknown headers are ignored."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [_text(h) for h in rows[0]]
    index = {header: i for i, header in enumerate(headers)}
    missing = [h for _, h in MASTER_COLUMNS if h not in index]
    if missing:
        raise ValueError("Not a NFA master list — missing columns: " + ", ".join(missing[:5]))
    out = []
    for row in rows[1:]:
        if not row or row[index["FIRM ID"]] is None and not _text(row[index["FIRM NAME"]]):
            continue
        rec = {}
        for db, header in MASTER_COLUMNS:
            value = row[index[header]] if index[header] < len(row) else None
            rec[db] = clean_id(value) if db in ("firm_id", "guarantor_id") else _text(value)
        out.append(rec)
    return out


def is_pending(value: str) -> bool:
    return "PENDING" in (value or "").upper()


def registrations(firm: dict) -> list[str]:
    """Human-readable list of registrations a firm holds, e.g. ['CTA', 'Introducing broker']."""
    labels = {"fcm": "FCM", "introducing_broker": "Introducing broker", "cta": "CTA", "cpo": "CPO",
              "rfed": "Retail FX dealer", "forex_dealer": "Forex dealer", "forex_firm": "Forex firm",
              "swap_dealer": "Swap dealer", "msp": "Major swap participant", "swap_firm": "Swap firm",
              "floor_trader": "Floor trader"}
    return [label for key, label in labels.items() if _text(firm.get(key))]


def location(firm: dict) -> str:
    parts = [firm.get("city"), firm.get("us_state") or firm.get("province"), firm.get("country")]
    return ", ".join(p.title() if p and p.isupper() else (p or "") for p in parts if p)


def quality_bucket(firm: dict) -> str:
    """Which export bucket a firm belongs to. A person's manual decision overrides the bot."""
    decision = (firm.get("decision") or "").strip().lower()
    if decision == "keep":
        return "qualified"
    if decision == "exclude":
        return "irrelevant"
    quality = (firm.get("lead_quality") or "").strip().lower()
    if quality in ("qualified", "irrelevant"):
        return quality
    return "uncertain"  # uncertain, or not screened yet — needs a human look either way


def _fill_sheet(ws, rows: list[dict]) -> None:
    headers = [h for _, h in ALL_COLUMNS]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864" if cell.column <= len(MASTER_COLUMNS) else "2E5AAC")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    tier_fill = {"A": "F8CBAD", "B": "FFE699", "C": "DDEBF7"}
    for r in rows:
        values = []
        for db, _ in ALL_COLUMNS:
            v = r.get(db)
            if db in ("firm_id", "guarantor_id") and v:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    pass
            values.append("" if v is None else v)
        ws.append(values)
        if r.get("tier") in tier_fill:
            for cell in ws[ws.max_row]:
                cell.fill = PatternFill("solid", fgColor=tier_fill[r["tier"]])
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    for idx, (db, header) in enumerate(ALL_COLUMNS, start=1):
        width = 34 if db in ("firm_name", "email_body", "score_reasons", "source_url") else 16
        ws.column_dimensions[get_column_letter(idx)].width = width


def write_master(rows: list[dict], path: Path, sheet: str = SHEET_NAME) -> Path:
    """Write firms in the master layout: the 20 NFA columns, then the bot's columns.

    The first sheet keeps every firm in the boss's layout. Three more sheets separate the
    qualified capital-raising IBs from the uncertain ones and the hedging / execution-only
    exclusions, so the review piles never mix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    _fill_sheet(ws, rows)
    buckets = {"qualified": [], "uncertain": [], "irrelevant": []}
    for r in rows:
        buckets[quality_bucket(r)].append(r)
    for title, key in (("Qualified IBs", "qualified"), ("Needs review", "uncertain"),
                       ("Excluded (hedging-exec)", "irrelevant")):
        _fill_sheet(wb.create_sheet(title), buckets[key])
    wb.save(path)
    return path


def default_export_name() -> str:
    return f"NFA_Master_IB_List_updated_{date.today().isoformat()}.xlsx"
