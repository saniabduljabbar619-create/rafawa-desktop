# database/services/payment_service.py

from datetime import datetime, date
from typing import List, Dict, Optional

from sqlalchemy import func

from database.session import get_session
from database.models.payment import Payment


class PaymentService:

    # -------------------------------------------------
    # GENERIC PAYMENT
    # -------------------------------------------------
    @staticmethod
    def create_payment(
        entity_type: str,
        entity_id: int,
        party_id: int,
        direction: str,
        amount: float,
        created_by_user_id: int,
        method: str = "",
        notes: str = ""
    ) -> int:

        entity_type = (entity_type or "").strip().lower()

        if entity_type not in ("sale", "purchase", "advance"):
            raise ValueError("invalid entity_type")

        direction = (direction or "").strip().lower()

        if direction not in ("in", "out"):
            raise ValueError("direction must be 'in' or 'out'")

        if amount <= 0:
            raise ValueError("amount must be > 0")

        with get_session() as s:

            pay = Payment(
                entity_type=entity_type,
                entity_id=entity_id,
                party_id=party_id,
                direction=direction,
                amount=float(amount),
                method=str(method or ""),
                notes=str(notes or ""),
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )

            s.add(pay)
            s.flush()
            s.commit()

            return int(pay.id)

    # -------------------------------------------------
    # SALE PAYMENT
    # Customer pays for a sale
    # -------------------------------------------------
    @staticmethod
    def record_sale_payment(
        sale_id: int,
        party_id: int,
        amount: float,
        created_by_user_id: int,
        method: str = "",
        notes: str = ""
    ) -> int:

        if sale_id <= 0:
            raise ValueError("sale_id required")

        if amount <= 0:
            raise ValueError("amount must be > 0")

        with get_session() as s:

            pay = Payment(
                entity_type="sale",
                entity_id=sale_id,
                party_id=party_id,
                direction="in",
                amount=float(amount),
                method=str(method or ""),
                notes=str(notes or ""),
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )

            s.add(pay)
            s.flush()
            s.commit()

            return int(pay.id)

    # -------------------------------------------------
    # PURCHASE PAYMENT
    # Paying supplier
    # -------------------------------------------------
    @staticmethod
    def record_purchase_payment(
        purchase_id: int,
        party_id: int,
        amount: float,
        created_by_user_id: int,
        method: str = "",
        notes: str = ""
    ) -> int:

        if purchase_id <= 0:
            raise ValueError("purchase_id required")

        if amount <= 0:
            raise ValueError("amount must be > 0")

        with get_session() as s:

            pay = Payment(
                entity_type="purchase",
                entity_id=purchase_id,
                party_id=party_id,
                direction="out",
                amount=float(amount),
                method=str(method or ""),
                notes=str(notes or ""),
                created_by_user_id=created_by_user_id,
                created_at=datetime.now()
            )

            s.add(pay)
            s.flush()
            s.commit()

            return int(pay.id)

    # -------------------------------------------------
    # ADVANCE PAYMENT
    # Repaying or settling advance (🚀 UNIFIED INTERCEPTOR PROXY)
    # -------------------------------------------------
    @staticmethod
    def record_advance_payment(
        advance_id: int,
        party_id: int,
        direction: str,
        amount: float,
        created_by_user_id: int,
        method: str = "",
        notes: str = ""
    ) -> int:
        """
        Intercepts advance payouts and routes them through the range-bound same-day checker.
        If a same-day correction occurs, the primary Advance record is altered or wiped directly,
        preventing opposing clutter rows from being added to the general payments table.
        """
        if advance_id <= 0:
            raise ValueError("advance_id required")

        direction = str(direction or "").strip().lower()
        if direction not in ("in", "out"):
            raise ValueError("direction must be 'in' or 'out'")

        if amount <= 0:
            raise ValueError("amount must be > 0")

        # 🚀 Master Redirect: Route straight to the range-bound interceptor engine
        from database.services.advance_service import AdvanceService

        result = AdvanceService.settle_advance_payment(
            advance_id=int(advance_id),
            amount=float(amount),
            method=str(method or ""),
            created_by_user_id=int(created_by_user_id)
        )

        # Returns the fresh Payment record ID if a historical drawdown happens.
        # Returns 0 or the matching Advance ID if a same-day mutation/deletion occurred.
        return int(result) if isinstance(result, (int, float)) else 0

    # -------------------------------------------------
    # LIST PARTY PAYMENTS
    # -------------------------------------------------
    @staticmethod
    def list_party_payments(
        party_id: int,
        created_by_user_id: int,
        limit: int = 500,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        """
        NOTE: created_by_user_id is kept in the signature for backward
        compatibility with the caller, but is deliberately NOT used to
        filter results - this tab shows every payment for the party
        regardless of which cashier recorded it (the same principle
        already established for the Activity tab's own multi-cashier
        operator display). Filtering by the currently-viewing user meant
        another cashier's genuinely real payments against this same
        party were completely invisible here, not just mislabeled.
        """
        from database.models.user import User

        with get_session() as s:

            query = s.query(Payment).filter(Payment.party_id == party_id)
            if start_date:
                query = query.filter(func.date(Payment.created_at) >= start_date)
            if end_date:
                query = query.filter(func.date(Payment.created_at) <= end_date)

            rows = (
                query
                .order_by(Payment.created_at.desc())
                .limit(limit)
                .all()
            )
            out = [r.to_dict() for r in rows]

            # Price adjustments are real, genuine financial events too - a
            # supplier renegotiating after delivery genuinely changes what's
            # owed - but they live in their own table, not payments, and
            # were previously completely invisible here even though the
            # operator could see their EFFECT (a changed balance) elsewhere.
            # Merged in and shaped to match Payment.to_dict() exactly, so
            # the UI needs no special-casing to display them.
            from database.models.purchase_price_adjustment import PurchasePriceAdjustment
            from database.models.purchase import Purchase

            adj_query = (
                s.query(PurchasePriceAdjustment, Purchase.id)
                .join(Purchase, Purchase.id == PurchasePriceAdjustment.purchase_id)
                .filter(Purchase.supplier_party_id == party_id)
            )
            if start_date:
                adj_query = adj_query.filter(func.date(PurchasePriceAdjustment.created_at) >= start_date)
            if end_date:
                adj_query = adj_query.filter(func.date(PurchasePriceAdjustment.created_at) <= end_date)

            for adj, purchase_id in adj_query.all():
                out.append({
                    "id": adj.id,
                    "entity_type": "purchase",
                    "entity_id": purchase_id,
                    "party_id": party_id,
                    "direction": "out",
                    "amount": float(adj.amount or 0),
                    # Distinct, recognizable method label - never a real
                    # cash payment, so it must never be mistaken for one.
                    "method": "price_adjustment",
                    "notes": adj.reason or "",
                    "status": "completed",
                    "voided_at": None,
                    "voided_by_user_id": None,
                    "void_reason": "",
                    "created_by_user_id": adj.created_by_user_id,
                    "created_at": adj.created_at,
                })

            out.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
            out = out[:limit]

            # Resolve every row's own real operator, in one batch lookup -
            # never "System" for a genuine, attributable payment. Only
            # ever "System" if created_by_user_id is genuinely unset.
            user_ids = {r["created_by_user_id"] for r in out if r.get("created_by_user_id")}
            usernames = {}
            if user_ids:
                for u in s.query(User).filter(User.id.in_(user_ids)).all():
                    usernames[u.id] = u.username
            for r in out:
                r["operator"] = usernames.get(r.get("created_by_user_id"), "System")

        # Document-specific Balance Before / Balance After, bank-statement
        # style - one sale's or purchase's own balance around THIS payment,
        # never the party's overall total. Price adjustments aren't a cash
        # Payment row so they're excluded here (they already surface as
        # their own line via the Activity tab's document balance instead).
        from database.services.party_ledger_service import PartyLedgerService
        doc_balances = PartyLedgerService.payment_document_balances(party_id)
        for r in out:
            bal = doc_balances.get(r.get("id")) if r.get("method") != "price_adjustment" else None
            r["balance_before"] = bal["before"] if bal else None
            r["balance_after"] = bal["after"] if bal else None

        return out

    @staticmethod
    def list_walkin_payments(walkin_name):

        from database.session import get_session
        from sqlalchemy import text

        with get_session() as s:

            rows = s.execute(text("""
                SELECT
                    p.created_at,
                    p.entity_type,
                    p.entity_id,
                    p.direction,
                    p.amount,
                    p.method,
                    p.notes
                FROM payments p
                JOIN sales s
                    ON p.entity_type = 'sale'
                    AND p.entity_id = s.id
                WHERE s.buyer_party_id = 11
                AND s.walkin_name = :name

                UNION ALL

                SELECT
                    p.created_at,
                    p.entity_type,
                    p.entity_id,
                    p.direction,
                    p.amount,
                    p.method,
                    p.notes
                FROM payments p
                JOIN purchases pu
                    ON p.entity_type = 'purchase'
                    AND p.entity_id = pu.id
                WHERE pu.supplier_party_id = 11
                AND pu.walkin_name = :name

                ORDER BY created_at DESC
            """), {"name": walkin_name}).fetchall()

        return [dict(r._mapping) for r in rows]

    # -------------------------------------------------
    # LIST VOIDABLE PAYMENTS ("Revert Mistakes" tab picker)
    # -------------------------------------------------
    @staticmethod
    def list_voidable_payments(
        party_id: int,
        entity_type: Optional[str] = None,
        limit: int = 300,
    ) -> List[Dict]:
        """
        Every currently-active (non-voided) payment for this party, newest
        first - optionally narrowed to one entity_type ("sale", "purchase",
        "advance"). This is the picker list for reverting a mistaken
        payment; an already-voided payment has nothing left to revert, so
        it's excluded rather than shown disabled.
        """
        entity_type = (entity_type or "").strip().lower() or None
        if entity_type and entity_type not in ("sale", "purchase", "advance"):
            raise ValueError("invalid entity_type")

        with get_session() as s:
            query = s.query(Payment).filter(
                Payment.party_id == int(party_id),
                Payment.status != "voided",
            )
            if entity_type:
                query = query.filter(Payment.entity_type == entity_type)

            rows = query.order_by(Payment.created_at.desc()).limit(limit).all()

        prefixes = {"sale": "SAL", "purchase": "PUR", "advance": "ADV"}
        out = []
        for p in rows:
            d = p.to_dict()
            d["reference"] = f"{prefixes.get(p.entity_type, str(p.entity_type).upper())}-{p.entity_id}"
            out.append(d)
        return out

    # -------------------------------------------------
    # VOID PAYMENT (mistake correction, keeps history)
    # -------------------------------------------------
    @staticmethod
    def void_payment(payment_id: int, voided_by_user_id: int, reason: str = "") -> None:
        """
        Voids a payment - it stays in the table forever as the audit trail,
        but every balance calculation across the app excludes it from this
        point on. Never edits the amount/direction/entity in place: to fix
        a wrong payment, void it, then record a fresh correct one - the
        same append-only convention already used for every other money
        record in this app (e.g. AdvanceService.void_advance).

        Refuses if this payment already produced a cross-cashier
        settlement that has been marked "settled" - that means cash has
        physically already changed hands between two cashiers' drawers,
        and voiding the payment at that point cannot undo money that
        genuinely already moved. A still-OPEN settlement (nothing
        physically exchanged yet) is safe to void along with the payment.
        """
        from database.models.cashier_settlement import CashierSettlement

        payment_id = int(payment_id or 0)
        voided_by_user_id = int(voided_by_user_id or 0)
        if payment_id <= 0:
            raise ValueError("payment_id is required.")
        if voided_by_user_id <= 0:
            raise ValueError("voided_by_user_id is required.")

        with get_session() as s:
            payment = s.get(Payment, payment_id)
            if not payment:
                raise ValueError("Payment not found.")
            if payment.status == "voided":
                return  # already voided - idempotent, same convention as void_advance

            settlement = (
                s.query(CashierSettlement)
                .filter(CashierSettlement.source_payment_id == payment_id)
                .first()
            )
            if settlement and settlement.status == "settled":
                raise ValueError(
                    "Cannot revert this payment - it already triggered a "
                    "cross-cashier settlement that has been marked settled, "
                    "meaning cash has physically already changed hands "
                    "between cashiers. Reconcile that settlement first."
                )

            payment.status = "voided"
            payment.voided_at = datetime.now()
            payment.voided_by_user_id = voided_by_user_id
            payment.void_reason = (reason or "").strip()[:255] or None

            if settlement:
                s.delete(settlement)

            s.commit()

    # -------------------------------------------------
    # DOCUMENT PAYMENTS
    # -------------------------------------------------
    @staticmethod
    def get_document_payments(
        entity_type: str,
        entity_id: int
    ) -> List[Dict]:

        entity_type = (entity_type or "").strip().lower()

        if entity_type not in ("sale", "purchase", "advance"):
            raise ValueError("invalid entity_type")

        if entity_id <= 0:
            raise ValueError("entity_id must be > 0")

        with get_session() as s:

            rows = (
                s.query(Payment)
                .filter(
                    Payment.entity_type == entity_type,
                    Payment.entity_id == entity_id
                )
                .order_by(Payment.created_at.asc())
                .all()
            )

        return [r.to_dict() for r in rows]