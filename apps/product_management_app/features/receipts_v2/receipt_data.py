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

from sqlalchemy import func

from database.session import get_session
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.product import Product
from database.models.party import Party
from database.models.user import User
from database.models.payment import Payment
from database.models.advance import Advance
from database.models.sale_return import SaleReturn
from database.models.purchase_return import PurchaseReturn
from database.models.sale_discount import SaleDiscount
from database.models.advance_allocations import AdvanceAllocation
from database.services.document_balance import offset_totals

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
    return f"{prefix}-{int(rid)}"


def _sum_paid(s, entity_type: str, entity_id: int) -> float:
    rows = (
        s.query(Payment)
        .filter(Payment.entity_type == entity_type, Payment.entity_id == int(entity_id))
        .all()
    )
    paid = sum(_f(p.amount) for p in rows)

    # Applying an advance never creates a Payment row at all - it's
    # recorded ONLY in AdvanceAllocation. Without this, a sale or purchase
    # settled partly by advance would print with a wildly inflated Balance
    # Due, even though it's genuinely, fully paid.
    alloc_total = (
        s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
        .filter(AdvanceAllocation.entity_type == entity_type, AdvanceAllocation.entity_id == int(entity_id))
        .scalar()
    ) or 0.0

    # Set off against the party's opposite documents (a swap / settlement): no cash, but it settles the
    # document exactly like a payment does.
    offset = offset_totals(s, entity_type, [int(entity_id)]).get(int(entity_id), 0.0)

    return round(paid + float(alloc_total) + offset, 2)


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

        from database.services.sale_adjustment_service import SaleAdjustmentService
        lines = [
            ReceiptLine(
                name=l["product"],
                bags=_f(l["bags"]),
                kg=_f(l["kg"]),
                is_fertilizer=(l["unit"] == "bag"),
                rate=_f(l["rate"]),
                amount=_f(l["amount"]),
            )
            for l in SaleAdjustmentService._line_prices(s, sale.id)
        ]
        paid = _sum_paid(s, "sale", sale.id)
        payments = _payment_lines(s, "sale", sale.id)

        # Manual discount adjustments from the Payments section's "lazy
        # calculator" (SaleAdjustmentService) - additive, never overwriting
        # the sale's own original discount/total. Added to what's shown as
        # "Discount" on the receipt, and subtracted from the effective
        # total so the printed Balance Due correctly reflects it.
        manual_discount = float(
            s.query(func.coalesce(func.sum(SaleDiscount.amount), 0.0))
            .filter(SaleDiscount.sale_id == sale.id)
            .scalar() or 0.0
        )

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
            subtotal=_f(sale.subtotal), discount=_f(sale.discount) + manual_discount,
            total=max(_f(sale.total_amount) - manual_discount, 0.0), paid=paid,
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

        from database.services.purchase_adjustment_service import PurchaseAdjustmentService
        from database.models.purchase_price_adjustment import PurchasePriceAdjustment
        lines = [
            ReceiptLine(
                name=l["product"],
                bags=_f(l["bags"]),
                kg=_f(l["kg"]),
                is_fertilizer=(l["unit"] == "bag"),
                rate=_f(l["rate"]),
                amount=_f(l["amount"]),
            )
            for l in PurchaseAdjustmentService._line_prices(s, pur.id)
        ]
        price_adj = float(
            s.query(func.coalesce(func.sum(PurchasePriceAdjustment.amount), 0.0))
            .filter(PurchasePriceAdjustment.purchase_id == pur.id)
            .scalar() or 0.0
        )
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
            total=max(_f(pur.total_amount) + price_adj, 0.0), paid=_sum_paid(s, "purchase", pur.id),
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

# ---------------------------------------------------------------------------
# QUICK PURCHASE SUPPLIER RECEIPT (urgent note + stamp)
# ---------------------------------------------------------------------------

def build_quick_purchase_supplier_note(purchase_id: int, driver_name: str,
                                       shop: Optional[ShopInfo] = None) -> str:
    """
    The real Sales/Quick Purchase supplier document - a "Purchase
    Request" for collection, not a full priced receipt. Matches the
    mockup's actual content: who the goods are being released to (the
    driver collecting them), what's being collected, and an
    authorization note - deliberately no pricing at all, since this is
    a collection authorization, not a financial receipt (the supplier
    already knows what they agreed to sell and at what price).

    Built fresh and standalone, NOT wrapping build_purchase()/
    render_receipt() - the content itself is genuinely different
    (no Rate/Amount/Payment columns at all), so reusing the full-receipt
    layout would mean fighting it, not extending it. The logo and a
    real barcode from the reference mockup are deliberately left out -
    the thermal printer this prints on has no image support at all -
    but the Purchase ID is still shown on its own, clearly labeled line,
    so a real barcode command can be added there later without
    restructuring anything.
    """
    from .receipt_render import _rule, _center, _wrap, WIDTH, _fmt_dt

    shop = shop or get_shop()
    purchase_id = int(purchase_id)

    with get_session() as s:
        pur = s.get(Purchase, purchase_id)
        if pur is None:
            raise ValueError(f"Purchase #{purchase_id} not found.")

        supplier = s.query(Party).get(pur.supplier_party_id) if pur.supplier_party_id else None
        supplier_name = supplier.display_name if supplier else (pur.walkin_name or "Unknown supplier")

        rows = (
            s.query(PurchaseItem, Product)
            .join(Product, Product.id == PurchaseItem.product_id)
            .filter(PurchaseItem.purchase_id == purchase_id)
            .all()
        )
        line_items = [
            (prod.name, _qty_display(pi))
            for pi, prod in rows
        ]
        when = pur.completed_at or pur.created_at
        purchase_no = pur.purchase_no or f"PUR-{pur.id}"

    driver_name = (driver_name or "").strip() or "our driver"

    parts: List[str] = []
    if shop.name:
        parts.append(_center(shop.name))
    if shop.address:
        for ln in _wrap(shop.address, WIDTH):
            parts.append(_center(ln))
    if shop.phone:
        parts.append(_center(shop.phone))
    parts.append(_rule())
    parts.append(_center("PURCHASE REQUEST"))
    parts.append(_rule())

    parts.append(f"Purchase ID : {purchase_no}")
    parts.append(f"Supplier    : {supplier_name}")
    parts.append(f"Driver      : {driver_name}")
    parts.append(f"Date & Time : {_fmt_dt(when)}")
    parts.append(_rule())

    parts.append(_center("PRODUCTS TO COLLECT"))
    parts.append(_rule())
    parts.append("S/N".ljust(5) + "Product".ljust(WIDTH - 5 - 12) + "Quantity".rjust(12))
    parts.append(_rule())
    for i, (name, qty) in enumerate(line_items, start=1):
        name_col_w = WIDTH - 5 - 12
        first, *rest = _wrap(name, name_col_w) or [""]
        parts.append(str(i).ljust(5) + first.ljust(name_col_w) + qty.rjust(12))
        for extra in rest:
            parts.append(" " * 5 + extra)
    parts.append(_rule())

    # Purchase ID shown again, on its own - deliberately where a real
    # barcode command would go once one's actually needed.
    parts.append(_center(f"Purchase ID: {purchase_no}"))
    parts.append(_rule())

    parts.append("NOTE:")
    note = (
        f"Please release the above products to {driver_name} for "
        f"collection on behalf of {shop.name or 'us'}."
    )
    for ln in _wrap(note, WIDTH):
        parts.append(ln)
    parts.append("")
    parts.append("Authorized by: ______________________")
    parts.append("")
    parts.append(_center("Thank you for your cooperation."))
    parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _qty_display(pi) -> str:
    """Bags for fertilizer, kg (with bags alongside, if any) for crops -
    matching how quantity is shown everywhere else in this app."""
    is_fert = _is_fert(pi.product_type_snapshot)
    if is_fert:
        return f"{int(pi.qty_bags)} bags" if float(pi.qty_bags or 0) != 1 else "1 bag"
    weight = float(pi.total_weight_kg or 0)
    bags = float(pi.qty_bags or 0)
    if bags > 0:
        bag_word = "bag" if bags == 1 else "bags"
        return f"{weight:,.0f}kg ({int(bags)} {bag_word})"
    return f"{weight:,.0f}kg"


def build_combined_delivery_note(purchase_ids: List[int], shop: Optional[ShopInfo] = None) -> str:
    """
    One delivery note covering every supplier involved in a single Quick
    Purchase batch (e.g. 3 different products, 3 different suppliers,
    bought together to cover one sale's shortfall) - products only, no
    money, segmented into one section per supplier so it's clear at a
    glance who delivered what.

    Deliberately built outside the ReceiptDoc/render_receipt pipeline
    used by every other document here: that pipeline assumes one
    document = one party = one flat list of lines, which doesn't fit a
    genuinely multi-supplier document. Reuses the same low-level layout
    helpers (rule, center, wrap, row formatting) from receipt_render.py
    directly, so it still LOOKS like every other receipt from this app,
    even though its own structure is different.
    """
    from .receipt_render import _rule, _center, _wrap, WIDTH, _qty_label, _fmt_dt
    from .receipt_models import ReceiptLine

    shop = shop or get_shop()
    purchase_ids = [int(pid) for pid in (purchase_ids or []) if pid]
    if not purchase_ids:
        raise ValueError("No purchases to build a delivery note from.")

    with get_session() as s:
        purchases = (
            s.query(Purchase)
            .filter(Purchase.id.in_(purchase_ids))
            .order_by(Purchase.id.asc())
            .all()
        )
        if not purchases:
            raise ValueError("None of the given purchases were found.")

        # Group line items by supplier, preserving the order suppliers
        # were first bought from in this batch.
        supplier_order: List[int] = []
        by_supplier: dict = {}  # supplier_party_id -> {"name": str, "lines": [ReceiptLine]}

        for pur in purchases:
            sid = int(pur.supplier_party_id or 0)
            if sid not in by_supplier:
                party = s.query(Party).get(sid) if sid else None
                by_supplier[sid] = {
                    "name": (party.display_name if party else (pur.walkin_name or "Unknown supplier")),
                    "lines": [],
                }
                supplier_order.append(sid)

            rows = (
                s.query(PurchaseItem, Product)
                .join(Product, Product.id == PurchaseItem.product_id)
                .filter(PurchaseItem.purchase_id == pur.id)
                .all()
            )
            for pi, prod in rows:
                by_supplier[sid]["lines"].append(ReceiptLine(
                    name=prod.name,
                    bags=_f(pi.qty_bags),
                    kg=_f(pi.total_weight_kg),
                    is_fertilizer=_is_fert(pi.product_type_snapshot),
                    rate=_f(pi.unit_cost_rate),
                    amount=_f(pi.line_total),
                ))

        latest_when = max((p.completed_at or p.created_at) for p in purchases)

    parts: List[str] = []
    if shop.name:
        parts.append(_center(shop.name))
    if shop.address:
        for ln in _wrap(shop.address, WIDTH):
            parts.append(_center(ln))
    if shop.phone:
        parts.append(_center(shop.phone))
    parts.append(_rule())
    parts.append(_center("DELIVERY NOTE"))
    parts.append(_center(f"({len(supplier_order)} supplier(s))"))
    parts.append(_rule())
    parts.append(f"Date    : {_fmt_dt(latest_when)}")
    parts.append(_rule())

    for sid in supplier_order:
        group = by_supplier[sid]
        parts.append(f"Supplier: {group['name']}")
        parts.append("Item".ljust(WIDTH - 10) + "Qty".rjust(10))
        parts.append(_rule())
        for line in group["lines"]:
            qty = _qty_label(line)
            name_lines = _wrap(line.name, max(4, WIDTH - len(qty) - 1))
            parts.append((name_lines[0].ljust(WIDTH - len(qty)) + qty)[:WIDTH])
            for extra in name_lines[1:]:
                parts.append("  " + extra)
        parts.append(_rule())

    parts.append("")
    parts.append("Received by: ______________________")
    parts.append("")
    if shop.footer_note:
        parts.append(shop.footer_note)
    parts.append("")

    return "\n".join(parts).rstrip() + "\n"


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