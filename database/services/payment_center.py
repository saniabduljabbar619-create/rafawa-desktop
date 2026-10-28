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

    # -------------------------------------------------
    # APPLY AN ADVANCE TO CHOSEN DOCUMENTS (the user decides which, and how much)
    # -------------------------------------------------

    @staticmethod
    def open_documents(party_id: int, kind: str):
        """Purchases (kind="purchase") or sales (kind="sale") of this party that still owe money, oldest first.

        Each row: {"id", "ref", "date", "total", "balance"} - the balance is the ONE shared definition
        (document_balances), so returns, price adjustments and earlier settlements are already taken off.
        """
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        from database.services.document_balance import document_balances
        from database.session import get_session

        model, party_col, prefix = ((Purchase, Purchase.supplier_party_id, "PUR") if kind == "purchase"
                                    else (Sale, Sale.buyer_party_id, "SAL"))
        with get_session() as s:
            rows = (s.query(model.id, model.completed_at, model.created_at)
                    .filter(party_col == int(party_id))
                    .order_by(model.completed_at.asc(), model.id.asc()).all())
            ids = [int(r[0]) for r in rows]
            balances = document_balances(s, kind, ids)
        out = []
        for doc_id, done, created in rows:
            b = balances.get(int(doc_id))
            if b is None or b.voided or b.balance <= 0.005:
                continue
            out.append({"id": int(doc_id), "ref": f"{prefix}-{int(doc_id)}", "date": done or created,
                        "total": float(b.net_total), "balance": float(b.balance)})
        return out

    @staticmethod
    def available_advance(party_id: int, kind: str) -> float:
        """Advance money still unused: given to a supplier (purchases) / received from a customer (sales)."""
        direction = "given" if kind == "purchase" else "received"
        return round(sum(float(a.get("remaining") or 0)
                         for a in AdvanceService.get_fifo_advances(party_id, direction)), 2)

    @staticmethod
    def apply_advance(party_id: int, kind: str, allocations, created_by_user_id: int) -> float:
        """
        Apply open advance money to the documents the user chose: allocations = [(document_id, amount), ...].

        Nothing is applied unless the whole request is valid: every amount must be positive and no more
        than that document still owes, and the total no more than the advance still unused. Returns the
        total applied. A document left partly owing stays partly owing; unused advance stays as advance.
        """
        wanted = [(int(d), round(float(a), 2)) for d, a in allocations if float(a or 0) > 0.004]
        if not wanted:
            return 0.0
        owing = {d["id"]: d["balance"] for d in PaymentCenter.open_documents(party_id, kind)}
        for doc_id, amount in wanted:
            if doc_id not in owing:
                raise ValueError(f"Document {doc_id} owes nothing, or is not this party's.")
            if amount > owing[doc_id] + 0.005:
                raise ValueError(f"{amount:,.2f} is more than the {owing[doc_id]:,.2f} still owed on it.")
        total = round(sum(a for _d, a in wanted), 2)
        available = PaymentCenter.available_advance(party_id, kind)
        if total > available + 0.005:
            raise ValueError(f"Only {available:,.2f} of advance is left to use, not {total:,.2f}.")
        handler = PaymentCenter.handle_purchase_creation if kind == "purchase" else PaymentCenter.handle_sale_creation
        key = "purchase" if kind == "purchase" else "sale"
        used = 0.0
        for doc_id, amount in wanted:
            res = handler(party_id=party_id, **{f"{key}_id": doc_id, f"{key}_total": amount},
                          created_by_user_id=created_by_user_id)
            used += float(res.get("advance_used") or 0)
        return round(used, 2)

