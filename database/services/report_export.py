# database/services/report_export.py
"""Excel (.xlsx) export of the management reports, with proper formatting."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from database.services.report_service import AGE_BUCKETS, ReportService

_HEAD_FILL = PatternFill("solid", fgColor="0B2A24")
_HEAD_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FONT = Font(bold=True)
_THIN = Side(style="thin", color="D1D5DB")
_MONEY = '#,##0.00;[Red]-#,##0.00'
_Column = Tuple[str, str, str]          # (header, key, "text"|"money"|"qty"|"int")


def _write_table(ws, title: str, subtitle: str, columns: Sequence[_Column],
                 rows: Iterable[Dict[str, Any]], total_keys: Sequence[str] = ()) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = subtitle
    ws["A2"].font = Font(italic=True, color="6B7280")
    header_row = 4
    for c, (head, _key, _kind) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row, column=c, value=head)
        cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    rows = list(rows)
    r = header_row
    sums = {k: 0.0 for k in total_keys}
    for r, row in enumerate(rows, start=header_row + 1):
        for c, (_head, key, kind) in enumerate(columns, start=1):
            val = row.get(key)
            cell = ws.cell(row=r, column=c, value=val)
            cell.border = Border(bottom=_THIN)
            if kind == "money":
                cell.number_format = _MONEY
            elif kind == "qty":
                cell.number_format = "#,##0.00"
        for k in total_keys:
            sums[k] += float(row.get(k) or 0)
    if total_keys:
        r += 1
        ws.cell(row=r, column=1, value="TOTAL").font = _TOTAL_FONT
        for c, (_h, key, kind) in enumerate(columns, start=1):
            if key in sums:
                cell = ws.cell(row=r, column=c, value=sums[key])
                cell.font = _TOTAL_FONT
                cell.number_format = _MONEY if kind == "money" else "#,##0.00"
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    for c, (head, key, _k) in enumerate(columns, start=1):
        longest = max([len(head)] + [len(f"{rw.get(key, '')}") for rw in rows] + [12])
        ws.column_dimensions[get_column_letter(c)].width = min(max(longest + 2, 16 if c > 1 else 28), 60)


def _stamp(start, end) -> str:
    rng = "all dates" if not (start or end) else f"{start or '...'} to {end or '...'}"
    return f"Period: {rng}   |   Generated {datetime.now():%Y-%m-%d %H:%M}"


def export_reports(path: str, start: date = None, end: date = None, as_of: date = None) -> Path:
    """Write one workbook: Summary, Receivables, Payables, Stock valuation."""
    summary = ReportService.period_summary(start, end)
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    rows: List[Dict[str, Any]] = []
    sa, pu, ca, ad = summary["sales"], summary["purchases"], summary["cash"], summary["advances"]
    rows += [
        {"item": "Sales (documents)", "amount": sa["count"]},
        {"item": "Sales - gross", "amount": sa["gross"]},
        {"item": "Sales - returns", "amount": -sa["returns"]},
        {"item": "Sales - manual discounts", "amount": -sa["manual_discounts"]},
        {"item": "Sales - NET", "amount": sa["net"]},
        {"item": "Purchases (documents)", "amount": pu["count"]},
        {"item": "Purchases - gross", "amount": pu["gross"]},
        {"item": "Purchases - returns", "amount": -pu["returns"]},
        {"item": "Purchases - NET", "amount": pu["net"]},
        {"item": "Cash received", "amount": ca["in"]},
        {"item": "Cash paid out", "amount": -ca["out"]},
        {"item": "Net cash movement", "amount": ca["net"]},
        {"item": "Advances received", "amount": ad["received"]},
        {"item": "Advances given", "amount": -ad["given"]},
    ]
    _write_table(ws, "Period summary", _stamp(start, end),
                 [("Item", "item", "text"), ("Amount", "amount", "money")], rows)

    ws2 = wb.create_sheet("By cashier")
    _write_table(ws2, "By cashier", _stamp(start, end),
                 [("Cashier", "username", "text"), ("Sales", "sales", "money"), ("Purchases", "purchases", "money"),
                  ("Cash in", "cash_in", "money"), ("Cash out", "cash_out", "money")],
                 summary["by_cashier"], total_keys=("sales", "purchases", "cash_in", "cash_out"))

    ws3 = wb.create_sheet("By payment method")
    _write_table(ws3, "Cash by payment method", _stamp(start, end),
                 [("Method", "method", "text"), ("In", "in", "money"), ("Out", "out", "money"), ("Net", "net", "money")],
                 summary["by_method"], total_keys=("in", "out", "net"))

    aging_cols: List[_Column] = [("Party", "party", "text"), ("Documents", "documents", "int")]
    aging_cols += [(f"{label} days", label, "money") for label, _, _ in AGE_BUCKETS]
    aging_cols.append(("Total owed", "total", "money"))
    bucket_keys = tuple(label for label, _, _ in AGE_BUCKETS) + ("total",)
    for kind, name, title in (("sale", "Receivables", "Receivables - customers owe us"),
                              ("purchase", "Payables", "Payables - we owe suppliers")):
        w = wb.create_sheet(name)
        _write_table(w, title, f"As of {as_of or date.today()}   |   Generated {datetime.now():%Y-%m-%d %H:%M}",
                     aging_cols, ReportService.aging(kind, as_of), total_keys=bucket_keys)

    ws5 = wb.create_sheet("Stock valuation")
    _write_table(ws5, "Stock valuation at cost", f"Generated {datetime.now():%Y-%m-%d %H:%M}",
                 [("Product", "product", "text"), ("SKU", "sku", "text"), ("Bags", "bags", "qty"), ("Kg", "kg", "qty"),
                  ("Unit", "unit", "text"), ("Unit cost", "unit_cost", "money"), ("Value", "value", "money")],
                 ReportService.stock_valuation(), total_keys=("value",))

    out = Path(path)
    wb.save(out)
    return out
