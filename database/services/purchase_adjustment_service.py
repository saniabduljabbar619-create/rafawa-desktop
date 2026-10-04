# database/services/purchase_adjustment_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy import func

from database.session import get_session
from database.models.purchase import Purchase
from database.models.purchase_return import PurchaseReturn
from database.models.payment import Payment
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.advance_allocations import AdvanceAllocation
from database.models.party import Party
from database.services.document_balance import document_balances


class PurchaseAdjustmentService:
    """
    A supplier renegotiating an already-recorded purchase's price after
    the fact - e.g. "actually, make it 370/kg instead of 350/kg" -
    mirrors SaleAdjustmentService's exact structure, for the purchase
    side. The key difference from a sale discount: this works
    regardless of the purchase's current payment status. A discount can
    only ever reduce a still-outstanding balance, but a price increase
    is meaningful either way - if the purchase was already fully paid,
    the new amount becomes a fresh, genuine balance and the purchase
    correctly reappears as needing payment; if it was already partial
    or unpaid, the increase simply adds to what's already outstanding.

    Every adjustment is its own additive row (PurchasePriceAdjustment) -
    the original Purchase/PurchaseItem numbers are never touched, so the
    original transaction stays reconstructable exactly as it happened,
    forever.
    """

    @staticmethod
    def _purchase_balance(s, purchase: Purchase) -> Dict[str, float]:
        """
        The real, current numbers behind one purchase's balance - shared by list_supplier_purchases,
        apply_adjustment and the payment dialogs.

        Taken from the ONE shared definition (document_balance.document_balances): cash paid, advance
        allocations AND offsets/swaps against the party's sales count as settled, a voided purchase owes
        nothing, and returns and price adjustments change the total. (This method used to keep its own
        copy of that arithmetic, which ignored offsets - a purchase settled by a swap still showed its
        full total as outstanding in the payment screens.)
        """
        b = document_balances(s, "purchase", [int(purchase.id)])[int(purchase.id)]
        return {
            "raw_total": b.gross,
            "paid": b.settled,
            "paid_cash": b.cash_paid,
            "paid_via_advance": b.advance_applied,
            "paid_via_offset": b.offset_applied,
            "returned": b.returned,
            "existing_adjustments": b.adjustment,
            "net_total": b.net_total,
            "balance": b.balance,
        }

    @staticmethod
    def list_supplier_purchases(party_id: int) -> List[Dict[str, Any]]:
        """
        Every completed purchase for this supplier, each with its real,
        current balance. Unlike SaleAdjustmentService.list_customer_
        sales, every row is selectable here - not just partial/unpaid
        ones - since a price adjustment is meaningful on an already
        fully-paid purchase too.
        """
        party_id = int(party_id or 0)
        if party_id <= 0:
            return []

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            purchases = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(Purchase.status == "completed")
                .order_by(Purchase.created_at.desc())
                .all()
            )

            for purchase in purchases:
                nums = PurchaseAdjustmentService._purchase_balance(s, purchase)
                if nums["net_total"] <= 0.005:
                    status = "paid"
                elif nums["paid"] <= 0.005:
                    status = "unpaid"
                elif nums["balance"] > 0.005:
                    status = "partial"
                else:
                    status = "paid"

                out.append({
                    "purchase_id": int(purchase.id),
                    "purchase_no": purchase.purchase_no or f"PUR-{purchase.id}",
                    "created_at": purchase.created_at,
                    "total_amount": nums["raw_total"],
                    "paid": nums["paid"],
                    "returned": nums["returned"],
                    "existing_adjustments": nums["existing_adjustments"],
                    "net_total": nums["net_total"],
                    "balance": nums["balance"],
                    "status": status,
                })
        return out

    @staticmethod
    def get_purchase_balance(purchase_id: int) -> Dict[str, Any]:
        """The current, real numbers for one purchase - used to populate
        the adjustment screen right before the operator applies anything."""
        purchase_id = int(purchase_id or 0)
        with get_session() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                raise ValueError("Purchase not found.")
            nums = PurchaseAdjustmentService._purchase_balance(s, purchase)
            nums["purchase_id"] = int(purchase.id)
            nums["purchase_no"] = purchase.purchase_no or f"PUR-{purchase.id}"
            return nums

    @staticmethod
    def get_purchase_weight_and_products(purchase_id: int) -> Dict[str, Any]:
        """
        The total weight (kg) actually recorded across this purchase's
        line items, plus the product name(s) involved - what the
        weight/price adjustment input needs: the operator enters a rate
        (e.g. \u20a620 per 1kg), and this is what it gets multiplied
        against, rather than the operator having to compute the total
        themselves. Purchase itself has no total_weight_kg of its own -
        only PurchaseItem does, per line - so this sums across every
        line for the purchase.
        """
        from database.models.purchase_item import PurchaseItem
        from database.models.product import Product

        purchase_id = int(purchase_id or 0)
        with get_session() as s:
            items = (
                s.query(PurchaseItem)
                .filter(PurchaseItem.purchase_id == purchase_id)
                .all()
            )
            total_weight_kg = sum(float(it.total_weight_kg or 0) for it in items)

            product_ids = [it.product_id for it in items]
            names = []
            if product_ids:
                products = s.query(Product).filter(Product.id.in_(product_ids)).all()
                names = [p.name or f"Product#{p.id}" for p in products]

        return {
            "total_weight_kg": total_weight_kg,
            "product_names": names,
            "product_summary": ", ".join(names) if names else "",
        }

    @staticmethod
    def apply_adjustment(purchase_id: int, amount: float, created_by_user_id: int,
                         reason: str = "") -> int:
        """
        Applies a price correction to a specific purchase - positive for
        the common case (the supplier is owed more), negative for a
        price decrease agreed after the fact.

        Works regardless of the purchase's current payment status - see
        this class's own docstring for why that's deliberately different
        from SaleAdjustmentService.apply_discount. Re-checks the
        purchase's REAL, CURRENT balance at this exact moment, not
        whatever the screen showed when first opened. A negative
        adjustment is still bounded: it can never reduce the net total
        below what's already been genuinely paid, since that would mean
        the supplier now owes the company money back - a different,
        separate scenario this isn't meant to handle.
        """
        purchase_id = int(purchase_id or 0)
        amount = float(amount or 0.0)
        created_by_user_id = int(created_by_user_id or 0)

        if purchase_id <= 0:
            raise ValueError("purchase_id is required.")
        if amount == 0:
            raise ValueError("Adjustment amount cannot be zero.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        with get_session() as s:
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                raise ValueError("Purchase not found.")

            nums = PurchaseAdjustmentService._purchase_balance(s, purchase)

            if amount < 0:
                new_net_total = nums["net_total"] + amount  # amount is negative
                if new_net_total < nums["paid"] - 0.01:
                    raise ValueError(
                        f"This decrease (\u20a6{abs(amount):,.2f}) would take the "
                        f"total below what's already been paid "
                        f"(\u20a6{nums['paid']:,.2f}). That would mean the supplier "
                        f"owes the company money back - a different situation "
                        f"this isn't meant to handle."
                    )

            row = PurchasePriceAdjustment(
                purchase_id=purchase_id,
                amount=amount,
                reason=(reason or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(row)
            s.flush()
            new_id = int(row.id)
            return new_id