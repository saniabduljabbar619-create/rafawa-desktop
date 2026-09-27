# database/services/sale_adjustment_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy import func

from database.session import get_session
from database.models.sale import Sale
from database.models.sale_return import SaleReturn
from database.models.payment import Payment
from database.models.sale_discount import SaleDiscount
from database.models.advance_allocations import AdvanceAllocation
from database.models.party import Party


class SaleAdjustmentService:
    """
    The "lazy calculator" from the Payments section: search a customer,
    see their sales, apply a flat, one-time discount to a SPECIFIC sale
    that's genuinely still partial or unpaid - never to every sale they've
    ever made, and never to a sale that's already fully settled.

    Every adjustment is its own additive row (SaleDiscount) - the original
    Sale/SaleItem numbers are never touched, so the original transaction
    stays reconstructable exactly as it happened, forever.
    """

    @staticmethod
    def _sale_balance(s, sale: Sale) -> Dict[str, float]:
        """
        The real, current numbers behind one sale's balance - shared by
        both list_customer_sales (for display) and apply_discount (for
        the ceiling check), so both always agree with each other.

        "paid" and "advance-allocated" are genuinely two separate things
        in this schema: applying an advance to a sale (AdvanceService.
        allocate_fifo) never creates a Payment row at all - it's recorded
        ONLY in AdvanceAllocation. Missing this table entirely (as an
        earlier version of this method did) meant a sale settled partly
        by advance and partly by cash would still show most of its total
        as outstanding, even once genuinely fully paid - matching the
        established, correct pattern already used in dashboard_service.py's
        get_sale_payment_snapshot: settled = paid + alloc_total.
        """
        raw_total = float(sale.total_amount or 0.0)

        paid = float(
            s.query(func.coalesce(func.sum(Payment.amount), 0.0))
            .filter(Payment.entity_type == "sale")
            .filter(Payment.entity_id == sale.id)
            .filter(Payment.direction == "in")
            .filter(Payment.method != "advance")
            .filter(Payment.status != "voided")
            .scalar() or 0.0
        )

        alloc_total = float(
            s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
            .filter(AdvanceAllocation.entity_type == "sale")
            .filter(AdvanceAllocation.entity_id == sale.id)
            .scalar() or 0.0
        )
        settled = paid + alloc_total

        returned = abs(float(
            s.query(func.coalesce(func.sum(SaleReturn.amount), 0.0))
            .filter(SaleReturn.sale_id == sale.id)
            .scalar() or 0.0
        ))

        existing_discounts = float(
            s.query(func.coalesce(func.sum(SaleDiscount.amount), 0.0))
            .filter(SaleDiscount.sale_id == sale.id)
            .scalar() or 0.0
        )

        net_total = max(raw_total - returned - existing_discounts, 0.0)
        balance = max(net_total - settled, 0.0)

        return {
            "raw_total": raw_total,
            "paid": settled,
            "paid_cash": paid,
            "paid_via_advance": alloc_total,
            "returned": returned,
            "existing_discounts": existing_discounts,
            "net_total": net_total,
            "balance": balance,
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