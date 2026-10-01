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
        """
        Apply the party's open "given" advances to what they still owe on purchases, oldest first.

        What is still owed comes from the ONE shared definition (document_balances): returns,
        price adjustments and earlier cash/advance settlements are all accounted for exactly once,
        and voided purchases are skipped. (This used to subtract each earlier advance settlement
        twice - once as its allocation and once as its "advance" payment row - so a second advance
        was only partly applied and the rest stayed behind as Advance OUT.)
        """
        from database.models.purchase import Purchase
        from database.services.document_balance import document_balances
        from database.session import get_session

        with get_session() as s:
            rows = (
                s.query(Purchase.id)
                .filter(Purchase.supplier_party_id == party_id)
                .order_by(Purchase.completed_at.asc(), Purchase.id.asc())
                .all()
            )
            ids = [int(r[0]) for r in rows if not (skip_purchase_id and int(r[0]) == skip_purchase_id)]
            balances = document_balances(s, "purchase", ids)

        for pid in ids:
            b = balances.get(pid)
            if b is None or b.balance <= 0.005:
                continue
            PaymentCenter.handle_purchase_creation(
                party_id=party_id,
                purchase_id=pid,
                purchase_total=b.balance,
                created_by_user_id=created_by_user_id
            )

    @staticmethod
    def handle_customer_advance_settlement(party_id: int, created_by_user_id: int, skip_sale_id: int = 0):
        """Same as handle_supplier_advance_settlement, for a customer's deposits against their sales."""
        from database.models.sale import Sale
        from database.services.document_balance import document_balances
        from database.session import get_session

        with get_session() as s:
            rows = (
                s.query(Sale.id)
                .filter(Sale.buyer_party_id == party_id)
                .order_by(Sale.completed_at.asc(), Sale.id.asc())
                .all()
            )
            ids = [int(r[0]) for r in rows if not (skip_sale_id and int(r[0]) == skip_sale_id)]
            balances = document_balances(s, "sale", ids)

        for sid in ids:
            b = balances.get(sid)
            if b is None or b.balance <= 0.005:
                continue
            PaymentCenter.handle_sale_creation(
                party_id=party_id,
                sale_id=sid,
                sale_total=b.balance,
                created_by_user_id=created_by_user_id
            )
