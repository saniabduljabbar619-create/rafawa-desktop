# apps/product_management_app/features/receipts_v2/closing_slip.py
# -*- coding: utf-8 -*-
"""
Customer Closing Statement slip (80mm): itemized closing calculation of
Purchases, Sales, Advances, and Payments - printed in Hausa or English.

Pure text, no Qt, no database: `render_closing_slip(data, shop, lang, include_items)`
takes the dict produced by `ClosingService.compute_closing()` or `ClosingService.get_closing_by_id()`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .receipt_models import BOLD_MARKER, ShopInfo
from .receipt_render import WIDTH, _center, _wrap


def _rule(char: str = "-") -> str:
    return char * WIDTH

LANGUAGES = {"ha": "Hausa", "en": "English"}
DEFAULT_LANG = "ha"
MAX_LISTED = 8

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "CUSTOMER CLOSING STATEMENT",
        "closing_no": "Closing No",
        "date": "Date",
        "customer": "Customer",
        "phone": "Phone",
        "period": "Period",
        "opening_bal": "Opening Balance",
        "purchases": "Purchases (Supplied)",
        "sales": "Sales (Bought)",
        "adv_out": "Advance OUT (Given)",
        "adv_in": "Advance IN (Received)",
        "pay_in": "Payments IN (Received)",
        "pay_out": "Payments OUT (Paid)",
        "returns": "Returns & Adjustments",
        "net_change": "Net Period Change",
        "closing_bal": "CLOSING BALANCE",
        "customer_owes": "CUSTOMER OWES US",
        "business_owes": "WE OWE CUSTOMER",
        "settled": "ALL SETTLED (NO DEBT)",
        "itemized_header": "PERIOD ACTIVITY BREAKDOWN",
        "purchases_hdr": "Purchases in Period",
        "sales_hdr": "Sales in Period",
        "advances_hdr": "Advances in Period",
        "payments_hdr": "Payments in Period",
        "and_more": "...and {n} more items",
        "cust_sign": "Customer Sign",
        "officer_sign": "Closed By / Sign",
        "thanks": "Thank you for your partnership.",
    },
    "ha": {
        "title": "TAKARDAR RUFE LISSAFIN ABOKIN CINIKI",
        "closing_no": "Lambar Rufewa",
        "date": "Kwanan wata",
        "customer": "Abokin ciniki",
        "phone": "Lambar waya",
        "period": "Lokacin Lissafi",
        "opening_bal": "Bude Balance (Farko)",
        "purchases": "Siyayya (Kayan da aka Kawo)",
        "sales": "Sayarwa (Kayan da aka Saya)",
        "adv_out": "Kudin Gaba da aka Ba shi (Out)",
        "adv_in": "Kudin Gaba da aka Karba (In)",
        "pay_in": "Kudin da aka Karba (Biya In)",
        "pay_out": "Kudin da aka Biya shi (Out)",
        "returns": "Kayan da aka Mayar",
        "net_change": "Canjin Wannan Lokaci",
        "closing_bal": "RUFE BALANCE (KARSHE)",
        "customer_owes": "YANA BIN MU BASHI",
        "business_owes": "MUNA BIN SHI BASHI",
        "settled": "BABU BASHI (AN KAMMALA)",
        "itemized_header": "BAYANIN AYYUKAN WANNAN LOKACI",
        "purchases_hdr": "Siyayyar Wannan Lokaci",
        "sales_hdr": "Sayarwar Wannan Lokaci",
        "advances_hdr": "Kudaden Gaba na Wannan Lokaci",
        "payments_hdr": "Biyan Kudade na Wannan Lokaci",
        "and_more": "...da wasu {n} kuma",
        "cust_sign": "Sa hannun abokin ciniki",
        "officer_sign": "Sa hannun ma'aikaci",
        "thanks": "Mun gode da kyakkyawar hulda.",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kw) if kw else text


def _money(v) -> str:
    return "N" + f"{float(v or 0):,.2f}"


def _b(text: str) -> str:
    return f"{BOLD_MARKER}{text}{BOLD_MARKER}"


def _two(left: str, right: str) -> str:
    shown = len(left.replace(BOLD_MARKER, "")) + len(right.replace(BOLD_MARKER, ""))
    return left + " " * max(WIDTH - shown, 1) + right


def _fmt_period(start_dt, end_dt) -> str:
    s = start_dt.strftime("%d/%m/%Y") if hasattr(start_dt, "strftime") else str(start_dt or "")[:10]
    e = end_dt.strftime("%d/%m/%Y") if hasattr(end_dt, "strftime") else str(end_dt or "")[:10]
    return f"{s} -> {e}"


def render_closing_slip(
    data: Dict[str, Any],
    shop: ShopInfo,
    lang: str = DEFAULT_LANG,
    include_items: bool = True,
) -> str:
    """Renders an 80mm printable thermal closing slip."""
    lines: List[str] = []

    # Header / Shop branding
    lines.append(_b(_center(shop.name or "RAFAWA ENTERPRISES")))
    if getattr(shop, "address", None):
        for a in _wrap(shop.address, WIDTH):
            lines.append(_center(a))
    phone = getattr(shop, "phone", None) or (" / ".join(getattr(shop, "phones", [])) if getattr(shop, "phones", None) else "")
    if phone:
        lines.append(_center(phone))
    lines.append(_rule("="))

    # Title
    lines.append(_b(_center(tr(lang, "title"))))
    lines.append(_rule("="))

    # Metadata
    closing_no = data.get("closing_no") or "CLS-DRAFT"
    lines.append(_two(tr(lang, "closing_no") + ":", _b(closing_no)))
    if data.get("period_end"):
        lines.append(_two(tr(lang, "date") + ":", str(data.get("period_end"))[:16]))

    cust_name = data.get("party_name") or "Customer"
    lines += _wrap(f"{tr(lang, 'customer')}: {cust_name}", WIDTH)
    if data.get("party_phone"):
        lines.append(_two(tr(lang, "phone") + ":", data["party_phone"]))

    p_str = _fmt_period(data.get("period_start"), data.get("period_end"))
    lines.append(_two(tr(lang, "period") + ":", p_str))
    lines.append(_rule("-"))

    # Financial Summary
    lines.append(_two(tr(lang, "opening_bal") + ":", _money(data.get("opening_balance", 0))))
    lines.append(_two(tr(lang, "purchases") + ":", _money(data.get("total_purchases", 0))))
    lines.append(_two(tr(lang, "sales") + ":", _money(data.get("total_sales", 0))))
    lines.append(_two(tr(lang, "adv_out") + ":", _money(data.get("total_advance_out", 0))))
    lines.append(_two(tr(lang, "adv_in") + ":", _money(data.get("total_advance_in", 0))))
    lines.append(_two(tr(lang, "pay_in") + ":", _money(data.get("total_payments_in", 0))))
    lines.append(_two(tr(lang, "pay_out") + ":", _money(data.get("total_payments_out", 0))))
    if float(data.get("total_returns") or 0) > 0.005:
        lines.append(_two(tr(lang, "returns") + ":", _money(data.get("total_returns", 0))))

    lines.append(_rule("-"))
    lines.append(_b(_two(tr(lang, "net_change") + ":", _money(data.get("net_period_change", 0)))))
    lines.append(_rule("="))

    # Final Closing Balance
    bal = float(data.get("closing_balance") or 0)
    lines.append(_b(_center(tr(lang, "closing_bal"))))
    lines.append(_b(_center(_money(bal))))

    if bal > 0.005:
        state_str = tr(lang, "customer_owes")
    elif bal < -0.005:
        state_str = tr(lang, "business_owes")
    else:
        state_str = tr(lang, "settled")
    lines.append(_center(f"[{state_str}]"))
    lines.append(_rule("="))

    # Optional Itemized Breakdown
    details = data.get("details") or {}
    if include_items and details:
        purchases = details.get("purchases") or []
        sales = details.get("sales") or []
        adv_out = details.get("advance_out") or []
        adv_in = details.get("advance_in") or []
        pay_in = details.get("payments_in") or []
        pay_out = details.get("payments_out") or []

        has_items = any([purchases, sales, adv_out, adv_in, pay_in, pay_out])
        if has_items:
            lines.append(_b(_center(tr(lang, "itemized_header"))))
            lines.append(_rule("-"))

            if purchases:
                lines.append(_b(tr(lang, "purchases_hdr") + f" ({len(purchases)}):"))
                for p in purchases[:MAX_LISTED]:
                    ref = p.get("ref") or f"PUR-{p.get('id')}"
                    lines.append(_two(f" {ref} {p.get('date', '')[:10]}", _money(p.get("total_amount", 0))))
                if len(purchases) > MAX_LISTED:
                    lines.append(" " + tr(lang, "and_more", n=len(purchases) - MAX_LISTED))

            if sales:
                lines.append(_b(tr(lang, "sales_hdr") + f" ({len(sales)}):"))
                for s in sales[:MAX_LISTED]:
                    ref = s.get("ref") or f"SAL-{s.get('id')}"
                    lines.append(_two(f" {ref} {s.get('date', '')[:10]}", _money(s.get("total_amount", 0))))
                if len(sales) > MAX_LISTED:
                    lines.append(" " + tr(lang, "and_more", n=len(sales) - MAX_LISTED))

            all_adv = adv_out + adv_in
            if all_adv:
                lines.append(_b(tr(lang, "advances_hdr") + f" ({len(all_adv)}):"))
                for a in all_adv[:MAX_LISTED]:
                    tag = "OUT" if a.get("direction") == "given" else "IN"
                    ref = a.get("ref") or f"ADV-{a.get('id')}"
                    lines.append(_two(f" {ref} ({tag})", _money(a.get("amount", 0))))
                if len(all_adv) > MAX_LISTED:
                    lines.append(" " + tr(lang, "and_more", n=len(all_adv) - MAX_LISTED))

            all_pay = pay_in + pay_out
            if all_pay:
                lines.append(_b(tr(lang, "payments_hdr") + f" ({len(all_pay)}):"))
                for pm in all_pay[:MAX_LISTED]:
                    tag = "IN" if pm.get("direction") == "in" else "OUT"
                    ref = pm.get("ref") or f"PAY-{pm.get('id')}"
                    lines.append(_two(f" {ref} ({tag})", _money(pm.get("amount", 0))))
                if len(all_pay) > MAX_LISTED:
                    lines.append(" " + tr(lang, "and_more", n=len(all_pay) - MAX_LISTED))

            lines.append(_rule("-"))

    # Notes if any
    notes = data.get("notes")
    if notes:
        for n in _wrap(f"Note: {notes}", WIDTH):
            lines.append(n)
        lines.append(_rule("-"))

    # Signatures
    lines.append("")
    lines.append(_two(tr(lang, "cust_sign") + ":", "_________________"))
    lines.append("")
    lines.append(_two(tr(lang, "officer_sign") + ":", "_________________"))
    lines.append("")

    lines.append(_center(tr(lang, "thanks")))
    lines.append("")
    lines.append("")

    return "\n".join(lines)
