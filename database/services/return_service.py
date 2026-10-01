# database/services/return_service.py

from __future__ import annotations

from typing import Optional, Tuple, cast
from sqlalchemy import func
from database.session import get_session

from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.purchase_return import PurchaseReturn

from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.sale_return import SaleReturn

from database.models.stock_movement import StockMovement
from database.models.advance_allocations import AdvanceAllocation


# Tolerance for float kg comparisons: the UI clamps to a 2-decimal spin box,
# so the "remaining" figure (sold - returned) can differ from what the user
# can type by float noise (e.g. 100.1 - 33.7 = 66.39999999999999).
_KG_EPS = 1e-6


class ReturnService:

    # ---------------------------------------------------------
    # INTERNAL HELPERS (🛡️ REFACTORED TO ENFORCE PURE SCALARS)
    # ---------------------------------------------------------
    @staticmethod
    def _sum_purchase_returned(s, purchase_item_id: int) -> Tuple[float, int]:
        qty_kg = s.query(func.sum(PurchaseReturn.qty_kg)).filter(
            PurchaseReturn.purchase_item_id == purchase_item_id
        ).scalar() or 0.0
        
        qty_bags = s.query(func.sum(PurchaseReturn.qty_bags)).filter(
            PurchaseReturn.purchase_item_id == purchase_item_id
        ).scalar() or 0
        
        return float(qty_kg), int(qty_bags)

    @staticmethod
    def _sum_sale_returned(s, sale_item_id: int) -> Tuple[float, int]:
        qty_kg = s.query(func.sum(SaleReturn.qty_kg)).filter(
            SaleReturn.sale_item_id == sale_item_id
        ).scalar() or 0.0
        
        qty_bags = s.query(func.sum(SaleReturn.qty_bags)).filter(
            SaleReturn.sale_item_id == sale_item_id
        ).scalar() or 0
        
        return float(qty_kg), int(qty_bags)

    # ---------------------------------------------------------
    # CREATE RETURN
    # ---------------------------------------------------------
    @staticmethod
    def create_return(
        *,
        document_type: str,  # "purchase" or "sale"
        document_id: int,
        item_id: int,
        qty_kg: float,
        qty_bags: int,
        reason: Optional[str],
        created_by_user_id: int,
    ) -> int:

        document_type = (document_type or "").strip().lower()

        if document_type not in ("purchase", "sale"):
            raise ValueError("document_type must be 'purchase' or 'sale'.")

        qty_bags = int(qty_bags or 0)
        qty_kg = float(qty_kg or 0.0)

        if qty_bags < 0 or qty_kg < 0:
            raise ValueError("Return quantity cannot be negative.")

        if qty_bags <= 0 and qty_kg <= 0:
            raise ValueError("Return quantity must be provided.")

        with get_session() as s:

            # ==========================================================
            # PURCHASE RETURN TRACK
            # ==========================================================
            if document_type == "purchase":
                purchase = s.query(Purchase).filter(Purchase.id == document_id).first()

                if not purchase:
                    raise ValueError("Purchase not found.")

                if purchase.status != "completed":
                    raise ValueError("Only completed purchases can be returned.")

                item = (
                    s.query(PurchaseItem)
                    .filter(PurchaseItem.id == item_id)
                    .filter(PurchaseItem.purchase_id == document_id)
                    .first()
                )

                if not item:
                    raise ValueError("Purchase item not found.")

                returned_kg, returned_bags = ReturnService._sum_purchase_returned(s, item_id)

                sold_kg = float(item.total_weight_kg or 0.0)
                sold_bags = int(item.qty_bags or 0)

                ptype = str(item.product_type_snapshot or "").strip().lower()
                is_fert = ptype in ("fertilizer", "fertilizers")

                if is_fert:
                    if qty_kg > 0:
                        raise ValueError("Fertilizer returns are bags-only.")
                    qty_kg = 0.0
                else:
                    if qty_kg <= 0:
                        raise ValueError("Crop return requires qty_kg.")

                if qty_bags > (sold_bags - returned_bags):
                    raise ValueError("Return bags exceed remaining quantity.")

                if not is_fert and qty_kg > (sold_kg - returned_kg) + _KG_EPS:
                    raise ValueError("Return kg exceed remaining quantity.")

                rate = float(item.unit_cost_rate or 0.0)
                amount = -(rate * (qty_bags if is_fert else qty_kg))

                r = PurchaseReturn(
                    purchase_id=purchase.id,
                    purchase_item_id=item.id,
                    party_id=purchase.supplier_party_id,
                    product_id=item.product_id,
                    product_type_snapshot=item.product_type_snapshot,
                    unit=("bag" if is_fert else "kg"),
                    unit_cost_snapshot=rate,
                    qty_kg=(0.0 if is_fert else qty_kg),
                    qty_bags=qty_bags,
                    amount=amount,
                    reason=reason,
                    created_by_user_id=created_by_user_id,
                )

                s.add(r)
                s.flush()

                return_id = cast(int, r.id)

                sm = StockMovement(
                    product_id=item.product_id,
                    movement_type="adjustment_out",
                    qty_bags=qty_bags,
                    total_weight_kg=(0.0 if is_fert else qty_kg),
                    unit_rate=rate,
                    reference_type="purchase_return",
                    reference_id=return_id,
                    party_id=purchase.supplier_party_id,
                    created_by_user_id=created_by_user_id,
                )
                s.add(sm)

                # RELEASE GATES: Re-open supplier pre-funding balance spaces
                return_val_to_release = abs(float(amount))
                if return_val_to_release > 0:
                    allocations = (
                        s.query(AdvanceAllocation)
                        .filter(
                            AdvanceAllocation.entity_type == "purchase",
                            AdvanceAllocation.entity_id == document_id
                        )
                        .order_by(AdvanceAllocation.id.desc())
                        .all()
                    )
                    for alloc in allocations:
                        alloc_amt = float(alloc.amount or 0.0)
                        if return_val_to_release >= alloc_amt:
                            return_val_to_release -= alloc_amt
                            s.delete(alloc)
                        else:
                            alloc.amount = alloc_amt - return_val_to_release
                            return_val_to_release = 0.0
                            break

                    # NOTE: leftover value after releasing the supplier advance
                    # is NOT credited here. What you actually paid the supplier in
                    # cash is reclaimed at approval (approve_purchase_return); the
                    # unpaid portion simply clears with the return.

                s.commit()

                return return_id

            # ==========================================================
            # SALE RETURN
            # ==========================================================
            sale = s.query(Sale).filter(Sale.id == document_id).first()

            if not sale:
                raise ValueError("Sale not found.")

            if sale.status != "completed":
                raise ValueError("Only completed sales can be returned.")

            item = (
                s.query(SaleItem)
                .filter(SaleItem.id == item_id)
                .filter(SaleItem.sale_id == document_id)
                .first()
            )

            if not item:
                raise ValueError("Sale item not found.")

            returned_kg, returned_bags = ReturnService._sum_sale_returned(s, item_id)

            sold_kg = float(item.total_weight_kg or 0.0)
            sold_bags = int(item.qty_bags or 0)

            ptype = str(item.product_type_snapshot or "").strip().lower()
            is_fert = ptype in ("fertilizer", "fertilizers")

            if is_fert:
                if qty_kg > 0:
                    raise ValueError("Fertilizer returns are bags-only.")
                qty_kg = 0.0
            else:
                if qty_kg <= 0:
                    raise ValueError("Crop return requires qty_kg.")

            if qty_bags > (sold_bags - returned_bags):
                raise ValueError("Return bags exceed remaining quantity.")

            if not is_fert and qty_kg > (sold_kg - returned_kg) + _KG_EPS:
                raise ValueError("Return kg exceed remaining quantity.")

            rate = float(item.unit_sale_rate or 0.0)
            amount = -(rate * (qty_bags if is_fert else qty_kg))

            r = SaleReturn(
                sale_id=sale.id,
                sale_item_id=item.id,
                party_id=sale.buyer_party_id,
                product_id=item.product_id,
                product_type_snapshot=item.product_type_snapshot,
                unit=("bag" if is_fert else "kg"),
                unit_price_snapshot=rate,
                qty_kg=(0.0 if is_fert else qty_kg),
                qty_bags=qty_bags,
                amount=amount,
                reason=reason,
                created_by_user_id=created_by_user_id,
            )

            s.add(r)
            s.flush()

            return_id = cast(int, r.id)

            sm = StockMovement(
                product_id=item.product_id,
                movement_type="return_in",
                qty_bags=qty_bags,
                total_weight_kg=(0.0 if is_fert else qty_kg),
                unit_rate=rate,
                reference_type="sale_return",
                reference_id=return_id,
                party_id=sale.buyer_party_id,
                created_by_user_id=created_by_user_id,
            )
            s.add(sm)

            # RELEASE GATES: Re-open customer store credit balance spaces
            return_val_to_release = abs(float(amount))
            if return_val_to_release > 0:
                allocations = (
                    s.query(AdvanceAllocation)
                    .filter(
                        AdvanceAllocation.entity_type == "sale",
                        AdvanceAllocation.entity_id == document_id
                    )
                    .order_by(AdvanceAllocation.id.desc())
                    .all()
                )
                for alloc in allocations:
                    alloc_amt = float(alloc.amount or 0.0)
                    if return_val_to_release >= alloc_amt:
                        return_val_to_release -= alloc_amt
                        s.delete(alloc)
                    else:
                        alloc.amount = alloc_amt - return_val_to_release
                        return_val_to_release = 0.0
                        break

                # NOTE: any value left after releasing the advance allocation is
                # NOT credited anywhere here. The paid portion is refunded as CASH
                # at approval (approve_sale_return); the unpaid portion simply
                # clears with the return. create_return only reverses goods and
                # releases the advance - it never moves cash.

            s.commit()

            return return_id

    # ---------------------------------------------------------
    # APPROVE RETURN
    # ---------------------------------------------------------
    @staticmethod
    def approve_purchase_return(return_id: int, created_by_user_id: int):
        from database.models.payment import Payment
        from sqlalchemy import func

        with get_session() as s:
            r = s.get(PurchaseReturn, return_id)
            if not r:
                raise ValueError("Return not found")

            return_value = abs(float(r.amount or 0.0))

            # What you actually paid the supplier in CASH (exclude advance-funded
            # settlements), minus anything already reclaimed on prior returns.
            cash_out = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                Payment.entity_type == "purchase",
                Payment.entity_id == r.purchase_id,
                Payment.direction == "out",
                Payment.method != "advance",
                Payment.status != "voided",
            ).scalar() or 0.0
            already_reclaimed = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                Payment.entity_type == "purchase",
                Payment.entity_id == r.purchase_id,
                Payment.direction == "in",
                Payment.status != "voided",
            ).scalar() or 0.0

            reclaimable = max(float(cash_out) - float(already_reclaimed), 0.0)
            reclaim = min(return_value, reclaimable)
            if reclaim <= 0:
                return  # nothing paid in cash; unpaid portion just clears

            pay = Payment(
                party_id=r.party_id,
                entity_type="purchase",
                entity_id=r.purchase_id,
                direction="in",
                amount=reclaim,
                method="refund",
                notes=f"Cash reclaim for Purchase Return (RET-{r.id})",
                created_by_user_id=created_by_user_id
            )
            s.add(pay)
            s.commit()

    @staticmethod
    def approve_sale_return(return_id: int, created_by_user_id: int):
        from database.models.payment import Payment
        from sqlalchemy import func

        with get_session() as s:
            r = s.get(SaleReturn, return_id)
            if not r:
                raise ValueError("Return not found")

            return_value = abs(float(r.amount or 0.0))

            # Cash the customer actually paid on this sale (exclude advance-funded
            # settlements), minus any refunds already handed back on prior returns.
            cash_in = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                Payment.entity_type == "sale",
                Payment.entity_id == r.sale_id,
                Payment.direction == "in",
                Payment.method != "advance",
                Payment.status != "voided",
            ).scalar() or 0.0
            already_refunded = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                Payment.entity_type == "sale",
                Payment.entity_id == r.sale_id,
                Payment.direction == "out",
                Payment.status != "voided",
            ).scalar() or 0.0

            refundable = max(float(cash_in) - float(already_refunded), 0.0)
            refund = min(return_value, refundable)
            if refund <= 0:
                return  # nothing paid in cash; unpaid portion just clears

            pay = Payment(
                party_id=r.party_id,
                entity_type="sale",
                entity_id=r.sale_id,
                direction="out",
                amount=refund,
                method="refund",
                notes=f"Cash refund for Sale Return (RET-{r.id})",
                created_by_user_id=created_by_user_id
            )
            s.add(pay)
            s.commit()