# database/services/supplier_profile_service.py
# -*- coding: utf-8 -*-
"""
Read-only data for the supplier profile screen.

Deliberately thin: the balance headline already exists in
PartyBalanceService.compute (party-agnostic, so it works for a supplier as-is).
All this adds is the two lists the profile tabs show - Purchases and Payments -
which no existing service exposed keyed by supplier.

Quick Purchases are NOT a separate concept here. A QP is a purchase, so it
appears in the same Purchases list with is_quick / linked_sale_id surfaced, so
the UI can show a Type = Intake / Quick column and a "For Sale" link. One list,
no fragmentation - which is what their own spec asked for.
"""
from __future__ import annotations

from typing import List, Dict, Any
from datetime import datetime

from sqlalchemy import select, desc, func

from database.session import get_session
from shared.auth.permissions import require_admin
from database.models.purchase import Purchase
from database.models.payment import Payment
from database.models.party import Party


def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


class SupplierProfileService:

    @staticmethod
    def header(supplier_party_id: int) -> Dict[str, Any]:
        """Name / phone / address for the profile header."""
        with get_session() as s:
            p = s.get(Party, int(supplier_party_id))
            if p is None:
                return {"id": int(supplier_party_id), "name": "Unknown supplier",
                        "phone": "", "address": "", "party_type": ""}
            return {
                "id": int(p.id),
                "name": p.display_name,
                "phone": p.phone or "",
                "address": p.address or "",
                "party_type": p.party_type or "",
                "is_customer": bool(p.is_customer),
                "is_supplier": bool(p.is_supplier),
            }

    @staticmethod
    def purchases(supplier_party_id: int) -> List[Dict[str, Any]]:
        """
        Every purchase from this supplier, newest first, intake and quick alike.
        The is_quick flag drives the Type column; linked_sale_id the For Sale link.
        """
        out: List[Dict[str, Any]] = []
        with get_session() as s:
            rows = s.execute(
                select(Purchase)
                .where(Purchase.supplier_party_id == int(supplier_party_id))
                .order_by(desc(Purchase.created_at))
            ).scalars().all()
            for p in rows:
                out.append({
                    "id": int(p.id),
                    "purchase_no": p.purchase_no or f"PUR-{p.id}",
                    "type": "Quick" if p.is_quick else "Intake",
                    "linked_sale_id": p.linked_sale_id,
                    "total_amount": _f(p.total_amount),
                    "status": p.status,
                    "when": p.created_at,
                    "note": p.notes or "",
                })
        return out

    @staticmethod
    def get_or_lazy_create_supplier(name: str, phone: str = "", address: str = "") -> int:
        """
        Look up an existing supplier by phone (if given) or name; create one
        if none exists. Mirrors AppState.get_or_lazy_create_walkin exactly -
        same dedup rule, same direct-session write.

        Deliberately NOT routed through suppliers/api.py. That module calls
        ApiClient -> httpx -> /api/suppliers, and nothing else in this app
        uses that path - it appears to be an unused Phase-1 prototype with no
        server behind it (its own fields, supplier_type/company_ref, do not
        even exist as columns on Party). Every other service writes straight
        to the database, so this does too.

        address is only applied when CREATING a new row - an existing match
        (by phone or name) is never silently overwritten.

        The dedupe SEARCH is deliberately NOT scoped to existing suppliers.
        If "Muhammad Sabiu" is already a registered customer and we now need
        to record a purchase FROM him, this finds that SAME party by phone
        and flags him is_supplier=True on it - one party, one balance, not a
        duplicate row that fragments his history across two identities.

        It IS, however, scoped away from walk-ins specifically. A walk-in is
        deliberately temporary with no permanent ledger - the dual-role merge
        above is the right call for a genuine registered person, but a
        walk-in must never silently gain is_supplier=True just because a
        name or phone number happened to coincide with one. Walk-in stays
        walk-in, no matter what.
        """
        from database.session import get_session as _gs
        from database.models.party import Party

        name = (name or "").strip() or "Unknown Supplier"
        phone = (phone or "").strip()
        address = (address or "").strip()

        with _gs() as s:
            q = (
                s.query(Party)
                .filter(Party.is_active == True)  # noqa: E712
                .filter(Party.party_type != "walkin")
            )
            row = (q.filter(Party.phone == phone).first() if phone
                  else q.filter(Party.display_name == name).first())
            if row:
                if not row.is_supplier:
                    row.is_supplier = True
                return int(row.id)

            new_supplier = Party(
                party_type="supplier",
                display_name=name,
                phone=phone or None,
                address=address,
                is_active=True,
                is_supplier=True,
            )
            s.add(new_supplier)
            s.flush()
            return int(new_supplier.id)

    @staticmethod
    def supplier_only_for_picker() -> List[Dict[str, Any]]:
        """
        Suppliers who are NOT already a customer - for the Sale screen's
        buyer picker to offer alongside real customers. "There may be a
        certain time they sell to the supplier too" needs this party to be
        FINDABLE the first time, before any sale has ever happened to them.
        Once SaleService.record() sets is_customer=True on them, they drop
        out of this list and appear normally in state.customers instead -
        this is only ever a supplement for the not-yet-a-customer window.
        """
        out: List[Dict[str, Any]] = []
        with get_session() as s:
            rows = s.execute(
                select(Party)
                .where(Party.is_supplier == True)  # noqa: E712
                .where(Party.is_customer == False)  # noqa: E712
                .where(Party.is_active == True)  # noqa: E712
                .order_by(Party.display_name)
            ).scalars().all()
            for p in rows:
                out.append({
                    "id": int(p.id),
                    "name": p.display_name,
                    "phone": p.phone or "",
                })
        return out

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        """
        Every supplier, alphabetical. Feeds the Suppliers list screen (which
        does its own client-side search over what this returns).
        """
        out: List[Dict[str, Any]] = []
        with get_session() as s:
            rows = s.execute(
                select(Party)
                .where(Party.is_supplier == True)  # noqa: E712
                .order_by(Party.display_name)
            ).scalars().all()
            for p in rows:
                out.append({
                    "id": int(p.id),
                    "name": p.display_name,
                    "phone": p.phone or "",
                    "address": p.address or "",
                    "status": "Active" if p.is_active else "Inactive",
                })
        return out

    @staticmethod
    def active_suppliers(limit: int = 200) -> List[Dict[str, Any]]:
        """
        Suppliers for the Quick Purchase picker, MOST-RECENTLY-USED first.

        Two plain, single-table queries + an in-Python sort - deliberately NOT
        a join+subquery+.nullslast(). That construct was never verified against
        real MySQL (only SQLite in testing) and is a known portability trap;
        it likely threw there, and a broad except in the dialog silently
        swallowed it, so the picker showed nothing. Sorting in Python sidesteps
        the whole class of dialect differences.
        """
        out: List[Dict[str, Any]] = []
        with get_session() as s:
            parties = s.execute(
                select(Party).where(Party.is_supplier == True)  # noqa: E712
            ).scalars().all()

            last_at_by_pid: Dict[int, Any] = {}
            for pid, last_at in s.execute(
                select(Purchase.supplier_party_id, func.max(Purchase.created_at))
                .group_by(Purchase.supplier_party_id)
            ).all():
                if pid is not None and last_at is not None:
                    last_at_by_pid[int(pid)] = last_at

            def sort_key(p):
                last = last_at_by_pid.get(int(p.id))
                # never-used suppliers sort last; among used ones, most recent first
                return (last is None, -(last.timestamp() if last else 0))

            for party in sorted(parties, key=sort_key)[:int(limit)]:
                last_at = last_at_by_pid.get(int(party.id))
                out.append({
                    "id": int(party.id),
                    "name": party.display_name,
                    "phone": party.phone or "",
                    "last_used": last_at,
                })
        return out

    @staticmethod
    def payments(supplier_party_id: int) -> List[Dict[str, Any]]:
        """
        Every payment involving this party, BOTH directions, newest first:
        money we paid them (purchase, direction out) AND money they paid us
        (sale, direction in). A party can now be both a supplier and a
        customer at once - excluding one direction here would make this tab
        look broken the moment "Collect from Supplier" is ever used.
        """
        from database.models.sale import Sale as _Sale

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            rows = s.execute(
                select(Payment)
                .where(Payment.party_id == int(supplier_party_id))
                .where(Payment.entity_type.in_(("purchase", "sale")))
                .where(Payment.status != "voided")
                .order_by(desc(Payment.created_at))
            ).scalars().all()
            for p in rows:
                out.append({
                    "id": int(p.id),
                    "direction": p.direction,
                    "entity_type": p.entity_type,
                    "purchase_id": int(p.entity_id or 0) if p.entity_type == "purchase" else None,
                    "sale_id": int(p.entity_id or 0) if p.entity_type == "sale" else None,
                    "reference": (f"Purchase #{p.entity_id}" if p.entity_type == "purchase"
                                 else f"Sale #{p.entity_id}"),
                    "amount": _f(p.amount),
                    "method": p.method or "",
                    "when": p.created_at,
                })
        return out

    @staticmethod
    def activity(supplier_party_id: int) -> List[Dict[str, Any]]:
        """
        Every purchase, payment-out, sale, AND payment-in for this party,
        merged into ONE chronological timeline, newest first. A party can now
        be both a supplier and a customer (independent is_customer/is_supplier
        flags) - this is the "everything that happened with them" view, in
        both directions. Each row is tagged with "kind" so the UI can route a
        double-click to the right detail view.
        """
        from database.models.purchase import Purchase as _Purchase
        from database.models.sale import Sale as _Sale

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            purchases = s.execute(
                select(_Purchase)
                .where(_Purchase.supplier_party_id == int(supplier_party_id))
            ).scalars().all()
            for p in purchases:
                out.append({
                    "kind": "purchase",
                    "id": int(p.id),
                    "when": p.created_at,
                    "reference": p.purchase_no or f"PUR-{p.id}",
                    "detail": "Quick Purchase" if p.is_quick else "Intake",
                    "amount": _f(p.total_amount),
                    "status": p.status,
                })

            payments_out = s.execute(
                select(Payment)
                .where(Payment.party_id == int(supplier_party_id))
                .where(Payment.entity_type == "purchase")
                .where(Payment.direction == "out")
                .where(Payment.status != "voided")
            ).scalars().all()
            for pay in payments_out:
                out.append({
                    "kind": "payment_out",
                    "id": int(pay.id),
                    "when": pay.created_at,
                    "reference": f"Purchase #{pay.entity_id}",
                    "detail": pay.method or "",
                    "amount": _f(pay.amount),
                    "status": "paid",
                })

            sales = s.execute(
                select(_Sale)
                .where(_Sale.buyer_party_id == int(supplier_party_id))
            ).scalars().all()
            for sale in sales:
                out.append({
                    "kind": "sale",
                    "id": int(sale.id),
                    "when": sale.created_at,
                    "reference": sale.sale_no or f"SAL-{sale.id}",
                    "detail": "Sale",
                    "amount": _f(sale.total_amount),
                    "status": sale.status,
                })

            payments_in = s.execute(
                select(Payment)
                .where(Payment.party_id == int(supplier_party_id))
                .where(Payment.entity_type == "sale")
                .where(Payment.direction == "in")
                .where(Payment.status != "voided")
            ).scalars().all()
            for pay in payments_in:
                out.append({
                    "kind": "payment_in",
                    "id": int(pay.id),
                    "when": pay.created_at,
                    "reference": f"Sale #{pay.entity_id}",
                    "detail": pay.method or "",
                    "amount": _f(pay.amount),
                    "status": "received",
                })

        out.sort(key=lambda r: r["when"] or datetime.min, reverse=True)
        return out

    @staticmethod
    def sales(supplier_party_id: int) -> List[Dict[str, Any]]:
        """Every sale made TO this party (they're acting as a customer)."""
        from database.models.sale import Sale as _Sale

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            rows = s.execute(
                select(_Sale)
                .where(_Sale.buyer_party_id == int(supplier_party_id))
                .order_by(desc(_Sale.created_at))
            ).scalars().all()
            for sale in rows:
                out.append({
                    "id": int(sale.id),
                    "sale_no": sale.sale_no or f"SAL-{sale.id}",
                    "total_amount": _f(sale.total_amount),
                    "status": sale.status,
                    "when": sale.created_at,
                })
        return out

    @staticmethod
    def sale_detail(sale_id: int) -> Dict[str, Any]:
        """
        Full detail for ONE sale made to this party: header, every line item,
        and every payment received against it. Mirrors purchase_detail() on
        the opposite side of the ledger.
        """
        from database.models.sale import Sale as _Sale
        from database.models.sale_item import SaleItem
        from database.models.product import Product
        from database.models.user import User

        with get_session() as s:
            sale = s.get(_Sale, int(sale_id))
            if sale is None:
                return {}

            items = s.execute(
                select(SaleItem).where(SaleItem.sale_id == sale.id)
            ).scalars().all()
            lines = []
            for it in items:
                prod = s.get(Product, int(it.product_id))
                qty = float(it.qty_bags or 0)
                kg = _f(it.total_weight_kg)
                rate = _f(it.unit_sale_rate)
                lines.append({
                    "product": prod.name if prod else f"Product#{it.product_id}",
                    "qty_bags": qty,
                    "total_weight_kg": kg,
                    "unit_sale_rate": rate,
                    "line_total": _f(it.line_total),
                })

            payments = s.execute(
                select(Payment)
                .where(Payment.entity_type == "sale", Payment.entity_id == sale.id)
                .where(Payment.direction == "in")
                .where(Payment.status != "voided")
                .order_by(Payment.created_at)
            ).scalars().all()
            received_total = round(sum(_f(pay.amount) for pay in payments), 2)

            # Same formula as the ledger / dashboard: net of returns and
            # discounts, and advance-allocated amounts count as settled too.
            from database.models.advance_allocations import AdvanceAllocation
            from database.services.party_ledger_service import PartyLedgerService
            net_total = PartyLedgerService._net_totals_for_sales(s, [int(sale.id)]).get(int(sale.id), 0.0)
            cash_received = round(sum(
                _f(pay.amount) for pay in payments
                if (pay.method or "").lower() != "advance"), 2)
            alloc_total = _f(
                s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                .filter(AdvanceAllocation.entity_type == "sale",
                        AdvanceAllocation.entity_id == sale.id)
                .scalar()
            )

            creator = (s.get(User, int(sale.created_by_user_id))
                      if sale.created_by_user_id else None)

            return {
                "id": int(sale.id),
                "sale_no": sale.sale_no or f"SAL-{sale.id}",
                "status": sale.status,
                "notes": sale.notes or "",
                "created_by": creator.username if creator else "",
                "created_at": sale.created_at,
                "completed_at": sale.completed_at,
                "discount": _f(sale.discount),
                "total_amount": _f(sale.total_amount),
                "received_total": received_total,
                "net_total": round(net_total, 2),
                "balance": round(max(net_total - cash_received - alloc_total, 0.0), 2),
                "lines": lines,
                "payments": [{
                    "id": int(pay.id),
                    "amount": _f(pay.amount),
                    "method": pay.method or "",
                    "when": pay.created_at,
                    "notes": pay.notes or "",
                } for pay in payments],
            }

    @staticmethod
    def purchase_detail(purchase_id: int) -> Dict[str, Any]:
        """
        Full detail for ONE purchase: header, every line item (product, qty,
        rate), and every payment ever recorded against it. Backs the
        double-click detail view on both the Purchases and Activity tabs.
        """
        from database.models.purchase import Purchase as _Purchase
        from database.models.purchase_item import PurchaseItem
        from database.models.product import Product
        from database.models.user import User

        with get_session() as s:
            p = s.get(_Purchase, int(purchase_id))
            if p is None:
                return {}

            items = s.execute(
                select(PurchaseItem).where(PurchaseItem.purchase_id == p.id)
            ).scalars().all()
            lines = []
            for it in items:
                prod = s.get(Product, int(it.product_id))
                qty = float(it.qty_bags or 0)
                kg = _f(it.total_weight_kg)
                rate = _f(it.unit_cost_rate)
                lines.append({
                    "product": prod.name if prod else f"Product#{it.product_id}",
                    "qty_bags": qty,
                    "total_weight_kg": kg,
                    "unit_cost_rate": rate,
                    "line_total": round((kg if kg else qty) * rate, 2),
                })

            payments = s.execute(
                select(Payment)
                .where(Payment.entity_type == "purchase", Payment.entity_id == p.id)
                .where(Payment.status != "voided")
                .order_by(Payment.created_at)
            ).scalars().all()
            paid_total = round(sum(_f(pay.amount) for pay in payments), 2)

            # Same formula as the ledger / dashboard: net of returns, plus
            # price adjustments, with advance-allocated amounts counted as
            # settled and refunds (direction "in") not counted as paid.
            from database.models.advance_allocations import AdvanceAllocation
            from database.services.party_ledger_service import PartyLedgerService
            net_total = PartyLedgerService._net_totals_for_purchases(s, [int(p.id)]).get(int(p.id), 0.0)
            cash_paid = round(sum(
                _f(pay.amount) for pay in payments
                if pay.direction == "out" and (pay.method or "").lower() != "advance"), 2)
            alloc_total = _f(
                s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                .filter(AdvanceAllocation.entity_type == "purchase",
                        AdvanceAllocation.entity_id == p.id)
                .scalar()
            )

            from database.models.purchase_price_adjustment import PurchasePriceAdjustment
            adjustment_total = round(
                _f(
                    s.query(func.coalesce(func.sum(PurchasePriceAdjustment.amount), 0))
                    .filter(PurchasePriceAdjustment.purchase_id == p.id)
                    .scalar()
                ), 2
            )

            creator = s.get(User, int(p.created_by_user_id)) if p.created_by_user_id else None

            return {
                "id": int(p.id),
                "purchase_no": p.purchase_no or f"PUR-{p.id}",
                "type": "Quick Purchase" if p.is_quick else "Intake",
                "linked_sale_id": p.linked_sale_id,
                "status": p.status,
                "notes": p.notes or "",
                "created_by": creator.username if creator else "",
                "created_at": p.created_at,
                "completed_at": p.completed_at,
                "total_amount": _f(p.total_amount),
                "paid_total": paid_total,
                # A price adjustment is a real, additional amount now
                # genuinely owed (e.g. a supplier renegotiating after
                # delivery), not an assumption - folded into the balance
                # the same way it's folded in everywhere else this
                # session that computes a purchase's true, current total.
                "adjustment_total": adjustment_total,
                "net_total": round(net_total, 2),
                "balance": round(max(net_total - cash_paid - alloc_total, 0.0), 2),
                "lines": lines,
                "payments": [{
                    "id": int(pay.id),
                    "amount": _f(pay.amount),
                    "method": pay.method or "",
                    "when": pay.created_at,
                    "notes": pay.notes or "",
                } for pay in payments],
            }

    @staticmethod
    def payment_detail(payment_id: int) -> Dict[str, Any]:
        """
        Full detail for ONE payment: amount, method, who recorded it, and
        which transaction it applies to - a purchase (we paid) OR a sale
        (they paid us). Backs the double-click detail view on the Payments
        and Activity tabs.
        """
        from database.models.purchase import Purchase as _Purchase
        from database.models.sale import Sale as _Sale
        from database.models.user import User

        with get_session() as s:
            pay = s.get(Payment, int(payment_id))
            if pay is None:
                return {}

            purchase = None
            sale = None
            if pay.entity_type == "purchase":
                purchase = s.get(_Purchase, int(pay.entity_id))
            elif pay.entity_type == "sale":
                sale = s.get(_Sale, int(pay.entity_id))
            recorder = (s.get(User, int(pay.created_by_user_id))
                       if pay.created_by_user_id else None)

            return {
                "id": int(pay.id),
                "amount": _f(pay.amount),
                "method": pay.method or "",
                "notes": pay.notes or "",
                "when": pay.created_at,
                "recorded_by": recorder.username if recorder else "",
                "direction": pay.direction,
                "entity_type": pay.entity_type,
                "purchase_id": int(pay.entity_id or 0) if purchase else None,
                "purchase_no": (purchase.purchase_no or f"PUR-{purchase.id}") if purchase else "",
                "purchase_total": _f(purchase.total_amount) if purchase else 0.0,
                "sale_id": int(pay.entity_id or 0) if sale else None,
                "sale_no": (sale.sale_no or f"SAL-{sale.id}") if sale else "",
                "sale_total": _f(sale.total_amount) if sale else 0.0,
            }

    @staticmethod
    def update_supplier(party_id: int, name: str, phone: str = "", address: str = "") -> None:
        party_id = int(party_id or 0)
        name = (name or "").strip()
        if party_id <= 0:
            raise ValueError("party_id is required.")
        if not name:
            raise ValueError("Name is required.")

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Supplier not found.")
            p.display_name = name
            p.phone = (phone or "").strip()
            p.address = (address or "").strip()
            s.commit()

    @staticmethod
    def convert_to_supplier(party_id: int) -> None:
        """
        Flags an already-known party (typically a registered customer picked
        from the Customers list) as also a supplier, touching nothing else -
        same one-Party-both-roles model as get_or_lazy_create_supplier, just
        keyed by an existing id instead of a name/phone lookup. Walk-ins are
        excluded for the same reason _ensure_role excludes them: they have
        no permanent ledger and must never gain a standing role by accident.
        """
        party_id = int(party_id or 0)
        if party_id <= 0:
            raise ValueError("party_id is required.")

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Party not found.")
            if str(p.party_type or "") == "walkin":
                raise ValueError("Walk-ins cannot be converted to suppliers.")
            if not p.is_supplier:
                p.is_supplier = True
            s.commit()

    @staticmethod
    def remove_from_supplier_list(party_id: int) -> None:
        """
        Clears is_supplier only - is_customer, is_active, and every purchase/
        payment/return already on record for this party are left untouched.
        This is NOT delete_supplier: the party row survives, its history
        survives, it simply stops appearing in the Suppliers list.
        """
        party_id = int(party_id or 0)
        if party_id <= 0:
            raise ValueError("party_id is required.")

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Supplier not found.")
            p.is_supplier = False
            s.commit()

    @staticmethod
    def set_active(party_id: int, is_active: bool) -> None:
        party_id = int(party_id or 0)
        if party_id <= 0:
            raise ValueError("party_id is required.")

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Supplier not found.")
            p.is_active = bool(is_active)
            s.commit()

    @staticmethod
    def delete_supplier(party_id: int) -> None:
        """
        A TRUE delete - only allowed when this party has never appeared on
        any purchase, sale, payment, advance, or return. Six tables can
        reference a party this way, under six different column names
        (supplier_party_id, buyer_party_id, party_id x4) - two of them
        (Payment, Advance) have no real foreign key to parties.id, meaning a
        raw delete could succeed while silently orphaning real financial
        records rather than failing loudly. Checked proactively here instead.

        If ANY history exists, this raises rather than deletes - use
        set_active(party_id, False) instead, which preserves everything.
        """
        from database.models.sale import Sale
        from database.models.advance import Advance
        from database.models.purchase_return import PurchaseReturn
        from database.models.sale_return import SaleReturn

        party_id = int(party_id or 0)
        if party_id <= 0:
            raise ValueError("party_id is required.")

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Supplier not found.")

            checks = [
                (Purchase, "supplier_party_id", "purchase(s) bought from them"),
                (Sale, "buyer_party_id", "sale(s) made to them"),
                (Payment, "party_id", "payment(s)"),
                (Advance, "party_id", "advance(s)"),
                (PurchaseReturn, "party_id", "purchase return(s)"),
                (SaleReturn, "party_id", "sale return(s)"),
            ]
            for model, column, label in checks:
                count = s.query(model).filter(
                    getattr(model, column) == party_id
                ).count()
                if count > 0:
                    raise ValueError(
                        f"Cannot delete this supplier - there are {count} "
                        f"{label} that would be orphaned. Use \"Toggle Active\" "
                        f"instead to remove them from active use while keeping "
                        f"the history intact."
                    )

            s.delete(p)
            s.commit()

    @staticmethod
    @require_admin
    def force_delete_supplier(party_id: int) -> Dict[str, int]:
        """
        THE DESTRUCTIVE PATH - explicit client request, despite the warning
        delete_supplier gives by default. This does not just remove the
        party - it permanently erases every purchase, sale, payment,
        advance, and return ever recorded against them, plus every line
        item and stock movement those in turn created. There is no undo.

        Only call this after delete_supplier has already been tried and
        blocked, and the operator has explicitly confirmed a SECOND, more
        severe warning naming exactly what's about to be destroyed -
        delete_supplier's own ValueError already carries the real counts,
        which is what that second warning should show them.

        Deletes children before parents throughout, respecting every real
        foreign key constraint in this schema (Purchase, PurchaseReturn,
        and SaleReturn each have a genuine FK - deleting a parent first
        would fail loudly rather than cascade).

        Returns how many rows were removed from each table, so the caller
        can report back exactly what was destroyed.
        """
        from database.models.sale import Sale
        from database.models.sale_item import SaleItem
        from database.models.sale_return import SaleReturn
        from database.models.purchase_item import PurchaseItem
        from database.models.purchase_return import PurchaseReturn
        from database.models.advance import Advance
        from database.models.advance_allocations import AdvanceAllocation
        from database.models.stock_movement import StockMovement

        party_id = int(party_id or 0)
        if party_id <= 0:
            raise ValueError("party_id is required.")

        removed = {
            "purchase_items": 0, "purchase_returns": 0,
            "sale_items": 0, "sale_returns": 0,
            "advance_allocations": 0,
            "stock_movements": 0,
            "payments": 0,
            "purchases": 0, "sales": 0, "advances": 0,
        }

        with get_session() as s:
            p = s.get(Party, party_id)
            if not p:
                raise ValueError("Supplier not found.")

            # Cross-cashier settlements mirrored from this party's payments
            # must go too, or the drawer keeps an open debt for payments that
            # no longer exist.
            from database.models.cashier_settlement import CashierSettlement
            party_payment_ids = [
                pid for (pid,) in s.query(Payment.id)
                .filter(Payment.party_id == party_id).all()
            ]
            if party_payment_ids:
                s.query(CashierSettlement).filter(
                    CashierSettlement.source_payment_id.in_(party_payment_ids)
                ).delete(synchronize_session=False)

            # ---- Purchases this party supplied ----
            purchase_ids = [
                pid for (pid,) in s.query(Purchase.id)
                .filter(Purchase.supplier_party_id == party_id).all()
            ]
            if purchase_ids:
                from database.models.purchase_price_adjustment import PurchasePriceAdjustment
                # Real FK to purchases.id - would block the purchase delete.
                s.query(PurchasePriceAdjustment).filter(
                    PurchasePriceAdjustment.purchase_id.in_(purchase_ids)
                ).delete(synchronize_session=False)

                # PurchaseReturn has a real, not-nullable FK to
                # purchase_items.id (purchase_item_id) - must go before
                # PurchaseItem itself, or the delete fails on that
                # constraint (confirmed against the actual live schema).
                removed["purchase_returns"] += s.query(PurchaseReturn).filter(
                    PurchaseReturn.purchase_id.in_(purchase_ids)
                ).delete(synchronize_session=False)

                removed["purchase_items"] += s.query(PurchaseItem).filter(
                    PurchaseItem.purchase_id.in_(purchase_ids)
                ).delete(synchronize_session=False)

                removed["stock_movements"] += s.query(StockMovement).filter(
                    StockMovement.reference_type == "purchase",
                    StockMovement.reference_id.in_(purchase_ids),
                ).delete(synchronize_session=False)

                removed["payments"] += s.query(Payment).filter(
                    Payment.entity_type == "purchase",
                    Payment.entity_id.in_(purchase_ids),
                ).delete(synchronize_session=False)

                removed["purchases"] += s.query(Purchase).filter(
                    Purchase.id.in_(purchase_ids)
                ).delete(synchronize_session=False)

            # ---- Sales this party bought, if also a customer (dual-role) ----
            sale_ids = [
                sid for (sid,) in s.query(Sale.id)
                .filter(Sale.buyer_party_id == party_id).all()
            ]
            if sale_ids:
                from database.models.sale_discount import SaleDiscount
                # Real FK to sales.id - would block the sale delete.
                s.query(SaleDiscount).filter(
                    SaleDiscount.sale_id.in_(sale_ids)
                ).delete(synchronize_session=False)
                # A Quick Purchase made for one of these sales (possibly from
                # a different supplier) keeps its row but loses the link.
                s.query(Purchase).filter(
                    Purchase.linked_sale_id.in_(sale_ids)
                ).update({Purchase.linked_sale_id: None}, synchronize_session=False)

                # Same reasoning as PurchaseReturn above - SaleReturn has a
                # real, not-nullable FK to sale_items.id (sale_item_id).
                removed["sale_returns"] += s.query(SaleReturn).filter(
                    SaleReturn.sale_id.in_(sale_ids)
                ).delete(synchronize_session=False)

                removed["sale_items"] += s.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids)
                ).delete(synchronize_session=False)

                removed["stock_movements"] += s.query(StockMovement).filter(
                    StockMovement.reference_type == "sale",
                    StockMovement.reference_id.in_(sale_ids),
                ).delete(synchronize_session=False)

                removed["payments"] += s.query(Payment).filter(
                    Payment.entity_type == "sale",
                    Payment.entity_id.in_(sale_ids),
                ).delete(synchronize_session=False)

                removed["sales"] += s.query(Sale).filter(
                    Sale.id.in_(sale_ids)
                ).delete(synchronize_session=False)

            # ---- Advances directly against this party ----
            advance_ids = [
                aid for (aid,) in s.query(Advance.id)
                .filter(Advance.party_id == party_id).all()
            ]
            if advance_ids:
                removed["advance_allocations"] += s.query(AdvanceAllocation).filter(
                    AdvanceAllocation.advance_id.in_(advance_ids)
                ).delete(synchronize_session=False)

                # advance_out is a real, valid stock movement type - product
                # can be given directly as an advance, not just cash.
                removed["stock_movements"] += s.query(StockMovement).filter(
                    StockMovement.reference_type == "advance",
                    StockMovement.reference_id.in_(advance_ids),
                ).delete(synchronize_session=False)

                removed["payments"] += s.query(Payment).filter(
                    Payment.entity_type == "advance",
                    Payment.entity_id.in_(advance_ids),
                ).delete(synchronize_session=False)

                removed["advances"] += s.query(Advance).filter(
                    Advance.id.in_(advance_ids)
                ).delete(synchronize_session=False)

            # ---- Safety net: anything still referencing this party
            # directly by party_id, not caught by the cascades above
            # (shouldn't happen in practice, but ensures nothing orphaned) ----
            removed["purchase_returns"] += s.query(PurchaseReturn).filter(
                PurchaseReturn.party_id == party_id
            ).delete(synchronize_session=False)

            removed["sale_returns"] += s.query(SaleReturn).filter(
                SaleReturn.party_id == party_id
            ).delete(synchronize_session=False)

            removed["payments"] += s.query(Payment).filter(
                Payment.party_id == party_id
            ).delete(synchronize_session=False)

            # StockMovement.party_id is a real, separate, nullable FK -
            # entirely independent of reference_type/reference_id. A row
            # tied to this party this way, but not to any purchase/sale/
            # advance the cascades above already caught, was the exact
            # gap that surfaced as a live MySQL constraint failure.
            removed["stock_movements"] += s.query(StockMovement).filter(
                StockMovement.party_id == party_id
            ).delete(synchronize_session=False)

            # ---- Finally, the party itself ----
            s.delete(p)
            s.commit()

        return removed