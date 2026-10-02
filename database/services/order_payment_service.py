# database/services/order_payment_service.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from database.session import get_session
from database.models.order_payment import OrderPayment


class OrderPaymentService:
    """
    Payments against a Sales Order (or, later, a Purchase Order) -
    entirely within OrderPayment, never the shared Payment table, never
    PartyBalanceService. See OrderPayment's own docstring for the full
    isolation and shared-table reasoning.

    Only ever meaningful once an order is confirmed and genuinely
    priced - see _get_order_final_total below.
    """

    ZERO_EPS = 0.005

    @staticmethod
    def _get_order_final_total(order_type: str, order_id: int):
        """
        Resolves an order's own final total, based on order_type. Only
        "sales_order" exists today; adding Purchase Order support later
        means adding one branch here, not building a second, parallel
        payment service. Returns None if the order isn't confirmed/
        priced yet, or doesn't exist.
        """
        if order_type == "sales_order":
            from database.models.sales_order import SalesOrder
            with get_session() as s:
                order = s.get(SalesOrder, order_id)
                if not order:
                    raise ValueError("Sales Order not found.")
                if order.status != "confirmed":
                    # Not an error - "not yet priced" is a legitimate,
                    # expected state for a draft/in-transit order. Raising
                    # here instead of returning None used to short-circuit
                    # get_balance() before it could ever report
                    # status="not_yet_priced" - which is exactly the signal
                    # record_payment()'s is_advance branch depends on to
                    # allow a pre-confirmation advance through. Raising
                    # made every draft-order advance payment fail with
                    # this same message, regardless of is_advance.
                    return None
                return float(order.final_total_amount) if order.final_total_amount is not None else None
        if order_type == "purchase_order":
            from database.models.purchase_order import PurchaseOrder
            with get_session() as s:
                order = s.get(PurchaseOrder, order_id)
                if not order:
                    raise ValueError("Purchase Order not found.")
                if order.status != "confirmed":
                    # See the identical note in the sales_order branch above.
                    return None
                return float(order.final_total_amount) if order.final_total_amount is not None else None
        raise ValueError(f"Unsupported order_type: {order_type!r}")

    @staticmethod
    def get_balance(order_type: str, order_id: int) -> Dict:
        order_id = int(order_id or 0)
        total = OrderPaymentService._get_order_final_total(order_type, order_id)

        with get_session() as s:
            paid = float(
                s.query(func.coalesce(func.sum(OrderPayment.amount), 0))
                .filter(OrderPayment.order_type == order_type)
                .filter(OrderPayment.order_id == order_id)
                .filter(OrderPayment.status != "voided")
                .scalar() or 0.0
            )

        if total is None:
            return {
                "order_type": order_type, "order_id": order_id, "total": None,
                "paid": paid, "balance": None, "status": "not_yet_priced",
            }

        balance = max(total - paid, 0.0)
        if total <= OrderPaymentService.ZERO_EPS:
            status = "paid"
        elif paid <= OrderPaymentService.ZERO_EPS:
            status = "unpaid"
        elif balance > OrderPaymentService.ZERO_EPS:
            status = "partial"
        else:
            status = "paid"

        return {
            "order_type": order_type, "order_id": order_id, "total": total,
            "paid": paid, "balance": balance, "status": status,
        }

    @staticmethod
    def record_payment(order_type: str, order_id: int, amount: float, created_by_user_id: int,
                       method: str = "", notes: str = "", is_advance: bool = False) -> int:
        """
        Records a payment against an order. Normally only meaningful once
        an order is confirmed and genuinely priced (see
        _get_order_final_total) - the balance check below caps the
        amount against what's actually still owed.

        is_advance=True is the one deliberate exception: money the
        supplier/company was actually handed at DRAFT creation time,
        before any final price exists to validate against. There's
        nothing to cap it against yet, so it's recorded as-is and simply
        counts toward `paid` the moment get_balance() has a real total
        to subtract against (get_balance already sums every non-voided
        OrderPayment row regardless of order status, so this reconciles
        itself automatically at confirmation - no separate merge step
        needed). Tagged in `notes` rather than a new column, so it's
        visible in the payment history without a schema change.
        """
        order_id = int(order_id or 0)
        amount = float(amount or 0.0)
        created_by_user_id = int(created_by_user_id or 0)

        if order_id <= 0:
            raise ValueError("order_id is required.")
        if amount <= 0:
            raise ValueError("amount must be greater than zero.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        bal = OrderPaymentService.get_balance(order_type, order_id)
        if bal["status"] == "not_yet_priced":
            if not is_advance:
                raise ValueError(
                    "This order isn't confirmed yet - there's no final price "
                    "to pay against until it is."
                )
            # No cap possible yet - recorded as entered, reconciled once confirmed.
        elif amount > bal["balance"] + OrderPaymentService.ZERO_EPS:
            raise ValueError(
                f"Amount (\u20a6{amount:,.2f}) exceeds the remaining "
                f"balance (\u20a6{bal['balance']:,.2f})."
            )

        notes = (notes or "").strip()
        if is_advance:
            tag = "Advance (paid at order creation)"
            notes = f"{tag} - {notes}" if notes else tag

        with get_session() as s:
            pay = OrderPayment(
                order_type=order_type,
                order_id=order_id,
                amount=amount,
                method=(method or "").strip() or None,
                notes=notes or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(pay)
            s.flush()
            return int(pay.id)

    @staticmethod
    def list_payments(order_type: str, order_id: int) -> List[Dict]:
        order_id = int(order_id or 0)
        with get_session() as s:
            from database.models.user import User
            rows = (
                s.query(OrderPayment, User.username)
                .join(User, User.id == OrderPayment.created_by_user_id)
                .filter(OrderPayment.order_type == order_type)
                .filter(OrderPayment.order_id == order_id)
                .order_by(OrderPayment.created_at.desc(), OrderPayment.id.desc())
                .all()
            )
            return [
                {
                    "id": int(p.id), "amount": float(p.amount or 0),
                    "method": p.method or "", "notes": p.notes or "",
                    "status": p.status, "created_at": p.created_at,
                    "created_by": username,
                }
                for p, username in rows
            ]

    @staticmethod
    def void_payment(payment_id: int, voided_by_user_id: int, reason: str = "") -> Dict:
        """
        Deliberately stricter than the main Payment table's void_payment:
        a reason is required here, not optional. Sales/Purchase Order
        payments are large, infrequent, high-value transactions - a void
        without an explained reason leaves a real gap in exactly the
        history that matters most for these.
        """
        payment_id = int(payment_id or 0)
        voided_by_user_id = int(voided_by_user_id or 0)
        reason = (reason or "").strip()
        if not reason:
            raise ValueError(
                "A reason is required to void this payment - this "
                "history is too important to leave unexplained."
            )

        with get_session() as s:
            pay = s.get(OrderPayment, payment_id)
            if not pay:
                raise ValueError("Payment not found.")
            if pay.status == "voided":
                return {"ok": True, "payment_id": payment_id, "already_voided": True}

            pay.status = "voided"
            pay.voided_at = datetime.now()
            pay.voided_by_user_id = voided_by_user_id
            pay.void_reason = reason

        return {"ok": True, "payment_id": payment_id}