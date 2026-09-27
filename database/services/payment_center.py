# database/services/payment_center.py

from database.services.advance_service import AdvanceService
from database.services.payment_service import PaymentService

class PaymentCenter:

    # -------------------------------------------------
    # HANDLE PURCHASE SETTLEMENT
    # -------------------------------------------------

    @staticmethod
    def handle_purchase_creation(
        party_id: int,
        purchase_id: int,
        purchase_total: float,
        created_by_user_id: int
    ):

        open_advances = AdvanceService.get_fifo_advances(
            party_id=party_id,
            direction="given"
        )

        if not open_advances:
            return {
                "advance_used": 0.0,
                "remaining": purchase_total,
                "allocations": []
            }

        result = AdvanceService.allocate_fifo(
            party_id=party_id,
            entity_type="purchase",
            entity_id=purchase_id,
            transaction_total=purchase_total,
            created_by_user_id=created_by_user_id
        )

        allocated = sum(a["amount"] for a in result["allocated"])

        # -------------------------------------------------
        # CREATE PURCHASE PAYMENT RECORD (advance settlement)
        # -------------------------------------------------

        if allocated > 0:

            PaymentService.record_purchase_payment(
                purchase_id=purchase_id,
                party_id=party_id,
                amount=allocated,
                created_by_user_id=created_by_user_id,
                method="advance",
                notes="Advance settlement"
            )

        return {
            "advance_used": allocated,
            "remaining": result["remaining_transaction"],
            "allocations": result["allocated"]
        }

    # -------------------------------------------------
    # HANDLE SALE SETTLEMENT
    # -------------------------------------------------

    @staticmethod
    def handle_sale_creation(
        party_id: int,
        sale_id: int,
        sale_total: float,
        created_by_user_id: int
    ):

        open_advances = AdvanceService.get_fifo_advances(
            party_id=party_id,
            direction="received"
        )

        if not open_advances:
            return {
                "advance_used": 0.0,
                "remaining": sale_total,
                "allocations": []
            }

        result = AdvanceService.allocate_fifo(
            party_id=party_id,
            entity_type="sale",
            entity_id=sale_id,
            transaction_total=sale_total,
            created_by_user_id=created_by_user_id
        )

        allocated = min(
            sum(a["amount"] for a in result["allocated"]),
            sale_total
        )

        # -------------------------------------------------
        # CREATE SALE PAYMENT RECORD (advance settlement)
        # -------------------------------------------------
        if allocated > 0:

            PaymentService.record_sale_payment(
                sale_id=sale_id,
                party_id=party_id,
                amount=allocated,
                created_by_user_id=created_by_user_id,
                method="advance",
                notes="Advance settlement"
            )

        return {
            "advance_used": allocated,
            "remaining": result["remaining_transaction"],
            "allocations": result["allocated"]
        }
    

    @staticmethod
    def handle_supplier_advance_settlement(party_id: int, created_by_user_id: int, skip_purchase_id: int = 0):

        from database.models.purchase import Purchase
        from database.models.payment import Payment
        from database.models.advance_allocations import AdvanceAllocation
        from database.session import get_session
        from sqlalchemy import func

        with get_session() as s:

            purchases = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == party_id)
                .all()
            )

            for purchase in purchases:

                # Don't re-apply the freed advance to a purchase being returned.
                if skip_purchase_id and purchase.id == skip_purchase_id:
                    continue

                paid = (
                    s.query(func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "purchase")
                    .filter(Payment.entity_id == purchase.id)
                    .filter(Payment.status != "voided")
                    .scalar()
                ) or 0.0

                allocated = (
                    s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                    .filter(AdvanceAllocation.entity_type == "purchase")
                    .filter(AdvanceAllocation.entity_id == purchase.id)
                    .scalar()
                ) or 0.0

                from database.models.purchase_price_adjustment import PurchasePriceAdjustment
                adjustment_total = (
                    s.query(func.coalesce(func.sum(PurchasePriceAdjustment.amount), 0))
                    .filter(PurchasePriceAdjustment.purchase_id == purchase.id)
                    .scalar()
                ) or 0.0

                # A price adjustment is a real, additional amount now
                # genuinely owed - without this, a purchase already fully
                # paid before an adjustment would be silently skipped
                # below (balance <= 0), even though it genuinely owes
                # again and an available advance should be tried against it.
                balance = float(purchase.total_amount or 0) + float(adjustment_total or 0) - float(paid or 0) - float(allocated or 0)

                if balance <= 0:
                    continue

                PaymentCenter.handle_purchase_creation(
                    party_id=party_id,
                    purchase_id=purchase.id,
                    purchase_total=balance,
                    created_by_user_id=created_by_user_id
                )

    @staticmethod
    def handle_customer_advance_settlement(party_id: int, created_by_user_id: int, skip_sale_id: int = 0):

        from database.models.sale import Sale
        from database.models.payment import Payment
        from database.models.advance_allocations import AdvanceAllocation
        from database.session import get_session
        from sqlalchemy import func

        with get_session() as s:

            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == party_id)
                .all()
            )

            for sale in sales:

                # Don't re-apply the freed advance to a sale that's being
                # returned - the release should leave the advance free.
                if skip_sale_id and sale.id == skip_sale_id:
                    continue

                paid = (
                    s.query(func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "sale")
                    .filter(Payment.entity_id == sale.id)
                    .filter(Payment.status != "voided")
                    .scalar()
                ) or 0.0

                allocated = (
                    s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                    .filter(AdvanceAllocation.entity_type == "sale")
                    .filter(AdvanceAllocation.entity_id == sale.id)
                    .scalar()
                ) or 0.0

                balance = float(sale.total_amount or 0) - float(paid or 0) - float(allocated or 0)
                if balance <= 0:
                    continue


                PaymentCenter.handle_sale_creation(
                    party_id=party_id,
                    sale_id=sale.id,
                    sale_total=balance,
                    created_by_user_id=created_by_user_id
                )