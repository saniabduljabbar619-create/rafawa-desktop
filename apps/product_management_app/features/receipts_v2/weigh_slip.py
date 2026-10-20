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
from .receipt_models import ShopInfo

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
    gap = max(WIDTH - len(left) - len(right), 1)
    return left + " " * gap + right


def _money(v) -> str:
    return f"{float(v or 0):,.0f}"


def _header(shop: ShopInfo) -> List[str]:
    out = [_center(shop.name)]
    for ln in _wrap(shop.address, WIDTH):
        if ln:
            out.append(_center(ln))
    for x in (shop.phone, getattr(shop, "email", "")):
        if x:
            out.append(_center(x))
    return out


def _kv(label: str, value) -> str:
    return f"{label:<14}: {value}"


def render_weigh_receipt(ticket: Dict[str, Any], shop: ShopInfo, option: str = WEIGHING) -> str:
    """
    The weigher's two receipts (80mm text):
      'weighing' - Option 1: product + total bags + total kg per product, grand total bags, signatures.
      'value'    - Option 2: + price/kg, amount, total, payment status / paid / balance, cashier + customer signatures.
    ticket is WeighService.get_ticket()'s dict.
    """
    value = option == VALUE
    role = "Supplier" if ticket.get("party_role") == "supplier" else "Customer"
    out = _header(shop)
    out.append(_rule())
    if value:
        out += [_center("WEIGHING RECEIPT"), _rule()]
    out.append(_kv("Receipt No", ticket.get("ticket_no", "")))
    out.append(_kv(f"{role} Name", ticket.get("party_name", "")))
    if ticket.get("party_code"):
        out.append(_kv("Walk-in ID", ticket["party_code"]))
    out.append(_kv("Phone", ticket.get("party_phone", "") or "-"))
    out.append(_kv("Ref ID", ticket.get("id", "")))
    out.append(_kv("User", ticket.get("weigher_name", "")))
    out.append(_kv("Date", _fmt_dt(ticket.get("submitted_at") or ticket.get("created_at") or datetime.now())))
    out.append(_rule())

    lines = [ln for ln in ticket.get("lines", []) if ln.get("rounds")]
    if value:
        out.append(f"{'S/N':<4}{'Product':<11}{'Bags':>5}{'KG':>9}{'Price/KG':>9}{'Amount':>10}")
    else:
        out.append(f"{'S/N':<4}{'Product':<20}{'Total Bags':>12}{'Total KG':>12}")
    out.append(_rule())
    for i, ln in enumerate(lines, 1):
        name = ln.get("product_name", "")
        if ln.get("grade"):
            name += f" ({ln['grade']})"
        amount = ln.get("amount")
        if amount is None and ln.get("unit_price"):
            amount = float(ln["unit_price"]) * float(ln.get("kg") or 0)
        if value:
            wrapped = _wrap(name, 11)
            for extra in wrapped[:-1]:
                out.append(f"{'':<4}{extra}")
            out.append(f"{i:<4}{wrapped[-1]:<11}{_bags(ln.get('bags')):>5}{_money(ln.get('kg')):>9}"
                       f"{_money(ln.get('unit_price')):>9}{_money(amount):>10}")
        else:
            wrapped = _wrap(name, 19)
            for extra in wrapped[:-1]:
                out.append(f"{'':<4}{extra}")
            out.append(f"{i:<4}{wrapped[-1]:<20}{_bags(ln.get('bags')) + ' bags':>12}{_money(ln.get('kg')) + ' KG':>12}")
    out.append(_rule())
    if value:
        out.append(_two("TOTAL AMOUNT:", f"N{_money(ticket.get('total_amount'))}"))
        pay = ticket.get("payment") or {}
        out += ["", _kv("Payment Status", pay.get("status", "Unpaid")),
                _kv("Amount Paid", f"N{_money(pay.get('paid'))}"),
                _kv("Balance", f"N{_money(pay.get('balance', ticket.get('total_amount')))}"), _rule(),
                "", "Authorized By: ____________________", "(Cashier/Admin)", "",
                "Customer Signature: _______________"]
    else:
        out.append(_two("GRAND TOTAL BAGS:", f"{_bags(ticket.get('total_bags'))} bags"))
        out += ["", "Note: Total KG is shown per product. Grand",
                "Total KG is not provided on this receipt.", "", "",
                "Weigher Signature: _________________"]
    out += ["", _rule(), _center(shop.footer_note or "Thank you for trading with us"), "", ""]
    return "\n".join(out)


def render_payment_receipt(ticket: Dict[str, Any], shop: ShopInfo) -> str:
    """
    Option 3 - the small payment receipt for a weighing: each product with its amount, then what was paid and
    what is left. No rounds, no weights table. ticket is WeighService.get_ticket()'s dict.
    """
    out = [_center(shop.name)]
    if shop.phone:
        out.append(_center(shop.phone))
    out += [_rule(), _center("PAYMENT RECEIPT"), _rule()]
    out.append(_kv("Receipt No", ticket.get("ticket_no", "")))
    out.append(_kv("Name", ticket.get("party_name", "")))
    out.append(_kv("Date", _fmt_dt(datetime.now())))
    out.append(_rule())
    for ln in ticket.get("lines", []):
        if not ln.get("rounds"):
            continue
        amount = ln.get("amount")
        if amount is None and ln.get("unit_price"):
            amount = float(ln["unit_price"]) * float(ln.get("kg") or 0)
        name = ln.get("product_name", "") + (f" ({ln['grade']})" if ln.get("grade") else "")
        right = f"N{_money(amount)}"
        wrapped = _wrap(name, WIDTH - len(right) - 1)
        out += wrapped[:-1]
        out.append(_two(wrapped[-1], right))
        out.append(f"  {_bags(ln.get('bags'))} bags  {_money(ln.get('kg'))} kg")
    pay = ticket.get("payment") or {}
    total = pay.get("total", ticket.get("total_amount"))
    out += [_rule(), _two("Total:", f"N{_money(total)}"), _two("PAID:", f"N{_money(pay.get('paid'))}"),
            _two("Balance:", f"N{_money(pay.get('balance', total))}"), _kv("Status", pay.get("status", "Unpaid")),
            _rule(), "", "Signature: ____________________", "", _center(shop.footer_note or "Thank you"), ""]
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
