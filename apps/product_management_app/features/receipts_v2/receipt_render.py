# apps/product_management_app/features/receipts_v2/receipt_render.py
# -*- coding: utf-8 -*-
"""Classic Rafawa 80mm layout. Pure Python - no Qt, no DB."""

from __future__ import annotations

from datetime import datetime
from typing import List

from .receipt_models import (
    ReceiptDoc, ReceiptLine,
    MODE_DELIVERY, MODE_FULL,
    SALE, PURCHASE, ADVANCE, PAYMENT, SALE_RETURN, PURCHASE_RETURN,
    BOLD_MARKER,
)

WIDTH = 48

_C_NAME = 12
_C_QTY = 8
_C_RATE = 10
_C_TOTAL = WIDTH - _C_NAME - _C_QTY - _C_RATE

_LABEL_W = 8


def _rule() -> str:
    return "-" * WIDTH


def _center(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= WIDTH:
        return s[:WIDTH]
    pad = (WIDTH - len(s)) // 2
    return (" " * pad) + s


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


def _num(v) -> str:
    try:
        f = float(v or 0.0)
    except Exception:
        return "0"
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def _fmt_dt(dt) -> str:
    if not dt:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(dt)


def _ref_id(receipt_no: str) -> str:
    tail = (receipt_no or "").split("-")[-1]
    return tail.lstrip("0") or "0"


def _label(label: str, value: str) -> str:
    label = f"{label:<{_LABEL_W}}"
    value = str(value or "")
    avail = WIDTH - _LABEL_W - 3
    if len(value) > avail:
        value = value[:avail]
    return f"{label}: {value}"


def _qty_label(line: ReceiptLine) -> str:
    bags = float(line.bags or 0)
    kg = float(line.kg or 0)
    if line.is_fertilizer:
        return f"{int(bags)}bg"
    if kg > 0:
        return f"{_num(kg)}kg"
    if bags > 0:
        return f"{int(bags)}bg"
    return "-"


def _row4(a: str, b: str, c: str, d: str) -> str:
    a = a[:_C_NAME].ljust(_C_NAME)
    b = b[:_C_QTY].ljust(_C_QTY)
    c = c[:_C_RATE].rjust(_C_RATE)
    d = d[:_C_TOTAL].rjust(_C_TOTAL)
    return (a + b + c + d)[:WIDTH]


_DELIVERY_HEADINGS = {
    SALE: "DELIVERY NOTE",
    PURCHASE: "GOODS RECEIVED NOTE",
    SALE_RETURN: "RETURN NOTE",
    PURCHASE_RETURN: "RETURN NOTE",
}


def _title(doc: ReceiptDoc, mode: str):
    if mode == MODE_DELIVERY:
        return _DELIVERY_HEADINGS.get(doc.doc_type, doc.title.upper())
    if doc.doc_type in (SALE, PURCHASE):
        return None
    return doc.title.upper()


def _header(doc: ReceiptDoc, mode: str) -> List[str]:
    out: List[str] = []
    if doc.shop.name:
        out.append(_center(doc.shop.name))
    if doc.shop.address:
        for ln in _wrap(doc.shop.address, WIDTH):
            out.append(_center(ln))
    if doc.shop.phone:
        out.append(_center(doc.shop.phone))
    if doc.shop.email:
        out.append(_center(doc.shop.email))
    out.append(_rule())
    title = _title(doc, mode)
    if title:
        out.append(_center(title))
        out.append(_rule())
    return out


def _meta(doc: ReceiptDoc) -> List[str]:
    out: List[str] = []
    out.append(_label("Receipt", doc.receipt_no))
    if doc.party.name:
        out.append(_label("Name", doc.party.name))
    if doc.party.phone:
        out.append(_label("Phone", doc.party.phone))
    out.append(_label("Ref ID", _ref_id(doc.receipt_no)))
    if doc.served_by:
        out.append(_label("User", doc.served_by))
    if doc.reference:
        out.append(_label("Ref", doc.reference))
    out.append(_label("Date", _fmt_dt(doc.when)))
    out.append(_rule())
    return out


def _items_full(doc: ReceiptDoc) -> List[str]:
    out: List[str] = [_row4("Item", "Qty", "Rate", "Total"), _rule()]
    if not doc.lines:
        out.append(_center("(no items)"))
        out.append(_rule())
        return out
    # Supplier receipts specifically bold the product name - what the
    # supplier actually provided, made to stand out. Not applied to
    # other receipt types (sale, advance, payment), which stay exactly
    # as they were.
    bold_names = (doc.doc_type == PURCHASE)
    for ln in doc.lines:
        qty = _qty_label(ln)
        rate = _num(ln.rate)
        total = _num(ln.amount)
        if len(ln.name) <= _C_NAME:
            row = _row4(ln.name, qty, rate, total)
            if bold_names:
                name_part = ln.name[:_C_NAME].ljust(_C_NAME)
                row = f"{BOLD_MARKER}{name_part}{BOLD_MARKER}" + row[len(name_part):]
            out.append(row)
        else:
            for nl in _wrap(ln.name, WIDTH):
                out.append(f"{BOLD_MARKER}{nl}{BOLD_MARKER}" if bold_names else nl)
            out.append(_row4("", qty, rate, total))
    out.append(_rule())
    return out


def _items_delivery(doc: ReceiptDoc) -> List[str]:
    out: List[str] = []
    out.append("Item".ljust(WIDTH - 10) + "Qty".rjust(10))
    out.append(_rule())
    if not doc.lines:
        out.append(_center("(no items)"))
        out.append(_rule())
        return out
    for ln in doc.lines:
        qty = _qty_label(ln)
        name_lines = _wrap(ln.name, max(4, WIDTH - len(qty) - 1))
        out.append((name_lines[0].ljust(WIDTH - len(qty)) + qty)[:WIDTH])
        for extra in name_lines[1:]:
            out.append("  " + extra)
    out.append(_rule())
    return out


def _totals(doc: ReceiptDoc) -> List[str]:
    out: List[str] = []
    out.append(_label("Total", _num(doc.total)))
    out.append(_label("Discount", _num(doc.discount)))
    out.append(_label("Paid", _num(doc.paid)))
    due = doc.balance
    if due >= 0:
        out.append(_label("Due/Debt", _num(due)))
    else:
        out.append(_label("Change", _num(abs(due))))
    out.append(_rule())
    return out


def _footer(doc: ReceiptDoc, mode: str) -> List[str]:
    out: List[str] = [""]
    if mode == MODE_DELIVERY:
        out.append("Received by: ______________________")
        out.append("")
    out.append(f"Receipt No: {doc.receipt_no}")
    if doc.shop.footer_note:
        out.append(doc.shop.footer_note)
    out.append("")
    return out


def render_receipt(doc: ReceiptDoc, mode: str = MODE_FULL) -> str:
    mode = (mode or MODE_FULL).strip().lower()
    if doc.doc_type in (PAYMENT, ADVANCE):
        mode = MODE_FULL

    parts: List[str] = []
    parts += _header(doc, mode)
    parts += _meta(doc)

    if mode == MODE_DELIVERY:
        parts += _items_delivery(doc)
    else:
        if doc.lines:
            parts += _items_full(doc)
            parts += _totals(doc)
        else:
            parts.append(_label("Amount", _num(doc.total)))
            if doc.doc_type == PAYMENT:
                parts.append(_label("Paid", _num(doc.paid)))
            parts.append(_rule())

    parts += _footer(doc, mode)
    return "\n".join(parts).rstrip() + "\n"