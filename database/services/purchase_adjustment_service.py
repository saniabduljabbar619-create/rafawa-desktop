# database/services/purchase_adjustment_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy import func

from database.session import get_session
from database.models.purchase import Purchase
from database.models.purchase_return import PurchaseReturn
from database.models.payment import Payment
from database.models.purchase_price_adjustment import PurchasePriceAdjustment, PurchasePriceAdjustmentLine
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

    # ------------------------------------------------------------------ edit prices product by product
    @staticmethod
    def _check_may_edit(s, purchase: Purchase) -> None:
        """Admin and cashier may edit any purchase's prices; a weigher only the purchases of their OWN tickets."""
        from database.models.weigh_ticket import WeighTicket
        from shared.auth.permissions import PermissionDenied, current_role
        from shared.auth.session import get_session as get_login_session

        role = current_role()
        if role in ("admin", "cashier"):
            return
        if role == "weigher":
            uid = int(getattr(get_login_session(), "user_id", 0) or 0)
            own = s.query(WeighTicket.id).filter(WeighTicket.purchase_id == int(purchase.id),
                                                 WeighTicket.weigher_user_id == uid).first()
            if own:
                return
            raise PermissionDenied("A weigher can only edit the prices of their own weighings.")
        raise PermissionDenied("You are not allowed to edit purchase prices.")

    @staticmethod
    def line_prices(purchase_id: int, session=None) -> List[Dict[str, Any]]:
        """
        Each product line of a purchase with the price it carries NOW (the recorded price plus every earlier edit),
        what it is priced per (kg for crops, bag for fertilizer) and the line's current amount.
        """
        if session is not None:
            return PurchaseAdjustmentService._line_prices(session, purchase_id)
        with get_session() as s:
            return PurchaseAdjustmentService._line_prices(s, purchase_id)

    @staticmethod
    def _line_prices(s, purchase_id: int) -> List[Dict[str, Any]]:
        return PurchaseAdjustmentService.line_prices_many(s, [int(purchase_id)]).get(int(purchase_id), [])

    @staticmethod
    def line_prices_many(s, purchase_ids) -> Dict[int, List[Dict[str, Any]]]:
        """line_prices for many purchases in three queries (items, product names, earlier edits) - for lists."""
        from database.models.product import Product
        from database.models.purchase_item import PurchaseItem

        pids = sorted({int(p) for p in purchase_ids if p})
        out: Dict[int, List[Dict[str, Any]]] = {p: [] for p in pids}
        if not pids:
            return out
        items = s.query(PurchaseItem).filter(PurchaseItem.purchase_id.in_(pids)).order_by(PurchaseItem.id).all()
        names = {int(p.id): p.name for p in s.query(Product).filter(
            Product.id.in_(sorted({int(i.product_id) for i in items}) or [0])).all()}
        deltas = {int(iid): float(a or 0) for iid, a in
                  s.query(PurchasePriceAdjustmentLine.purchase_item_id,
                          func.coalesce(func.sum(PurchasePriceAdjustmentLine.amount), 0))
                  .filter(PurchasePriceAdjustmentLine.purchase_item_id.in_([int(i.id) for i in items] or [0]))
                  .group_by(PurchasePriceAdjustmentLine.purchase_item_id).all()}
        for it in items:
            fert = it.product_type_snapshot == "fertilizer"
            basis = float(it.qty_bags or 0) if fert else float(it.total_weight_kg or 0)
            original = float(it.unit_cost_rate or 0)
            extra = deltas.get(int(it.id), 0.0)
            current = original + (extra / basis if basis else 0.0)
            out[int(it.purchase_id)].append({
                "item_id": int(it.id), "product": names.get(int(it.product_id), f"Product#{it.product_id}"),
                "unit": "bag" if fert else "kg", "qty": basis, "bags": int(it.qty_bags or 0),
                "kg": float(it.total_weight_kg or 0), "original_rate": original,
                "rate": round(current, 2), "amount": round(float(it.line_total or 0) + extra, 2),
            })
        return out

    @staticmethod
    def edit_prices(purchase_id: int, new_rates: Dict[int, float], created_by_user_id: int, reason: str = "") -> int:
        """
        Change the price of one or more products on a purchase already recorded. new_rates = {item_id: new price per
        kg/bag}. Only lines whose price really changes are touched. The difference (new - old) x quantity is saved as
        ONE additive PurchasePriceAdjustment, with a detail row per product - the original rows stay exactly as
        recorded, so the books can always be read back as they were. Raises ValueError and saves nothing if a price
        is not a positive number, nothing changed, or the new total would fall below what is already paid.
        """
        purchase_id = int(purchase_id or 0)
        if purchase_id <= 0:
            raise ValueError("purchase_id is required.")
        if int(created_by_user_id or 0) <= 0:
            raise ValueError("created_by_user_id is required.")
        current = {l["item_id"]: l for l in PurchaseAdjustmentService.line_prices(purchase_id)}
        changes = []
        for item_id, raw in (new_rates or {}).items():
            line = current.get(int(item_id))
            if line is None:
                raise ValueError("That product line is not on this purchase.")
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
            purchase = s.get(Purchase, purchase_id)
            if not purchase:
                raise ValueError("Purchase not found.")
            if (purchase.status or "") != "completed":
                raise ValueError("Only a completed purchase can have its prices edited.")
            PurchaseAdjustmentService._check_may_edit(s, purchase)
            nums = PurchaseAdjustmentService._purchase_balance(s, purchase)
            if total < 0 and nums["net_total"] + total < nums["paid"] - 0.01:
                raise ValueError(
                    f"This lowers the total by \u20a6{abs(total):,.2f}, below the \u20a6{nums['paid']:,.2f} already "
                    "paid. Void or reduce a payment first.")
            what = "; ".join(f"{l['product']} {l['rate']:,.2f}->{n:,.2f}/{l['unit']} x {l['qty']:,.0f}" for l, n, _a in changes)
            row = PurchasePriceAdjustment(
                purchase_id=purchase_id, amount=total, created_by_user_id=int(created_by_user_id),
                reason=(("Price edit: " + what + (f" - {reason.strip()}" if (reason or "").strip() else ""))[:255]))
            s.add(row)
            s.flush()
            for l, n, a in changes:
                s.add(PurchasePriceAdjustmentLine(adjustment_id=int(row.id), purchase_item_id=l["item_id"],
                                                  old_rate=l["rate"], new_rate=n, basis_qty=l["qty"],
                                                  basis_unit=l["unit"], amount=a))
            from shared.auth.permissions import current_role
            if current_role() == "weigher":
                # a weigher correcting their own weighing: their price follows (admin / cashier edits never move it)
                from database.models.weigh_ticket import WeighTicket
                tk = s.query(WeighTicket).filter(WeighTicket.purchase_id == purchase_id).first()
                if tk is not None:
                    weighed = [ln for ln in tk.lines if ln.rounds]
                    position = {l["item_id"]: i for i, l in enumerate(current.values())}
                    if len(weighed) == len(current):
                        for l, n, _a in changes:
                            weighed[position[l["item_id"]]].weigher_price = n
            from database.services.audit_service import AuditService
            AuditService.record("purchase.price_edit", "purchase", purchase_id,
                                f"{purchase.purchase_no or f'PUR-{purchase_id}'}: {what} (total {total:+,.2f})", session=s)
            s.commit()
            return int(row.id)

