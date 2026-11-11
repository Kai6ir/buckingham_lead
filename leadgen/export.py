"""Write the contact database, the outreach queue and the weekly list to Excel / CSV,
and (optionally) mirror the contacts to Google Sheets."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CONTACT_COLUMNS = [
    ("full_name", "Name"), ("title", "Title"), ("company", "Company"), ("email", "Email"),
    ("region", "Region"), ("country", "Country"), ("segment", "Segment"), ("source", "Source"),
    ("channel", "Found via"), ("tier", "Tier"), ("fit_score", "Fit score"), ("fit_reason", "Why"),
    ("lawful_basis", "Lawful basis"), ("status", "Status"), ("linkedin_url", "LinkedIn"),
    ("x_handle", "X handle"), ("domain", "Domain"), ("last_contacted_at", "Last contacted"),
    ("created_at", "Added"),
]

QUEUE_COLUMNS = [
    ("full_name", "Name"), ("company", "Company"), ("to_email", "To"), ("step", "Email #"),
    ("variant", "Variant"), ("status", "Status"), ("subject", "Subject"), ("body", "Body"),
    ("error", "Note"),
]

TIER_FILL = {"HOT": "F8CBAD", "WARM": "FFE699", "COLD": "DDEBF7"}


def _rows(rows: list[dict], columns: list[tuple[str, str]]) -> list[list]:
    return [[("" if r.get(k) is None else r.get(k)) for k, _ in columns] for r in rows]


def write_csv(rows: list[dict], path: Path, columns=CONTACT_COLUMNS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _, label in columns])
        writer.writerows(_rows(rows, columns))
    return path


def write_xlsx(rows: list[dict], path: Path, columns=CONTACT_COLUMNS, sheet: str = "Contacts") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    headers = [label for _, label in columns]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r, row in zip(rows, _rows(rows, columns)):
        ws.append(row)
        if r.get("tier") in TIER_FILL:
            fill = PatternFill("solid", fgColor=TIER_FILL[r["tier"]])
            for cell in ws[ws.max_row]:
                cell.fill = fill
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for idx, header in enumerate(headers, start=1):
        width = 60 if header in ("Why", "Body", "Subject") else 28 if header in ("Name", "Company", "Email", "To") else 16
        ws.column_dimensions[get_column_letter(idx)].width = width
    wb.save(path)
    return path


def write_queue_csv(rows: list[dict], path: Path) -> Path:
    return write_csv(rows, path, QUEUE_COLUMNS)


def write_queue_xlsx(rows: list[dict], path: Path) -> Path:
    return write_xlsx(rows, path, QUEUE_COLUMNS, sheet="Outbox")


def sync_google_sheet(rows: list[dict], spreadsheet_id: str, worksheet: str,
                      credentials_file: Path) -> int:
    """Overwrite the worksheet with the current contact list. Returns rows written."""
    try:
        import gspread
    except ImportError as exc:
        raise RuntimeError("gspread is not installed (pip install gspread google-auth)") from exc
    if not credentials_file.exists():
        raise RuntimeError(f"Google credentials not found: {credentials_file}")
    gc = gspread.service_account(filename=str(credentials_file))
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet, rows=max(100, len(rows) + 10), cols=len(CONTACT_COLUMNS))
    values = [[label for _, label in CONTACT_COLUMNS]] + [[str(v) for v in row] for row in _rows(rows, CONTACT_COLUMNS)]
    ws.clear()
    ws.update(values=values, range_name="A1")
    return len(rows)
