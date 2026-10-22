# apps/product_management_app/features/receipts_v2/weigh_slip.py
# -*- coding: utf-8 -*-
"""
The weigher's slip: what was weighed, signed by the weigher and the customer/supplier.

It shows what was weighed and, when the weigher has set one, the price per kg and the amount per product
(the cashier still confirms the price before paying). It carries NO payment and says so, so nobody
mistakes it for a payment receipt. Pure Python (80mm text, same layout family as the other receipts),
no Qt or database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .receipt_render import WIDTH, _center, _fmt_dt, _num, _rule, _wrap
from .receipt_models import BOLD_MARKER, ShopInfo

FULL = "full"            # legacy: every round
SUMMARY = "summary"      # legacy: totals per product
WEIGHING = "weighing"    # Option 1 - Weighing Receipt: product + total bags + total kg
VALUE = "value"          # Option 2 - Full Value Receipt: + price, amount and payment
RECEIPT_TYPES = (WEIGHING, VALUE)
PAYMENT = "payment"      # Option 3 - Payment Receipt: small, printed once there is a payment to show
RECEIPT_TITLES = {WEIGHING: "Weighing Receipt (Option 1)", VALUE: "Full Value Receipt (Option 2)",
                  PAYMENT: "Payment Receipt (Option 3)"}


def _kg(v) -> str:
    return f"{float(v or 0):,.2f}"


def _bags(v) -> str:
    f = float(v or 0)
    return f"{int(f)}" if f == int(f) else f"{f:g}"


def _two(left: str, right: str) -> str:
    left, right = str(left), str(right)
    shown = len(left.replace(BOLD_MARKER, "")) + len(right.replace(BOLD_MARKER, ""))      # bold marks print nothing
    return left + " " * max(WIDTH - shown, 1) + right


def _money(v) -> str:
    return f"{float(v or 0):,.0f}"


def _b(text: str) -> str:
    """Bold on the receipt printer (and ignored by the preview / the PDF fallback)."""
    return f"{BOLD_MARKER}{text}{BOLD_MARKER}"


def _header(shop: ShopInfo) -> List[str]:
    """Shop name and phone only - the address and e-mail are on the other receipts and only made this one long."""
    out = [_b(_center(shop.name))]
    if shop.phone:
        out.append(_center(shop.phone))
    return out


def _kv(label: str, value) -> str:
    return f"{label:<14}: {value}"


def _who(ticket: Dict[str, Any]) -> List[str]:
    role = "Supplier" if ticket.get("party_role") == "supplier" else "Customer"
    phone = f" ({ticket['party_phone']})" if ticket.get("party_phone") else ""
    out = _wrap(f"{role}: {ticket.get('party_name', '')}{phone}", WIDTH)
    if ticket.get("party_code"):
        out.append(f"Walk-in ID: {ticket['party_code']}")
    return out


def _when(ticket: Dict[str, Any]) -> str:
    return _fmt_dt(ticket.get("submitted_at") or ticket.get("created_at") or datetime.now())


def _lines_with_rounds(ticket: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ln for ln in ticket.get("lines", []) if ln.get("rounds")]


def _line_name(ln: Dict[str, Any]) -> str:
    return ln.get("product_name", "") + (f" ({ln['grade']})" if ln.get("grade") else "")


def _line_amount(ln: Dict[str, Any]):
    amount = ln.get("amount")
    if amount is None and ln.get("unit_price"):
        amount = float(ln["unit_price"]) * float(ln.get("kg") or 0)
    return amount


def render_weigh_receipt(ticket: Dict[str, Any], shop: ShopInfo, option: str = WEIGHING) -> str:
    """
    The weigher's two receipts (80mm text), kept short:
      'weighing' - Option 1: product + bags + kg, total bags, weigher signature.
      'value'    - Option 2: + price/kg and amount per product, total, paid and balance, two signatures.
    Product names and balances are bold. ticket is WeighService.get_ticket()'s dict.
    """
    value = option == VALUE
    out = _header(shop)
    out.append(_rule())
    out.append(_two(_b("WEIGHING RECEIPT") if value else ticket.get("ticket_no", ""),
                    ticket.get("ticket_no", "") if value else _when(ticket)))
    if value:
        out.append(_when(ticket))
    out += _who(ticket)
    out.append(f"Weigher: {ticket.get('weigher_name', '')}")
    out.append(_rule())

    lines = _lines_with_rounds(ticket)
    nw = WIDTH - (34 if value else 22)                      # room left for the product name
    if value:
        out.append(f"{'Product':<{nw}}{'Bags':>5}{'KG':>9}{'Price':>8}{'Amount':>12}")
    else:
        out.append(f"{'Product':<{nw}}{'Bags':>8}{'KG':>14}")
    for ln in lines:
        wrapped = _wrap(_line_name(ln), nw - 1)
        for extra in wrapped[:-1]:
            out.append(_b(extra))
        if value:
            out.append(_b(f"{wrapped[-1]:<{nw}}") + f"{_bags(ln.get('bags')):>5}{_money(ln.get('kg')):>9}"
                       f"{_money(ln.get('unit_price')):>8}{_money(_line_amount(ln)):>12}")
        else:
            out.append(_b(f"{wrapped[-1]:<{nw}}") + f"{_bags(ln.get('bags')):>8}{_money(ln.get('kg')):>14}")
    out.append(_rule())
    if value:
        pay = ticket.get("payment") or {}
        total = ticket.get("total_amount")
        out.append(_two("TOTAL:", f"N{_money(total)}"))
        out.append(_two("Paid:", f"N{_money(pay.get('paid'))}"))
        out.append(_b(_two("BALANCE:", f"N{_money(pay.get('balance', total))}")))
        out.append(f"Status: {pay.get('status', 'Unpaid')}")
        out += ["", "Cashier: ____________  Customer: ____________"]
    else:
        out.append(_two("TOTAL BAGS:", f"{_bags(ticket.get('total_bags'))}"))
        out += ["", "Weigher sign: ____________________"]
    out += [_center(shop.footer_note or "Thank you for trading with us"), ""]
    return "\n".join(out)


def render_payment_receipt(ticket: Dict[str, Any], shop: ShopInfo) -> str:
    """
    Option 3 - the small payment receipt for a weighing: each product with its amount, then what was paid and what is
    left. No rounds, no weights table. Product names and the balance are bold.
    """
    out = _header(shop)
    out.append(_rule())
    out.append(_two(_b("PAYMENT RECEIPT"), ticket.get("ticket_no", "")))
    out.append(f"Name: {ticket.get('party_name', '')}" + (f" [{ticket['party_code']}]" if ticket.get("party_code") else ""))
    out.append(_fmt_dt(datetime.now()))
    out.append(_rule())
    for ln in _lines_with_rounds(ticket):
        right = f"N{_money(_line_amount(ln))}"
        wrapped = _wrap(_line_name(ln), WIDTH - len(right) - 1)
        for extra in wrapped[:-1]:
            out.append(_b(extra))
        out.append(_b(wrapped[-1]) + " " * max(WIDTH - len(wrapped[-1]) - len(right), 1) + right)
        out.append(f"  {_bags(ln.get('bags'))} bags  {_money(ln.get('kg'))} kg")
    pay = ticket.get("payment") or {}
    total = pay.get("total", ticket.get("total_amount"))
    out += [_rule(), _two("Total:", f"N{_money(total)}"), _two("PAID:", f"N{_money(pay.get('paid'))}"),
            _b(_two("BALANCE:", f"N{_money(pay.get('balance', total))}")), f"Status: {pay.get('status', 'Unpaid')}",
            "", "Signature: ____________________", _center(shop.footer_note or "Thank you"), ""]
    return "\n".join(out)


def render_weigh_slip(ticket: Dict[str, Any], shop: ShopInfo, receipt_type: str = FULL) -> str:
    """
    ticket is WeighService.get_ticket()'s dict. receipt_type: 'weighing' / 'value' (the two weigher receipts), or
    the older 'full' (every round) / 'summary' slips still found on tickets saved before.
    """
    if receipt_type == PAYMENT:
        return render_payment_receipt(ticket, shop)
    if receipt_type in RECEIPT_TYPES:
        return render_weigh_receipt(ticket, shop, receipt_type)
    out: List[str] = []
    out += [_center(shop.name), ]
    for ln in _wrap(shop.address, WIDTH):
        if ln:
            out.append(_center(ln))
    if shop.phone:
        out.append(_center(shop.phone))
    out += [_rule(), _center("WEIGHING SLIP"), _center("(not a payment receipt)"), _rule()]

    out.append(_two("Ticket:", ticket.get("ticket_no", "")))
    out.append(_two("Date:", _fmt_dt(ticket.get("submitted_at") or ticket.get("created_at") or datetime.now())))
    role = "Supplier" if ticket.get("party_role") == "supplier" else "Customer"
    out.append(_two(f"{role}:", ""))
    for ln in _wrap(str(ticket.get("party_name", "")), WIDTH - 2):
        out.append("  " + ln)
    if ticket.get("party_phone"):
        out.append(_two("Phone:", ticket["party_phone"]))
    out.append(_two("Weighed by:", ticket.get("weigher_name", "")))
    out.append(_rule())

    for line in ticket.get("lines", []):
        title = line.get("product_name", "")
        extra = " / ".join(x for x in (line.get("area"), line.get("grade") and f"Grade {line['grade']}") if x)
        for ln in _wrap(title, WIDTH):
            out.append(ln)
        if extra:
            out.append("  " + extra)
        if receipt_type == FULL:
            out.append(f"  {'Round':<7}{'Bags':>8}{'Weight (kg)':>16}")
            for r in line.get("rounds", []):
                out.append(f"  {r['round_no']:<7}{_bags(r['bags']):>8}{_kg(r['weight_kg']):>16}")
            out.append("  " + "-" * (WIDTH - 2))
        out.append(_two(f"  Subtotal ({len(line.get('rounds', []))} rounds)",
                        f"{_bags(line.get('bags'))} bags  {_kg(line.get('kg'))} kg"))
        if line.get("unit_price"):
            out.append(_two("  Price per kg", _kg(line["unit_price"])))
            out.append(_two("  Amount", _kg(line.get("amount") or float(line["unit_price"]) * float(line.get("kg") or 0))))
        out.append("")

    out.append(_rule())
    out.append(_two("TOTAL BAGS:", _bags(ticket.get("total_bags"))))
    out.append(_two("TOTAL WEIGHT:", f"{_kg(ticket.get('total_kg'))} kg"))
    if ticket.get("total_amount"):
        out.append(_two("TOTAL AMOUNT:", _kg(ticket["total_amount"])))
    out.append(_rule())
    out.append(_center("Price is confirmed and payment"))
    out.append(_center("made by the cashier."))
    out += ["", "", "Weigher: ______________________", "", "",
            f"{role}: ______________________", "", _rule(), _center(shop.footer_note or ""), "", ""]
    return "\n".join(out)
