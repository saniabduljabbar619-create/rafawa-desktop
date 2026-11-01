# database/services/sale_adjustment_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy import func

from database.session import get_session
from database.models.sale import Sale
from database.models.sale_return import SaleReturn
from database.models.payment import Payment
from database.models.sale_discount import SaleDiscount, SalePriceAdjustmentLine
from database.models.advance_allocations import AdvanceAllocation
from database.models.party import Party
from database.services.document_balance import document_balances


class SaleAdjustmentService:
    """
    The "lazy calculator" from the Payments section: search a customer,
    see their sales, apply a flat, one-time discount to a SPECIFIC sale
    that's genuinely still partial or unpaid - or edit product line prices
    on any completed sale via edit_prices.

    Every adjustment is its own additive row (SaleDiscount, plus per-line
    SalePriceAdjustmentLine when edited by product) - the original
    Sale/SaleItem numbers are never touched, so the original transaction
    stays reconstructable exactly as it happened, forever.
    """

    @staticmethod
    def _sale_balance(s, sale: Sale) -> Dict[str, float]:
        """
        The real, current numbers behind one sale's balance - shared by list_customer_sales (display),
        apply_discount (ceiling check), edit_prices, and the payment dialogs.

        These come from the ONE shared definition (document_balance.document_balances): cash payments,
        advance allocations AND offsets/swaps against the party's purchases all count as settled, a
        voided sale owes nothing, and returns and discounts reduce the total. (This method used to keep
        its own copy of that arithmetic, which ignored offsets - a sale settled by a swap still showed
        its full total as outstanding in the payment screens.)
        """
        b = document_balances(s, "sale", [int(sale.id)])[int(sale.id)]
        return {
            "raw_total": b.gross,
            "paid": b.settled,
            "paid_cash": b.cash_paid,
            "paid_via_advance": b.advance_applied,
            "paid_via_offset": b.offset_applied,
            "returned": b.returned,
            "existing_discounts": b.discount,
            "net_total": b.net_total,
            "balance": b.balance,
        }

    @staticmethod
    def list_customer_sales(party_id: int) -> List[Dict[str, Any]]:
        """
        Every completed sale for this customer, each with its real, current
        balance - the screen uses this to show which sales are selectable
        (partial/unpaid) versus locked (already fully paid).
        """
        party_id = int(party_id or 0)
        if party_id <= 0:
            return []

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == party_id)
                .filter(Sale.status == "completed")
                .order_by(Sale.created_at.desc())
                .all()
            )

            for sale in sales:
                nums = SaleAdjustmentService._sale_balance(s, sale)
                if nums["net_total"] <= 0:
                    status = "paid"
                elif nums["paid"] <= 0:
                    status = "unpaid"
                elif nums["balance"] > 0:
                    status = "partial"
                else:
                    status = "paid"

                out.append({
                    "sale_id": int(sale.id),
                    "sale_no": sale.sale_no or f"SAL-{sale.id}",
                    "created_at": sale.created_at,
                    "total_amount": nums["raw_total"],
                    "paid": nums["paid"],
                    "returned": nums["returned"],
                    "existing_discounts": nums["existing_discounts"],
                    "net_total": nums["net_total"],
                    "balance": nums["balance"],
                    "status": status,
                    "selectable": status in ("partial", "unpaid"),
                })
        return out

    @staticmethod
    def get_sale_balance(sale_id: int) -> Dict[str, Any]:
        """The current, real numbers for one sale - used to populate the
        discount screen right before the operator applies anything."""
        sale_id = int(sale_id or 0)
        with get_session() as s:
            sale = s.get(Sale, sale_id)
            if not sale:
                raise ValueError("Sale not found.")
            nums = SaleAdjustmentService._sale_balance(s, sale)
            nums["sale_id"] = int(sale.id)
            nums["sale_no"] = sale.sale_no or f"SAL-{sale.id}"
            return nums

    @staticmethod
    def apply_discount(sale_id: int, amount: float, created_by_user_id: int,
                       reason: str = "") -> int:
        """
        Applies a flat discount to a specific sale's remaining balance.

        Re-checks the sale's REAL, CURRENT balance at this exact moment,
        not whatever the screen showed when it was first opened - a
        customer could have paid in full in the gap between opening this
        screen and clicking Apply, and whichever payment happened first
        must win. Raises rather than silently clamping if the amount
        would exceed what's actually still outstanding, or if the sale
        has already become fully paid.
        """
        sale_id = int(sale_id or 0)
        amount = float(amount or 0.0)
        created_by_user_id = int(created_by_user_id or 0)

        if sale_id <= 0:
            raise ValueError("sale_id is required.")
        if amount <= 0:
            raise ValueError("Discount amount must be greater than zero.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        with get_session() as s:
            sale = s.get(Sale, sale_id)
            if not sale:
                raise ValueError("Sale not found.")

            nums = SaleAdjustmentService._sale_balance(s, sale)
            if nums["net_total"] <= 0:
                raise ValueError(
                    "This sale has no outstanding balance to discount - it "
                    "may have just been paid in full or fully returned."
                )
            if nums["balance"] <= 0:
                raise ValueError(
                    "This sale is already fully paid - a discount can no "
                    "longer be applied to it."
                )
            if amount > nums["balance"]:
                raise ValueError(
                    f"Discount (\u20a6{amount:,.2f}) cannot exceed the "
                    f"remaining balance (\u20a6{nums['balance']:,.2f})."
                )

            row = SaleDiscount(
                sale_id=sale_id,
                amount=amount,
                reason=(reason or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(row)
            s.flush()
            new_id = int(row.id)
            return new_id

    # ------------------------------------------------------------------ edit prices product by product
    @staticmethod
    def _check_may_edit(s, sale: Sale) -> None:
        """Admin and cashier may edit any sale's prices; a weigher only the sales of their OWN tickets."""
        from database.models.weigh_ticket import WeighTicket
        from shared.auth.permissions import PermissionDenied, current_role
        from shared.auth.session import get_session as get_login_session

        role = current_role()
        if role in ("admin", "cashier"):
            return
        if role == "weigher":
            uid = int(getattr(get_login_session(), "user_id", 0) or 0)
            own = s.query(WeighTicket.id).filter(WeighTicket.sale_id == int(sale.id),
                                                 WeighTicket.weigher_user_id == uid).first()
            if own:
                return
            raise PermissionDenied("A weigher can only edit the prices of their own weighings.")
        raise PermissionDenied("You are not allowed to edit sale prices.")

    @staticmethod
    def line_prices(sale_id: int, session=None) -> List[Dict[str, Any]]:
        """
        Each product line of a sale with the price it carries NOW (the recorded price plus every earlier edit),
        what it is priced per (kg for crops, bag for fertilizer) and the line's current amount.
        """
        if session is not None:
            return SaleAdjustmentService._line_prices(session, sale_id)
        with get_session() as s:
            return SaleAdjustmentService._line_prices(s, sale_id)

    @staticmethod
    def _line_prices(s, sale_id: int) -> List[Dict[str, Any]]:
        return SaleAdjustmentService.line_prices_many(s, [int(sale_id)]).get(int(sale_id), [])

    @staticmethod
    def line_prices_many(s, sale_ids) -> Dict[int, List[Dict[str, Any]]]:
        """line_prices for many sales in three queries (items, product names, earlier edits) - for lists."""
        from database.models.product import Product
        from database.models.sale_item import SaleItem

        sids = sorted({int(sid) for sid in sale_ids if sid})
        out: Dict[int, List[Dict[str, Any]]] = {sid: [] for sid in sids}
        if not sids:
            return out
        items = s.query(SaleItem).filter(SaleItem.sale_id.in_(sids)).order_by(SaleItem.id).all()
        names = {int(p.id): p.name for p in s.query(Product).filter(
            Product.id.in_(sorted({int(i.product_id) for i in items}) or [0])).all()}
        deltas = {int(iid): float(a or 0) for iid, a in
                  s.query(SalePriceAdjustmentLine.sale_item_id,
                          func.coalesce(func.sum(SalePriceAdjustmentLine.amount), 0))
                  .filter(SalePriceAdjustmentLine.sale_item_id.in_([int(i.id) for i in items] or [0]))
                  .group_by(SalePriceAdjustmentLine.sale_item_id).all()}
        for it in items:
            fert = it.product_type_snapshot == "fertilizer"
            basis = float(it.qty_bags or 0) if fert else float(it.total_weight_kg or 0)
            original = float(it.unit_sale_rate or 0)
            extra = deltas.get(int(it.id), 0.0)
            current = original + (extra / basis if basis else 0.0)
            out[int(it.sale_id)].append({
                "item_id": int(it.id), "product": names.get(int(it.product_id), f"Product#{it.product_id}"),
                "unit": "bag" if fert else "kg", "qty": basis, "bags": int(it.qty_bags or 0),
                "kg": float(it.total_weight_kg or 0), "original_rate": original,
                "rate": round(current, 2), "amount": round(float(it.line_total or 0) + extra, 2),
            })
        return out

    @staticmethod
    def edit_prices(sale_id: int, new_rates: Dict[int, float], created_by_user_id: int, reason: str = "") -> int:
        """
        Change the price of one or more products on a sale already recorded. new_rates = {item_id: new price per
        kg/bag}. Only lines whose price really changes are touched. The difference (new - old) x quantity is saved as
        ONE additive SaleDiscount (with amount = -total, because SaleDiscount is subtracted from the sale gross),
        with a SalePriceAdjustmentLine detail row per product - the original rows stay exactly as recorded, so the
        books can always be read back as they were. Raises ValueError and saves nothing if a price is not a positive
        number, nothing changed, or the new total would fall below what is already paid.
        """
        sale_id = int(sale_id or 0)
        if sale_id <= 0:
            raise ValueError("sale_id is required.")
        if int(created_by_user_id or 0) <= 0:
            raise ValueError("created_by_user_id is required.")
        current = {l["item_id"]: l for l in SaleAdjustmentService.line_prices(sale_id)}
        changes = []
        for item_id, raw in (new_rates or {}).items():
            line = current.get(int(item_id))
            if line is None:
                raise ValueError("That product line is not on this sale.")
            try:
                new = round(float(raw), 2)
            except (TypeError, ValueError):
                raise ValueError(f"{line['product']}: type the price as a number.")
            if new <= 0:
                raise ValueError(f"{line['product']}: the price must be more than zero.")
            if abs(new - line["rate"]) < 0.005:
                continue
            amount = round((new - line["rate"]) * line["qty"], 2)
            if amount == 0:
                continue
            changes.append((line, new, amount))
        if not changes:
            raise ValueError("No price was changed.")
        total = round(sum(a for _l, _n, a in changes), 2)

        with get_session() as s:
            sale = s.get(Sale, sale_id)
            if not sale:
                raise ValueError("Sale not found.")
            if (sale.status or "") != "completed":
                raise ValueError("Only a completed sale can have its prices edited.")
            SaleAdjustmentService._check_may_edit(s, sale)
            nums = SaleAdjustmentService._sale_balance(s, sale)
            if total < 0 and nums["net_total"] + total < nums["paid"] - 0.01:
                raise ValueError(
                    f"This lowers the total by \u20a6{abs(total):,.2f}, below the \u20a6{nums['paid']:,.2f} already "
                    "paid. Void or reduce a payment first.")
            what = "; ".join(f"{l['product']} {l['rate']:,.2f}->{n:,.2f}/{l['unit']} x {l['qty']:,.0f}" for l, n, _a in changes)
            row = SaleDiscount(
                sale_id=sale_id, amount=-total, created_by_user_id=int(created_by_user_id),
                reason=(("Price edit: " + what + (f" - {reason.strip()}" if (reason or "").strip() else ""))[:255]))
            s.add(row)
            s.flush()
            for l, n, a in changes:
                s.add(SalePriceAdjustmentLine(discount_id=int(row.id), sale_item_id=l["item_id"],
                                              old_rate=l["rate"], new_rate=n, basis_qty=l["qty"],
                                              basis_unit=l["unit"], amount=a))
            from shared.auth.permissions import current_role
            if current_role() == "weigher":
                from database.models.weigh_ticket import WeighTicket
                tk = s.query(WeighTicket).filter(WeighTicket.sale_id == sale_id).first()
                if tk is not None:
                    weighed = [ln for ln in tk.lines if ln.rounds]
                    position = {l["item_id"]: i for i, l in enumerate(current.values())}
                    if len(weighed) == len(current):
                        for l, n, _a in changes:
                            weighed[position[l["item_id"]]].weigher_price = n
            from database.services.audit_service import AuditService
            AuditService.record("sale.price_edit", "sale", sale_id,
                                f"{sale.sale_no or f'SAL-{sale_id}'}: {what} (total {total:+,.2f})", session=s)
            s.commit()
            return int(row.id)
