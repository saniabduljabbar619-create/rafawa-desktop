# apps/product_management_app/features/receipts_v2/weigh_slip.py
# -*- coding: utf-8 -*-
"""
The weigher's slip: what was weighed, signed by the weigher and the customer/supplier.

It carries NO prices and NO payment - the weigher has no authority over either - and says so, so
nobody mistakes it for a payment receipt. Pure Python (80mm text, same layout family as the
other receipts), no Qt or database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .receipt_render import WIDTH, _center, _fmt_dt, _num, _rule, _wrap
from .receipt_models import ShopInfo

FULL = "full"
SUMMARY = "summary"


def _kg(v) -> str:
    return f"{float(v or 0):,.2f}"


def _bags(v) -> str:
    f = float(v or 0)
    return f"{int(f)}" if f == int(f) else f"{f:g}"


def _two(left: str, right: str) -> str:
    left, right = str(left), str(right)
    gap = max(WIDTH - len(left) - len(right), 1)
    return left + " " * gap + right


def render_weigh_slip(ticket: Dict[str, Any], shop: ShopInfo, receipt_type: str = FULL) -> str:
    """ticket is WeighService.get_ticket()'s dict. receipt_type: 'full' (every round) or 'summary'."""
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
        out.append("")

    out.append(_rule())
    out.append(_two("TOTAL BAGS:", _bags(ticket.get("total_bags"))))
    out.append(_two("TOTAL WEIGHT:", f"{_kg(ticket.get('total_kg'))} kg"))
    out.append(_rule())
    out.append(_center("Price and payment are confirmed"))
    out.append(_center("by the cashier."))
    out += ["", "", "Weigher: ______________________", "", "",
            f"{role}: ______________________", "", _rule(), _center(shop.footer_note or ""), "", ""]
    return "\n".join(out)
