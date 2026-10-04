# apps/product_management_app/features/receipts_v2/receipt_data.py
# -*- coding: utf-8 -*-
"""
Builders: read authoritative DB state -> return a ReceiptDoc.

Fixes carried over from the old engine:
  * paid is computed from the Payment table, not from non-existent
    Purchase.paid_amount / Sale.paid columns.
  * supplier is read from Purchase.supplier_party_id (not `party_id`).
  * returns are disambiguated by explicit doc_type, so a sale-return id can
    never accidentally print a purchase-return with the same id.

No Qt here. Import is cheap; the actual DB session is opened per call.
"""

from __future__ import annotations

from typing import List, Optional

from database.session import get_session
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.product import Product
from database.models.party import Party
from database.models.user import User
from database.models.payment import Payment
from database.services.document_balance import offset_totals
from database.models.advance import Advance
from database.models.sale_return import SaleReturn
from database.models.purchase_return import PurchaseReturn

from .receipt_models import (
    ReceiptDoc, ReceiptLine, PaymentLine, PartyInfo, ShopInfo,
    SALE, PURCHASE, ADVANCE, PAYMENT, SALE_RETURN, PURCHASE_RETURN,
)
from .shop import get_shop


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _f(x) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return (ptype or "").strip().lower() in ("fertilizer", "fertilizers")


def _no(prefix: str, rid: int) -> str:
    return f"{prefix}-{int(rid):05d}"


def _sum_paid(s, entity_type: str, entity_id: int) -> float:
    rows = (
        s.query(Payment)
        .filter(Payment.entity_type == entity_type, Payment.entity_id == int(entity_id))
        .all()
    )
    offset = offset_totals(s, entity_type, [int(entity_id)]).get(int(entity_id), 0.0)   # swap / set-off
    return round(sum(_f(p.amount) for p in rows) + offset, 2)


def _payment_lines(s, entity_type: str, entity_id: int) -> List[PaymentLine]:
    rows = (
        s.query(Payment)
        .filter(Payment.entity_type == entity_type, Payment.entity_id == int(entity_id))
        .order_by(Payment.id.asc())
        .all()
    )
    return [PaymentLine(amount=_f(p.amount), method=(p.method or ""), when=p.created_at)
            for p in rows]


def _ledger_from_items(lines: List[ReceiptLine]) -> str:
    return "fertilizers" if any(l.is_fertilizer for l in lines) else "crops"


# ---------------------------------------------------------------------------
# SALE
# ---------------------------------------------------------------------------

def build_sale(sale_id: int, shop: ShopInfo) -> ReceiptDoc:
    with get_session() as s:
        sale = s.query(Sale).filter(Sale.id == int(sale_id)).first()
        if not sale:
            raise ValueError(f"Sale not found: {sale_id}")

        party = s.query(Party).get(sale.buyer_party_id) if sale.buyer_party_id else None
        user = s.query(User).get(sale.created_by_user_id) if sale.created_by_user_id else None

        rows = (
            s.query(SaleItem, Product)
            .join(Product, Product.id == SaleItem.product_id)
            .filter(SaleItem.sale_id == sale.id)
            .all()
        )
        lines = [
            ReceiptLine(
                name=prod.name,
                bags=_f(si.qty_bags),
                kg=_f(si.total_weight_kg),
                is_fertilizer=_is_fert(si.product_type_snapshot),
                rate=_f(si.unit_sale_rate),
                amount=_f(si.line_total),
            )
            for si, prod in rows
        ]

        paid = _sum_paid(s, "sale", sale.id)
        payments = _payment_lines(s, "sale", sale.id)

        party_info = PartyInfo(
            name=(party.display_name if party else (sale.walkin_name or "Walk-in")),
            phone=(party.phone if party else ""),
            kind=(party.party_type if party else "walkin"),
        )

        return ReceiptDoc(
            doc_type=SALE, title="Sales Receipt", receipt_no=_no("SAL", sale.id),
            shop=shop, party=party_info,
            when=sale.completed_at or sale.created_at,
            served_by=(user.username if user else ""),
            ledger=_ledger_from_items(lines),
            note=(sale.notes or ""),
            lines=lines, payments=payments,
            subtotal=_f(sale.subtotal), discount=_f(sale.discount),
            total=_f(sale.total_amount), paid=paid,
        )


# ---------------------------------------------------------------------------
# PURCHASE / INTAKE
# ---------------------------------------------------------------------------

def build_purchase(purchase_id: int, shop: ShopInfo) -> ReceiptDoc:
    with get_session() as s:
        pur = s.query(Purchase).filter(Purchase.id == int(purchase_id)).first()
        if not pur:
            raise ValueError(f"Intake/Purchase not found: {purchase_id}")

        party = s.query(Party).get(pur.supplier_party_id) if pur.supplier_party_id else None
        user = s.query(User).get(pur.created_by_user_id) if pur.created_by_user_id else None

        rows = (
            s.query(PurchaseItem, Product)
            .join(Product, Product.id == PurchaseItem.product_id)
            .filter(PurchaseItem.purchase_id == pur.id)
            .all()
        )
        lines = [
            ReceiptLine(
                name=prod.name,
                bags=_f(pi.qty_bags),
                kg=_f(pi.total_weight_kg),
                is_fertilizer=_is_fert(pi.product_type_snapshot),
                rate=_f(pi.unit_cost_rate),
                amount=_f(pi.line_total),
            )
            for pi, prod in rows
        ]

        paid = _sum_paid(s, "purchase", pur.id)
        payments = _payment_lines(s, "purchase", pur.id)

        party_info = PartyInfo(
            name=(party.display_name if party else (pur.walkin_name or "Walk-in")),
            phone=(party.phone if party else ""),
            kind=(party.party_type if party else "supplier"),
        )

        return ReceiptDoc(
            doc_type=PURCHASE, title="Intake Receipt", receipt_no=_no("PUR", pur.id),
            shop=shop, party=party_info,
            when=pur.completed_at or pur.created_at,
            served_by=(user.username if user else ""),
            ledger=_ledger_from_items(lines),
            note=(pur.notes or ""),
            lines=lines, payments=payments,
            subtotal=_f(pur.subtotal), discount=_f(pur.discount),
            total=_f(pur.total_amount), paid=paid,
        )


# ---------------------------------------------------------------------------
# ADVANCE
# ---------------------------------------------------------------------------

def build_advance(advance_id: int, shop: ShopInfo) -> ReceiptDoc:
    with get_session() as s:
        adv = s.get(Advance, int(advance_id))
        if not adv:
            raise ValueError(f"Advance not found: {advance_id}")

        party = s.get(Party, adv.party_id) if adv.party_id else None
        user = s.get(User, adv.created_by_user_id) if adv.created_by_user_id else None

        direction = (adv.direction or "").lower()
        title = "Advance In" if direction == "received" else "Advance Out"
        amount = _f(adv.amount)

        return ReceiptDoc(
            doc_type=ADVANCE, title=title, receipt_no=_no("ADV", adv.id),
            shop=shop,
            party=PartyInfo(name=(party.display_name if party else ""),
                            phone=(party.phone if party else "")),
            when=adv.created_at,
            served_by=(user.username if user else ""),
            ledger=(adv.ledger or ""),
            note=(adv.notes or ""),
            lines=[], payments=[],
            subtotal=amount, discount=0.0, total=amount, paid=amount,
        )


# ---------------------------------------------------------------------------
# PAYMENT
# ---------------------------------------------------------------------------

def build_payment(payment_id: int, shop: ShopInfo) -> ReceiptDoc:
    with get_session() as s:
        pay = s.get(Payment, int(payment_id))
        if not pay:
            raise ValueError(f"Payment not found: {payment_id}")

        party = s.get(Party, pay.party_id) if pay.party_id else None
        user = s.get(User, pay.created_by_user_id) if pay.created_by_user_id else None

        et = (pay.entity_type or "").lower()
        ref_prefix = {"sale": "SAL", "purchase": "PUR", "advance": "ADV"}.get(et, "")
        reference = _no(ref_prefix, pay.entity_id) if ref_prefix and pay.entity_id else ""
        amount = _f(pay.amount)

        return ReceiptDoc(
            doc_type=PAYMENT, title="Payment Receipt", receipt_no=_no("PAY", pay.id),
            shop=shop,
            party=PartyInfo(name=(party.display_name if party else ""),
                            phone=(party.phone if party else "")),
            when=pay.created_at,
            served_by=(user.username if user else ""),
            reference=reference,
            note=(pay.notes or ""),
            lines=[],
            payments=[PaymentLine(amount=amount, method=(pay.method or ""), when=pay.created_at)],
            subtotal=amount, discount=0.0, total=amount, paid=amount,
        )


# ---------------------------------------------------------------------------
# RETURNS  (explicit doc_type -> no id collision)
# ---------------------------------------------------------------------------

def build_return(doc_type: str, return_id: int, shop: ShopInfo) -> ReceiptDoc:
    is_sale = (doc_type == SALE_RETURN)
    with get_session() as s:
        if is_sale:
            r = s.query(SaleReturn).filter(SaleReturn.id == int(return_id)).first()
        else:
            r = s.query(PurchaseReturn).filter(PurchaseReturn.id == int(return_id)).first()
        if not r:
            raise ValueError(f"{doc_type} not found: {return_id}")

        product = s.get(Product, r.product_id) if r.product_id else None
        party = s.get(Party, r.party_id) if r.party_id else None
        user = s.get(User, r.created_by_user_id) if r.created_by_user_id else None

        rate = _f(getattr(r, "unit_price_snapshot", None) if is_sale
                  else getattr(r, "unit_cost_snapshot", None))
        amount = abs(_f(r.amount))
        src_prefix = "SAL" if is_sale else "PUR"
        src_id = r.sale_id if is_sale else r.purchase_id

        line = ReceiptLine(
            name=(product.name if product else "Item"),
            bags=_f(r.qty_bags),
            kg=_f(r.qty_kg),
            is_fertilizer=_is_fert(r.product_type_snapshot),
            rate=rate,
            amount=amount,
        )

        return ReceiptDoc(
            doc_type=(SALE_RETURN if is_sale else PURCHASE_RETURN),
            title=("Sale Return" if is_sale else "Purchase Return"),
            receipt_no=_no("SRET" if is_sale else "PRET", r.id),
            shop=shop,
            party=PartyInfo(name=(party.display_name if party else ""),
                            phone=(getattr(party, "phone", "") if party else "")),
            when=r.created_at,
            served_by=(user.username if user else ""),
            ledger=("fertilizers" if line.is_fertilizer else "crops"),
            reference=_no(src_prefix, src_id) if src_id else "",
            note=(getattr(r, "reason", "") or ""),
            lines=[line], payments=[],
            subtotal=amount, discount=0.0, total=amount, paid=0.0,
        )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def build(doc_type: str, entity_id: int, shop: Optional[ShopInfo] = None) -> ReceiptDoc:
    shop = shop or get_shop()
    doc_type = (doc_type or "").strip().lower()
    if doc_type == SALE:
        return build_sale(entity_id, shop)
    if doc_type == PURCHASE:
        return build_purchase(entity_id, shop)
    if doc_type == ADVANCE:
        return build_advance(entity_id, shop)
    if doc_type == PAYMENT:
        return build_payment(entity_id, shop)
    if doc_type in (SALE_RETURN, PURCHASE_RETURN):
        return build_return(doc_type, entity_id, shop)
    raise ValueError(f"Unknown doc_type: {doc_type}")