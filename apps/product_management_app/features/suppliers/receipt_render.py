# apps/product_management_app/features/receipts_v2/receipt_render.py
# -*- coding: utf-8 -*-
"""
Renders a ReceiptDoc to compact monospace text for an 80mm thermal printer.

MODE_DELIVERY -> products only, NO money      ("Delivery / Goods slip")
MODE_FULL     -> products + amounts + payments ("Receipt")

Pure Python - no Qt, no DB. Tuning: WIDTH is the character grid; lower it if
text wraps/clips, and change FONT_PT in receipt_print.py together.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from .receipt_models import (
    ReceiptDoc, ReceiptLine,
    MODE_DELIVERY, MODE_FULL,
    ADVANCE, PAYMENT,
)

WIDTH = 42
CURRENCY = "\u20a6"   # naira sign; change to "N " or "NGN " if it prints as a box


def _rule(ch: str = "-") -> str:
    return ch * WIDTH


def _center(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= WIDTH:
        return s[:WIDTH]
    pad = (WIDTH - len(s)) // 2
    return (" " * pad) + s


def _lr(left: str, right: str) -> str:
    left = str(left or "")
    right = str(right or "")
    gap = WIDTH - len(left) - len(right)
    if gap < 1:
        left = left[: max(0, WIDTH - len(right) - 1)]
        gap = WIDTH - len(left) - len(right)
        if gap < 1:
            gap = 1
    return f"{left}{' ' * gap}{right}"


def _wrap(text: str, width: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return [""]
    words, lines, cur = text.split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    out: List[str] = []
    for ln in lines:
        while len(ln) > width:
            out.append(ln[:width])
            ln = ln[width:]
        out.append(ln)
    return out or [""]


def _money(v) -> str:
    try:
        return f"{CURRENCY}{float(v or 0.0):,.2f}"
    except Exception:
        return f"{CURRENCY}0.00"


def _num(v) -> str:
    try:
        f = float(v or 0.0)
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"
    except Exception:
        return "0"


def _qty_label(line: ReceiptLine) -> str:
    bags = float(line.bags or 0)
    kg = float(line.kg or 0)
    if line.is_fertilizer:
        n = int(bags)
        return f"{n} bag" + ("s" if n != 1 else "")
    if bags > 0 and kg > 0:
        return f"{int(bags)}bag/{_num(kg)}kg"
    if bags > 0:
        n = int(bags)
        return f"{n} bag" + ("s" if n != 1 else "")
    return f"{_num(kg)}kg"


def _fmt_dt(dt) -> str:
    if not dt:
        return datetime.now().strftime("%d-%m-%Y %H:%M")
    try:
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return str(dt)


_DELIVERY_HEADINGS = {
    "sale": "DELIVERY NOTE",
    "purchase": "GOODS RECEIVED NOTE",
    "sale_return": "RETURN NOTE",
    "purchase_return": "RETURN NOTE",
}


def _heading(doc: ReceiptDoc, mode: str) -> str:
    if mode == MODE_DELIVERY:
        return _DELIVERY_HEADINGS.get(doc.doc_type, doc.title)
    return doc.title


def _header(doc: ReceiptDoc, mode: str) -> List[str]:
    out: List[str] = []
    if doc.shop.name:
        out.append(_center(doc.shop.name.upper()))
    if doc.shop.address:
        for ln in _wrap(doc.shop.address, WIDTH):
            out.append(_center(ln))
    if doc.shop.phone:
        out.append(_center(doc.shop.phone))
    if doc.shop.email:
        out.append(_center(doc.shop.email))
    out.append(_rule("="))
    out.append(_center(_heading(doc, mode).upper()))
    out.append(_rule("="))
    return out


def _meta(doc: ReceiptDoc) -> List[str]:
    out: List[str] = []
    out.append(_lr("Receipt:", doc.receipt_no))
    out.append(_lr("Date:", _fmt_dt(doc.when)))
    if doc.party.name:
        out.append(_lr("Name:", doc.party.name[: WIDTH - 8]))
    if doc.party.phone:
        out.append(_lr("Phone:", doc.party.phone))
    if doc.served_by:
        out.append(_lr("Served by:", doc.served_by))
    if doc.ledger:
        out.append(_lr("Ledger:", doc.ledger.title()))
    if doc.reference:
        out.append(_lr("Ref:", doc.reference))
    out.append(_rule())
    return out


def _items_delivery(doc: ReceiptDoc) -> List[str]:
    out: List[str] = [_lr("ITEM", "QTY"), _rule()]
    if not doc.lines:
        out.append(_center("(no items)"))
        out.append(_rule())
        return out
    for ln in doc.lines:
        qty = _qty_label(ln)
        name_w = WIDTH - len(qty) - 1
        name_lines = _wrap(ln.name, max(4, name_w))
        out.append(_lr(name_lines[0], qty))
        for extra in name_lines[1:]:
            out.append("  " + extra)
    out.append(_rule())
    return out


def _items_full(doc: ReceiptDoc) -> List[str]:
    out: List[str] = [_lr("ITEM", "AMOUNT"), _rule()]
    if not doc.lines:
        out.append(_center("(no items)"))
        out.append(_rule())
        return out
    for ln in doc.lines:
        for nl in _wrap(ln.name, WIDTH):
            out.append(nl)
        detail = f"  {_qty_label(ln)} @ {_num(ln.rate)}"
        out.append(_lr(detail, _money(ln.amount)))
    out.append(_rule())
    return out


def _totals(doc: ReceiptDoc) -> List[str]:
    out: List[str] = []
    out.append(_lr("Subtotal", _money(doc.subtotal)))
    if float(doc.discount or 0) > 0:
        out.append(_lr("Discount", "-" + _money(doc.discount)))
    out.append(_lr("TOTAL", _money(doc.total)))
    out.append(_lr("Paid", _money(doc.paid)))
    bal = doc.balance
    label = "Balance" if bal >= 0 else "Change"
    out.append(_lr(label, _money(abs(bal))))
    if doc.payments:
        methods = ", ".join(sorted({(p.method or "").strip()
                                    for p in doc.payments if (p.method or "").strip()}))
        if methods:
            out.append(_lr("Method", methods[: WIDTH - 8]))
    out.append(_rule())
    return out


def _footer(doc: ReceiptDoc, mode: str) -> List[str]:
    out: List[str] = [""]
    if mode == MODE_DELIVERY:
        out.append("Received by: ______________________")
        out.append("")
    if doc.note:
        for ln in _wrap(doc.note, WIDTH):
            out.append(_center(ln))
    if doc.shop.footer_note:
        out.append(_center(doc.shop.footer_note))
    out.append(_center(doc.receipt_no))
    out.append("")
    return out


def render_receipt(doc: ReceiptDoc, mode: str = MODE_FULL) -> str:
    """
    mode = MODE_DELIVERY -> items only, no money (Receipt type 1)
    mode = MODE_FULL     -> items + amounts + payments (Receipt type 2)
    Payment/Advance always render full (a delivery slip is meaningless there).
    """
    mode = (mode or MODE_FULL).strip().lower()
    if doc.doc_type in (PAYMENT, ADVANCE):
        mode = MODE_FULL

    parts: List[str] = []
    parts += _header(doc, mode)
    parts += _meta(doc)

    if mode == MODE_DELIVERY:
        parts += _items_delivery(doc)
    else:
        parts += _items_full(doc)
        if doc.lines:
            parts += _totals(doc)
        else:
            parts.append(_lr("Amount", _money(doc.total)))
            if doc.doc_type == PAYMENT:
                parts.append(_lr("Paid", _money(doc.paid)))
            parts.append(_rule())

    parts += _footer(doc, mode)
    return "\n".join(parts).rstrip() + "\n"