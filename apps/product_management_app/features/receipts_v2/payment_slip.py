# apps/product_management_app/features/receipts_v2/payment_slip.py
# -*- coding: utf-8 -*-
"""
The small payment receipt: one payment, for the product(s) of the one purchase/sale it settles.

Deliberately short (80mm): who paid/was paid, which document and product, how much now, what is left.
`build_payment_slip` reads the database; `render_payment_slip` is pure text.
"""
from __future__ import annotations

from typing import Any, Dict, List

from database.models.payment import Payment
from database.models.party import Party
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.user import User
from database.services.document_balance import document_balances
from database.services.walkin_account import is_shared_walkin
from database.session import get_session

from .receipt_models import BOLD_MARKER, ShopInfo
from .receipt_render import WIDTH, _center, _fmt_dt, _rule, _wrap


def _money(v) -> str:
    return f"{float(v or 0):,.2f}"


def _bags(v) -> str:
    f = float(v or 0)
    return f"{int(f)}" if f == int(f) else f"{f:g}"


def _two(left: str, right: str) -> str:
    left, right = str(left), str(right)
    shown = len(left.replace(BOLD_MARKER, "")) + len(right.replace(BOLD_MARKER, ""))      # bold marks print nothing
    return left + " " * max(WIDTH - shown, 1) + right


def _b(text: str) -> str:
    return f"{BOLD_MARKER}{text}{BOLD_MARKER}"


def build_payment_slip(payment_id: int) -> Dict[str, Any]:
    with get_session() as s:
        pay = s.get(Payment, int(payment_id))
        if not pay:
            raise ValueError(f"Payment not found: {payment_id}")
        kind = (pay.entity_type or "").lower()
        if kind not in ("sale", "purchase"):
            raise ValueError("Not a sale/purchase payment")
        doc = s.get(Sale if kind == "sale" else Purchase, int(pay.entity_id))
        party_id = doc.buyer_party_id if kind == "sale" else doc.supplier_party_id
        party = s.get(Party, party_id) if party_id else None
        name = (party.display_name if party else "")
        if party is not None and party.walkin_code:
            name = f"{name} ({party.walkin_code})"
        if party is not None and is_shared_walkin(party.id) and (doc.walkin_name or "").strip():
            name = doc.walkin_name.strip()
        if kind == "sale":
            rows = (s.query(SaleItem, Product).join(Product, Product.id == SaleItem.product_id)
                    .filter(SaleItem.sale_id == doc.id).all())
        else:
            rows = (s.query(PurchaseItem, Product).join(Product, Product.id == PurchaseItem.product_id)
                    .filter(PurchaseItem.purchase_id == doc.id).all())
        items = [{"name": prod.name, "bags": float(it.qty_bags or 0), "kg": float(it.total_weight_kg or 0)}
                 for it, prod in rows]
        bal = document_balances(s, kind, [doc.id])[doc.id]
        user = s.get(User, pay.created_by_user_id) if pay.created_by_user_id else None
        prefix = "SAL" if kind == "sale" else "PUR"
        return {
            "receipt_no": f"PAY-{int(pay.id):05d}", "reference": f"{prefix}-{int(doc.id):05d}",
            "name": name, "when": pay.created_at, "served_by": user.username if user else "",
            "method": pay.method or "", "items": items,
            "total": bal.net_total, "paid_now": float(pay.amount or 0),
            "paid_so_far": min(bal.settled, bal.net_total), "balance": bal.balance,
            "kind": kind,
        }


def render_payment_slip(d: Dict[str, Any], shop: ShopInfo) -> str:
    """The small payment receipt: short, with the product names and the balance in bold."""
    out: List[str] = [_b(_center(shop.name))]
    if shop.phone:
        out.append(_center(shop.phone))
    out += [_rule(), _two(_b("PAYMENT RECEIPT"), d["receipt_no"])]
    out += _wrap(f"{d['name']}  -  {d['reference']}", WIDTH)
    out += [_fmt_dt(d["when"]), _rule()]
    for it in d["items"]:
        qty = f"{_bags(it['bags'])} bags" + (f" {_money(it['kg'])} kg" if it["kg"] else "")
        lines = _wrap(it["name"], WIDTH - len(qty) - 1)
        out += [_b(x) for x in lines[:-1]]
        out.append(_b(lines[-1]) + " " * max(WIDTH - len(lines[-1]) - len(qty), 1) + qty)
    out.append(_rule())
    out.append(_two("Total:", f"N{_money(d['total'])}"))
    out.append(_two("PAID NOW:", f"N{_money(d['paid_now'])}"))
    out.append(_two("Paid so far:", f"N{_money(d['paid_so_far'])}"))
    out.append(_b(_two("BALANCE:", f"N{_money(d['balance'])}")))
    if d["method"]:
        out.append(f"Method: {d['method']}")
    out += ["", f"By: {d['served_by']}      Sign: ________", _center(shop.footer_note or "Thank you"), ""]
    return "\n".join(out)
