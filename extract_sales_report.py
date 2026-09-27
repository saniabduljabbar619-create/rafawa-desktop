#!/usr/bin/env python3
"""
extract_sales_report.py

Extracts every Sale for a given Party (customer/walk-in), together with
everything that happened against that Sale ID: cash/bank Payments, Advance
allocations used to pay it, and Sale Returns -- grouped by Sale ID and
ordered by date/time. Produces a two-sheet Excel workbook:

    1. "Summary"            - one row per Sale, with computed totals and
                               a Paid / Partial / Unpaid status.
    2. "Transaction Detail" - every payment / advance-allocation / return
                               row, each tagged with its Sale ID, so you can
                               trace exactly what makes up each sale's balance.

USAGE
-----
    python extract_sales_report.py --party "John Doe"
    python extract_sales_report.py --party-id 42
    python extract_sales_report.py --party "John" --from 2026-01-01 --to 2026-08-01
    python extract_sales_report.py --party "John" --db cloud
    python extract_sales_report.py --list-parties "john"     # search for a party id/name first

REQUIREMENTS
------------
    pip install sqlalchemy pymysql pandas openpyxl python-dotenv

CONFIG
------
By default the script looks for a file named ".env" (or "_env") in the same
folder, in the same KEY=VALUE format you already have, e.g.:

    DB_HOST=192.168.0.50
    DB_PORT=3306
    DB_NAME=rafawa_db
    DB_USER=rafawa22
    DB_PASSWORD=...

    CLOUD_DB_HOST=...
    CLOUD_DB_PORT=28933
    CLOUD_DB_NAME=defaultdb
    CLOUD_DB_USER=avnadmin
    CLOUD_DB_PASSWORD=...
    CLOUD_DB_SSL_CA=ca.pem

Use --env-file to point at a different path, and --db local|cloud to choose
which set of credentials to use (default: local).
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------------
# Styling constants for the grouped "Sale Statement" sheet
# ---------------------------------------------------------------------------
FONT_NAME = "Calibri"

TITLE_FONT = Font(name=FONT_NAME, size=12, bold=True, color="FFFFFF")
TITLE_FILL = PatternFill("solid", fgColor="1F4E78")

SECTION_FONT = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
SECTION_FILL = PatternFill("solid", fgColor="4472C4")

COLHEAD_FONT = Font(name=FONT_NAME, size=10, bold=True)
COLHEAD_FILL = PatternFill("solid", fgColor="D9E1F2")

BODY_FONT = Font(name=FONT_NAME, size=10)
TOTALS_LABEL_FONT = Font(name=FONT_NAME, size=10, bold=True)
TOTALS_FILL = PatternFill("solid", fgColor="F2F2F2")

STATUS_FILL = {
    "Paid": PatternFill("solid", fgColor="C6EFCE"),
    "Partial": PatternFill("solid", fgColor="FFEB9C"),
    "Unpaid": PatternFill("solid", fgColor="FFC7CE"),
    "Voided": PatternFill("solid", fgColor="D9D9D9"),
}
STATUS_FONT = {
    "Paid": Font(name=FONT_NAME, size=10, bold=True, color="006100"),
    "Partial": Font(name=FONT_NAME, size=10, bold=True, color="9C6500"),
    "Unpaid": Font(name=FONT_NAME, size=10, bold=True, color="9C0006"),
    "Voided": Font(name=FONT_NAME, size=10, bold=True, color="595959"),
}

THIN = Side(style="thin", color="BFBFBF")
THIN_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DIVIDER_BORDER = Border(bottom=Side(style="medium", color="1F4E78"))

STMT_WIDTH = 9  # widest sub-table (Products) -> columns A..I


def _fmt_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return ""
    try:
        return v.strftime("%Y-%m-%d %H:%M")
    except AttributeError:
        return str(v)


def _fmt_money(v):
    try:
        if v is None or pd.isna(v):
            return ""
        return round(float(v), 2)
    except (TypeError, ValueError):
        return ""


def _merge_row(ws, row, first_col, last_col, value, font, fill, align="left"):
    ws.merge_cells(start_row=row, start_column=first_col, end_row=row, end_column=last_col)
    cell = ws.cell(row=row, column=first_col, value=value)
    cell.font = font
    cell.alignment = Alignment(horizontal=align, vertical="center", indent=1)
    for c in range(first_col, last_col + 1):
        ws.cell(row=row, column=c).fill = fill
    return row + 1


def _write_table_header(ws, row, headers):
    for i in range(1, STMT_WIDTH + 1):
        h = headers[i - 1] if i <= len(headers) else ""
        cell = ws.cell(row=row, column=i, value=h)
        cell.font = COLHEAD_FONT
        cell.fill = COLHEAD_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    return row + 1


def _write_table_row(ws, row, values):
    for i in range(1, STMT_WIDTH + 1):
        v = values[i - 1] if i <= len(values) else ""
        cell = ws.cell(row=row, column=i, value=v)
        cell.font = BODY_FONT
        cell.border = THIN_BORDER
        if isinstance(v, (int, float)):
            cell.number_format = "#,##0.00"
            cell.alignment = Alignment(horizontal="right", vertical="center")
        else:
            cell.alignment = Alignment(horizontal="left", vertical="center")
    return row + 1


def load_env(path):
    """Minimal .env-style loader (handles the exact format of your _env file)."""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def build_engine(env, which="local"):
    if which == "cloud":
        host = env["CLOUD_DB_HOST"]
        port = env.get("CLOUD_DB_PORT", "3306")
        name = env["CLOUD_DB_NAME"]
        user = env["CLOUD_DB_USER"]
        # Password in the env file may already be percent-encoded (it is
        # in your local _env, but not necessarily in cloud) - encode safely.
        password = quote_plus(env["CLOUD_DB_PASSWORD"])
        ssl_ca = env.get("CLOUD_DB_SSL_CA")
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"
        connect_args = {}
        if ssl_ca and os.path.exists(ssl_ca):
            connect_args["ssl"] = {"ca": ssl_ca}
        return create_engine(url, connect_args=connect_args)
    else:
        host = env["DB_HOST"]
        port = env.get("DB_PORT", "3306")
        name = env["DB_NAME"]
        user = env["DB_USER"]
        raw_password = env["DB_PASSWORD"]
        # Your local _env stores the password ALREADY percent-encoded
        # (e.g. %40 for '@', %24 for '$'). If you paste in a raw password
        # instead, this still works because quote_plus is idempotent on
        # a string with no unsafe chars, but if yours is pre-encoded like
        # the sample, we pass it through as-is.
        password = raw_password
        charset = env.get("DB_CHARSET", "utf8mb4")
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset={charset}"
        return create_engine(url)


def resolve_party(engine, party_arg=None, party_id_arg=None):
    with engine.connect() as conn:
        if party_id_arg:
            row = conn.execute(
                text("SELECT id, display_name, party_type FROM parties WHERE id = :pid"),
                {"pid": party_id_arg},
            ).fetchone()
            if not row:
                sys.exit(f"No party found with id={party_id_arg}")
            return row.id, row.display_name

        if party_arg:
            rows = conn.execute(
                text(
                    "SELECT id, display_name, party_type FROM parties "
                    "WHERE display_name LIKE :q OR phone LIKE :q"
                ),
                {"q": f"%{party_arg}%"},
            ).fetchall()
            if not rows:
                sys.exit(f"No party found matching '{party_arg}'")
            if len(rows) > 1:
                print(f"Multiple parties match '{party_arg}':")
                for r in rows:
                    print(f"  id={r.id:<6} {r.display_name:<30} ({r.party_type})")
                sys.exit("Re-run with --party-id <id> to pick one.")
            return rows[0].id, rows[0].display_name

    sys.exit("You must pass --party <name/phone> or --party-id <id>.")


def list_parties(engine, query):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, display_name, party_type, phone FROM parties "
                "WHERE display_name LIKE :q OR phone LIKE :q ORDER BY display_name"
            ),
            {"q": f"%{query}%"},
        ).fetchall()
    if not rows:
        print(f"No parties found matching '{query}'.")
        return
    print(f"{'ID':<6}{'Name':<32}{'Type':<12}{'Phone':<15}")
    for r in rows:
        print(f"{r.id:<6}{(r.display_name or ''):<32}{(r.party_type or ''):<12}{(r.phone or ''):<15}")


def fetch_sales(engine, party_id, date_from=None, date_to=None):
    sql = """
        SELECT
            s.id                AS sale_id,
            s.sale_no,
            s.status             AS sale_status,
            s.subtotal,
            s.discount,
            s.total_amount,
            s.notes,
            s.created_at,
            s.completed_at,
            p.display_name       AS party_name,
            p.id                 AS party_id,
            u.username            AS created_by
        FROM sales s
        JOIN parties p ON p.id = s.buyer_party_id
        LEFT JOIN users u ON u.id = s.created_by_user_id
        WHERE s.buyer_party_id = :party_id
    """
    params = {"party_id": party_id}
    if date_from:
        sql += " AND s.created_at >= :date_from"
        params["date_from"] = date_from
    if date_to:
        sql += " AND s.created_at <= :date_to"
        params["date_to"] = date_to
    sql += " ORDER BY s.created_at ASC, s.id ASC"

    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def fetch_payments(engine, sale_ids):
    if not sale_ids:
        return pd.DataFrame()
    sql = """
        SELECT
            entity_id   AS sale_id,
            id          AS payment_id,
            direction,
            amount,
            method,
            status,
            notes,
            created_at
        FROM payments
        WHERE entity_type = 'sale' AND entity_id IN :sale_ids
        ORDER BY entity_id, created_at
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"sale_ids": tuple(sale_ids)})


def fetch_advance_allocations(engine, sale_ids):
    if not sale_ids:
        return pd.DataFrame()
    sql = """
        SELECT
            aa.entity_id      AS sale_id,
            aa.id             AS allocation_id,
            aa.advance_id,
            aa.amount,
            aa.created_at,
            a.direction        AS advance_direction,
            a.advance_type
        FROM advance_allocations aa
        JOIN advances a ON a.id = aa.advance_id
        WHERE aa.entity_type = 'sale' AND aa.entity_id IN :sale_ids
        ORDER BY aa.entity_id, aa.created_at
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"sale_ids": tuple(sale_ids)})


def fetch_items(engine, sale_ids):
    if not sale_ids:
        return pd.DataFrame()
    sql = """
        SELECT
            si.sale_id,
            si.id                    AS sale_item_id,
            pr.name                  AS product_name,
            si.product_type_snapshot,
            si.qty_bags,
            si.total_weight_kg,
            si.unit_sale_rate,
            si.unit_cost_snapshot,
            pr.default_cost_rate,
            si.line_total,
            si.line_discount,
            si.notes
        FROM sale_items si
        LEFT JOIN products pr ON pr.id = si.product_id
        WHERE si.sale_id IN :sale_ids
        ORDER BY si.sale_id, si.id
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"sale_ids": tuple(sale_ids)})


def fetch_returns(engine, sale_ids):
    if not sale_ids:
        return pd.DataFrame()
    sql = """
        SELECT
            sale_id,
            id            AS return_id,
            sale_item_id,
            product_id,
            qty_kg,
            qty_bags,
            amount,
            reason,
            created_at
        FROM sale_returns
        WHERE sale_id IN :sale_ids
        ORDER BY sale_id, created_at
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"sale_ids": tuple(sale_ids)})


def build_summary(sales_df, payments_df, advances_df, returns_df):
    rows = []
    for _, s in sales_df.iterrows():
        sid = s["sale_id"]

        pay = payments_df[payments_df["sale_id"] == sid] if not payments_df.empty else payments_df
        # exclude voided payments from totals
        pay_active = pay[pay["status"] == "completed"] if not pay.empty else pay
        cash_in = pay_active[pay_active["direction"] == "in"]["amount"].astype(float).sum() if not pay_active.empty else 0.0
        cash_methods = ", ".join(sorted(set(pay_active["method"].dropna()))) if not pay_active.empty else ""

        adv = advances_df[advances_df["sale_id"] == sid] if not advances_df.empty else advances_df
        advance_paid = adv["amount"].astype(float).sum() if not adv.empty else 0.0
        used_advance = advance_paid > 0

        ret = returns_df[returns_df["sale_id"] == sid] if not returns_df.empty else returns_df
        returns_total = ret["amount"].astype(float).sum() if not ret.empty else 0.0  # stored negative
        return_reasons = "; ".join(sorted(set(ret["reason"].dropna()))) if not ret.empty else ""

        total_amount = float(s["total_amount"] or 0)
        total_paid = cash_in + advance_paid
        # returns_total is negative, so it reduces what's actually owed
        net_due = total_amount + returns_total
        balance = net_due - total_paid

        if s["sale_status"] == "voided":
            pay_status = "Voided"
        elif balance <= 0.005:
            pay_status = "Paid"
        elif total_paid > 0:
            pay_status = "Partial"
        else:
            pay_status = "Unpaid"

        rows.append({
            "Sale ID": sid,
            "Sale No": s["sale_no"],
            "Party": s["party_name"],
            "Date": s["created_at"],
            "Completed At": s["completed_at"],
            "Sale Status": s["sale_status"],
            "Subtotal": float(s["subtotal"] or 0),
            "Discount": float(s["discount"] or 0),
            "Total Amount": total_amount,
            "Returns Total": returns_total,
            "Net Due": net_due,
            "Cash/Bank Paid": cash_in,
            "Payment Method(s)": cash_methods,
            "Paid via Advance": "Yes" if used_advance else "No",
            "Advance Amount Used": advance_paid,
            "Total Paid": total_paid,
            "Balance": balance,
            "Payment Status": pay_status,
            "Return Reason(s)": return_reasons,
            "Notes": s["notes"] or "",
            "Created By": s["created_by"],
        })

    return pd.DataFrame(rows)


def build_items(items_df):
    if items_df.empty:
        return pd.DataFrame(columns=[
            "Sale ID", "Sale Item ID", "Product", "Type", "Qty Bags", "Weight (kg)",
            "Sale Rate", "Cost Rate (snapshot/default)", "Line Total", "Line Discount",
            "Net Line Amount", "Est. Profit", "Notes",
        ])
    d = items_df.copy()
    # NULL unit_cost_snapshot on old rows -> COALESCE back to product's current default_cost_rate
    d["cost_rate_used"] = d["unit_cost_snapshot"].astype(float).where(
        d["unit_cost_snapshot"].notna(), d["default_cost_rate"].astype(float)
    )
    d["net_line_amount"] = d["line_total"].astype(float) - d["line_discount"].astype(float)

    # Profit basis differs by product type: crops priced/costed per kg, fertilizer per bag.
    def est_profit(row):
        cost_rate = row["cost_rate_used"]
        if pd.isna(cost_rate):
            return None
        if row["product_type_snapshot"] == "crops" and pd.notna(row["total_weight_kg"]):
            cost = float(cost_rate) * float(row["total_weight_kg"])
        else:
            cost = float(cost_rate) * float(row["qty_bags"] or 0)
        return round(row["net_line_amount"] - cost, 2)

    d["est_profit"] = d.apply(est_profit, axis=1)

    d = d.rename(columns={
        "sale_id": "Sale ID", "sale_item_id": "Sale Item ID", "product_name": "Product",
        "product_type_snapshot": "Type", "qty_bags": "Qty Bags", "total_weight_kg": "Weight (kg)",
        "unit_sale_rate": "Sale Rate", "cost_rate_used": "Cost Rate (snapshot/default)",
        "line_total": "Line Total", "line_discount": "Line Discount",
        "net_line_amount": "Net Line Amount", "est_profit": "Est. Profit", "notes": "Notes",
    })
    return d[[
        "Sale ID", "Sale Item ID", "Product", "Type", "Qty Bags", "Weight (kg)",
        "Sale Rate", "Cost Rate (snapshot/default)", "Line Total", "Line Discount",
        "Net Line Amount", "Est. Profit", "Notes",
    ]]


def write_summary_sheet(ws, summary_df):
    """Quick-scan overview: one row per sale, with the Payment Status
    column color-coded so problem sales (Unpaid/Partial) jump out."""
    ws.sheet_view.showGridLines = False
    headers = list(summary_df.columns)

    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=i, value=h)
        cell.font = COLHEAD_FONT
        cell.fill = COLHEAD_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    status_col = headers.index("Payment Status") + 1
    money_cols = {
        headers.index(c) + 1
        for c in ("Subtotal", "Discount", "Total Amount", "Returns Total", "Net Due",
                   "Cash/Bank Paid", "Advance Amount Used", "Total Paid", "Balance")
        if c in headers
    }

    for r, (_, row) in enumerate(summary_df.iterrows(), start=2):
        status = row["Payment Status"]
        for c, h in enumerate(headers, start=1):
            v = row[h]
            if c in money_cols:
                v = _fmt_money(v)
            elif isinstance(v, pd.Timestamp):
                v = _fmt_date(v)
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            if c in money_cols:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right")
        scell = ws.cell(row=r, column=status_col)
        scell.font = STATUS_FONT.get(status, BODY_FONT)
        scell.fill = STATUS_FILL.get(status, PatternFill(fill_type=None))
        scell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        max_len = max([len(str(h))] + [len(str(v)) for v in summary_df[h].astype(str).values[:200]])
        ws.column_dimensions[get_column_letter(i)].width = min(max_len + 2, 40)


def write_statement_sheet(ws, sales_df, items_df, payments_df, advances_df, returns_df, summary_df):
    """One sheet, grouped block-by-block per sale:
    Sale header -> Products -> Payments -> Advance Used -> Returns -> Totals.
    Each sale is visually separated with a colored title bar and a divider."""
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 22
    for i in range(3, STMT_WIDTH + 1):
        ws.column_dimensions[get_column_letter(i)].width = 15

    row = 1
    for _, s in sales_df.iterrows():
        sid = s["sale_id"]
        srow = summary_df[summary_df["Sale ID"] == sid].iloc[0]
        status = srow["Payment Status"]

        # ---- Sale header -------------------------------------------------
        title = (f"SALE {s['sale_no']}  (ID {sid})   |   {_fmt_date(s['created_at'])}   |   "
                 f"{s['party_name']}   |   Status: {status}")
        row = _merge_row(ws, row, 1, STMT_WIDTH, title, TITLE_FONT, TITLE_FILL)

        # ---- Products ------------------------------------------------
        sale_items = items_df[items_df["Sale ID"] == sid] if not items_df.empty else items_df
        if sale_items is not None and not sale_items.empty:
            row = _merge_row(ws, row, 1, STMT_WIDTH, "Products", SECTION_FONT, SECTION_FILL)
            row = _write_table_header(ws, row, [
                "Product", "Type", "Qty (Bags)", "Weight (kg)", "Rate",
                "Line Total", "Discount", "Net Amount", "Est. Profit",
            ])
            for _, it in sale_items.iterrows():
                row = _write_table_row(ws, row, [
                    it["Product"], it["Type"], it["Qty Bags"], it["Weight (kg)"],
                    _fmt_money(it["Sale Rate"]), _fmt_money(it["Line Total"]),
                    _fmt_money(it["Line Discount"]), _fmt_money(it["Net Line Amount"]),
                    _fmt_money(it["Est. Profit"]),
                ])

        # ---- Payments ------------------------------------------------
        pays = payments_df[payments_df["sale_id"] == sid] if not payments_df.empty else payments_df
        if pays is not None and not pays.empty:
            row = _merge_row(ws, row, 1, STMT_WIDTH, "Payments", SECTION_FONT, SECTION_FILL)
            row = _write_table_header(ws, row, ["Date", "Type", "Method", "Status", "Amount", "Notes"])
            for _, p in pays.iterrows():
                ptype = "Payment Received" if p["direction"] == "in" else "Refund/Payout"
                row = _write_table_row(ws, row, [
                    _fmt_date(p["created_at"]), ptype, p["method"] or "", p["status"] or "",
                    _fmt_money(p["amount"]), p["notes"] or "",
                ])

        # ---- Advance used for settlement -------------------------------
        advs = advances_df[advances_df["sale_id"] == sid] if not advances_df.empty else advances_df
        if advs is not None and not advs.empty:
            row = _merge_row(ws, row, 1, STMT_WIDTH, "Advance Used for Settlement", SECTION_FONT, SECTION_FILL)
            row = _write_table_header(ws, row, ["Date", "Advance Type", "Advance ID", "Allocation ID", "Amount"])
            for _, a in advs.iterrows():
                row = _write_table_row(ws, row, [
                    _fmt_date(a["created_at"]), a["advance_type"] or "", a["advance_id"],
                    a["allocation_id"], _fmt_money(a["amount"]),
                ])

        # ---- Returns ----------------------------------------------------
        rets = returns_df[returns_df["sale_id"] == sid] if not returns_df.empty else returns_df
        if rets is not None and not rets.empty:
            row = _merge_row(ws, row, 1, STMT_WIDTH, "Returns", SECTION_FONT, SECTION_FILL)
            row = _write_table_header(ws, row, ["Date", "Qty (Bags)", "Qty (kg)", "Amount", "Reason"])
            for _, r in rets.iterrows():
                row = _write_table_row(ws, row, [
                    _fmt_date(r["created_at"]), r["qty_bags"], r["qty_kg"],
                    _fmt_money(r["amount"]), r["reason"] or "",
                ])

        # ---- Totals -------------------------------------------------
        row = _merge_row(ws, row, 1, STMT_WIDTH, "Sale Totals", SECTION_FONT, SECTION_FILL)
        totals = [
            ("Subtotal", srow["Subtotal"]),
            ("Discount", srow["Discount"]),
            ("Total Amount", srow["Total Amount"]),
            ("Returns Total", srow["Returns Total"]),
            ("Net Due", srow["Net Due"]),
            ("Cash/Bank Paid", srow["Cash/Bank Paid"]),
            ("Advance Amount Used", srow["Advance Amount Used"]),
            ("Total Paid", srow["Total Paid"]),
            ("Balance", srow["Balance"]),
        ]
        for label, val in totals:
            lcell = ws.cell(row=row, column=1, value=label)
            lcell.font = TOTALS_LABEL_FONT
            vcell = ws.cell(row=row, column=2, value=_fmt_money(val))
            vcell.font = BODY_FONT
            vcell.number_format = "#,##0.00"
            vcell.alignment = Alignment(horizontal="right")
            for c in range(1, STMT_WIDTH + 1):
                ws.cell(row=row, column=c).fill = TOTALS_FILL
                ws.cell(row=row, column=c).border = THIN_BORDER
            row += 1

        # Payment status callout, color-coded
        scell_label = ws.cell(row=row, column=1, value="Payment Status")
        scell_label.font = TOTALS_LABEL_FONT
        scell = ws.cell(row=row, column=2, value=status)
        scell.font = STATUS_FONT.get(status, BODY_FONT)
        fill = STATUS_FILL.get(status, TOTALS_FILL)
        for c in range(1, STMT_WIDTH + 1):
            ws.cell(row=row, column=c).fill = fill
            ws.cell(row=row, column=c).border = THIN_BORDER
        row += 1

        # ---- Divider before next sale ------------------------------
        for c in range(1, STMT_WIDTH + 1):
            ws.cell(row=row, column=c).border = DIVIDER_BORDER
        row += 2  # blank spacer row


def main():
    ap = argparse.ArgumentParser(description="Extract Sales + Payments + Advances + Returns for a Party into Excel.")
    ap.add_argument("--party", help="Party name or phone (partial match)")
    ap.add_argument("--party-id", type=int, help="Party ID (exact)")
    ap.add_argument("--list-parties", metavar="QUERY", help="Search parties by name/phone and exit (no export)")
    ap.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD (inclusive)")
    ap.add_argument("--db", choices=["local", "cloud"], default="local", help="Which DB credentials to use (default: local)")
    ap.add_argument("--env-file", default=None, help="Path to env file (default: .env then _env in script folder)")
    ap.add_argument("--output", default=None, help="Output .xlsx path (default: sales_report_<party>_<date>.xlsx)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    env_path = args.env_file
    if not env_path:
        for candidate in (".env", "_env"):
            p = os.path.join(here, candidate)
            if os.path.exists(p):
                env_path = p
                break
    if not env_path or not os.path.exists(env_path):
        sys.exit("Could not find an env file. Pass one explicitly with --env-file /path/to/_env")

    env = load_env(env_path)
    engine = build_engine(env, which=args.db)

    if args.list_parties:
        list_parties(engine, args.list_parties)
        return

    if not args.party and not args.party_id:
        sys.exit("Pass --party <name/phone> or --party-id <id> (or use --list-parties <query> to search first).")

    party_id, party_name = resolve_party(engine, args.party, args.party_id)
    print(f"Party resolved: id={party_id} name={party_name!r}")

    sales_df = fetch_sales(engine, party_id, args.date_from, args.date_to)
    if sales_df.empty:
        print("No sales found for this party in the given range.")
        return

    sale_ids = sales_df["sale_id"].tolist()
    payments_df = fetch_payments(engine, sale_ids)
    advances_df = fetch_advance_allocations(engine, sale_ids)
    returns_df = fetch_returns(engine, sale_ids)
    items_raw_df = fetch_items(engine, sale_ids)

    summary_df = build_summary(sales_df, payments_df, advances_df, returns_df)
    items_df = build_items(items_raw_df)

    output = args.output
    if not output:
        safe_name = "".join(c if c.isalnum() else "_" for c in party_name)[:40]
        output = f"sales_report_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Summary"
    write_summary_sheet(summary_ws, summary_df)

    statement_ws = wb.create_sheet("Sale Statement")
    write_statement_sheet(statement_ws, sales_df, items_df, payments_df, advances_df, returns_df, summary_df)

    wb.save(output)

    print(f"Done. {len(summary_df)} sale(s) written to: {output}")
    print('  - "Summary": one row per sale, color-coded Payment Status for a quick scan.')
    print('  - "Sale Statement": each sale as its own block -> Products, Payments, '
          "Advance Used, Returns, Totals.")


if __name__ == "__main__":
    main()