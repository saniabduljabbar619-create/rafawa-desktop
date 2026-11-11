# database/services/closing_service.py
"""
Customer Closings Service: periodic balance calculation and closing snapshot recording.

Computes:
  - Opening Balance (from previous closing or 0.00)
  - Period Purchases (what we bought from customer, itemized)
  - Period Sales (what customer bought from us, itemized)
  - Period Advances (Advance OUT given, Advance IN received, itemized)
  - Period Payments (Payments IN received, Payments OUT paid, itemized)
  - Period Returns (Sale / Purchase returns)
  - Net Period Change = Debits (Sales + Adv OUT + Pay OUT) - Credits (Purchases + Adv IN + Pay IN + Returns)
  - Closing Balance = Opening Balance + Net Period Change

Stores immutable audit records in `customer_closings` with complete itemized JSON details.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import func, or_

from database.models.advance import Advance
from database.models.customer_closing import CustomerClosing
from database.models.party import Party
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.purchase_return import PurchaseReturn
from database.models.sale import Sale
from database.models.sale_return import SaleReturn
from database.models.user import User
from database.services.audit_service import AuditService
from database.services.document_balance import document_balances
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_role


def _f(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _closing_no(closing_id: int) -> str:
    return f"CLS-{int(closing_id):05d}"


def _format_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


class ClosingService:

    @staticmethod
    def get_last_closing(party_id: int, session=None) -> Optional[CustomerClosing]:
        """Returns the most recent active closing for the given party, or None."""
        if session is not None:
            return (
                session.query(CustomerClosing)
                .filter(CustomerClosing.party_id == int(party_id))
                .filter(CustomerClosing.status == "closed")
                .order_by(CustomerClosing.period_end.desc(), CustomerClosing.id.desc())
                .first()
            )
        with get_session() as s:
            return ClosingService.get_last_closing(party_id, session=s)

    @staticmethod
    def compute_closing(
        party_id: int,
        as_of: Optional[datetime] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Computes the full breakdown for a customer from their last closing up to `as_of` (default now).
        Returns a rich dictionary ready for display, audit, or recording.
        """
        if session is None:
            with get_session() as s:
                return ClosingService.compute_closing(party_id, as_of=as_of, session=s)

        party_id = int(party_id)
        period_end = as_of or datetime.now()

        party = session.get(Party, party_id)
        if not party:
            raise ValueError(f"Party #{party_id} not found.")

        last_closing = ClosingService.get_last_closing(party_id, session=session)
        if last_closing:
            period_start = last_closing.period_end
            opening_balance = _f(last_closing.closing_balance)
            prev_closing_no = last_closing.closing_no
        else:
            period_start = party.created_at or datetime(2020, 1, 1)
            opening_balance = 0.0
            prev_closing_no = None

        # -------------------------------------------------------------
        # 1. Purchases in period
        # -------------------------------------------------------------
        purchases_query = (
            session.query(Purchase)
            .filter(Purchase.supplier_party_id == party_id)
            .filter(Purchase.status.notin_(["draft", "voided"]))
            .filter(
                func.coalesce(Purchase.completed_at, Purchase.created_at) > period_start,
                func.coalesce(Purchase.completed_at, Purchase.created_at) <= period_end,
            )
            .order_by(func.coalesce(Purchase.completed_at, Purchase.created_at).asc())
            .all()
        )

        p_ids = [p.id for p in purchases_query]
        p_bals = document_balances(session, "purchase", p_ids) if p_ids else {}

        purchases_list: List[Dict[str, Any]] = []
        for p in purchases_query:
            p_bal = p_bals.get(p.id)
            summary_parts = []
            for item in p.items:
                prod_name = item.product.name if item.product else "?"
                qty = f"{item.qty_bags or 0} bags"
                summary_parts.append(f"{prod_name} ({qty})")
            summary = ", ".join(summary_parts) if summary_parts else "Intake"

            purchases_list.append({
                "id": p.id,
                "ref": p.purchase_no or f"PUR-{p.id}",
                "date": _format_dt(p.completed_at or p.created_at),
                "summary": summary,
                "total_amount": _f(p.total_amount),
                "settled": p_bal.settled if p_bal else 0.0,
                "balance": p_bal.balance if p_bal else 0.0,
                "status": p.status,
            })
        total_purchases = sum(p["total_amount"] for p in purchases_list)

        # -------------------------------------------------------------
        # 2. Sales in period
        # -------------------------------------------------------------
        sales_query = (
            session.query(Sale)
            .filter(Sale.buyer_party_id == party_id)
            .filter(Sale.status.notin_(["draft", "voided"]))
            .filter(
                func.coalesce(Sale.completed_at, Sale.created_at) > period_start,
                func.coalesce(Sale.completed_at, Sale.created_at) <= period_end,
            )
            .order_by(func.coalesce(Sale.completed_at, Sale.created_at).asc())
            .all()
        )

        s_ids = [s.id for s in sales_query]
        s_bals = document_balances(session, "sale", s_ids) if s_ids else {}

        sales_list: List[Dict[str, Any]] = []
        for sl in sales_query:
            s_bal = s_bals.get(sl.id)
            summary_parts = []
            for item in sl.items:
                prod_name = item.product.name if item.product else "?"
                qty = f"{item.qty_bags or 0} bags"
                summary_parts.append(f"{prod_name} ({qty})")
            summary = ", ".join(summary_parts) if summary_parts else "Sale"

            sales_list.append({
                "id": sl.id,
                "ref": sl.sale_no or f"SAL-{sl.id}",
                "date": _format_dt(sl.completed_at or sl.created_at),
                "summary": summary,
                "total_amount": _f(sl.total_amount),
                "settled": s_bal.settled if s_bal else 0.0,
                "balance": s_bal.balance if s_bal else 0.0,
                "status": sl.status,
            })
        total_sales = sum(s["total_amount"] for s in sales_list)

        # -------------------------------------------------------------
        # 3. Advances in period
        # -------------------------------------------------------------
        advances_query = (
            session.query(Advance)
            .filter(Advance.party_id == party_id)
            .filter(Advance.status != "voided")
            .filter(Advance.created_at > period_start, Advance.created_at <= period_end)
            .order_by(Advance.created_at.asc())
            .all()
        )

        adv_out_list: List[Dict[str, Any]] = []
        adv_in_list: List[Dict[str, Any]] = []

        for adv in advances_query:
            item_dict = {
                "id": adv.id,
                "ref": f"ADV-{adv.id:04d}",
                "direction": adv.direction,
                "type": adv.advance_type,
                "date": _format_dt(adv.created_at),
                "amount": _f(adv.amount),
                "notes": adv.notes or "",
            }
            if adv.direction == "given":
                adv_out_list.append(item_dict)
            else:
                adv_in_list.append(item_dict)

        total_advance_out = sum(a["amount"] for a in adv_out_list)
        total_advance_in = sum(a["amount"] for a in adv_in_list)

        # -------------------------------------------------------------
        # 4. Payments in period
        # -------------------------------------------------------------
        payments_query = (
            session.query(Payment)
            .filter(Payment.party_id == party_id)
            .filter(Payment.status != "voided")
            .filter(Payment.created_at > period_start, Payment.created_at <= period_end)
            .order_by(Payment.created_at.asc())
            .all()
        )

        pay_in_list: List[Dict[str, Any]] = []
        pay_out_list: List[Dict[str, Any]] = []

        for p in payments_query:
            item_dict = {
                "id": p.id,
                "ref": f"PAY-{p.id:04d}",
                "direction": p.direction,
                "entity_type": p.entity_type,
                "entity_id": p.entity_id,
                "amount": _f(p.amount),
                "method": p.method or "Cash",
                "date": _format_dt(p.created_at),
                "notes": p.notes or "",
            }
            if p.direction == "in":
                pay_in_list.append(item_dict)
            else:
                pay_out_list.append(item_dict)

        total_payments_in = sum(p["amount"] for p in pay_in_list)
        total_payments_out = sum(p["amount"] for p in pay_out_list)

        # -------------------------------------------------------------
        # 5. Returns in period
        # -------------------------------------------------------------
        sale_returns = (
            session.query(func.coalesce(func.sum(func.abs(SaleReturn.amount)), 0))
            .filter(SaleReturn.party_id == party_id)
            .filter(SaleReturn.created_at > period_start, SaleReturn.created_at <= period_end)
            .scalar() or 0.0
        )
        purchase_returns = (
            session.query(func.coalesce(func.sum(func.abs(PurchaseReturn.amount)), 0))
            .filter(PurchaseReturn.party_id == party_id)
            .filter(PurchaseReturn.created_at > period_start, PurchaseReturn.created_at <= period_end)
            .scalar() or 0.0
        )
        total_returns = _f(sale_returns) + _f(purchase_returns)

        # -------------------------------------------------------------
        # 6. Balances & Net Computation
        # -------------------------------------------------------------
        # Debits (Increases what customer owes us):
        #   Sales + Advance OUT + Payments OUT (cash we refunded or paid them)
        # Credits (Decreases what customer owes us / we owe them):
        #   Purchases + Advance IN + Payments IN (cash customer paid us) + Returns
        debits = total_sales + total_advance_out + total_payments_out
        credits = total_purchases + total_advance_in + total_payments_in + total_returns

        net_period_change = round(debits - credits, 2)
        closing_balance = round(opening_balance + net_period_change, 2)

        activity_count = (
            len(purchases_list)
            + len(sales_list)
            + len(adv_out_list)
            + len(adv_in_list)
            + len(pay_in_list)
            + len(pay_out_list)
        )

        party_label = party.display_name or f"Party #{party_id}"
        if party.walkin_code:
            party_label = f"{party_label} ({party.walkin_code})"

        return {
            "party_id": party_id,
            "party_name": party_label,
            "party_phone": party.phone or "",
            "period_start": period_start,
            "period_end": period_end,
            "prev_closing_no": prev_closing_no,
            "opening_balance": opening_balance,
            "total_purchases": round(total_purchases, 2),
            "total_sales": round(total_sales, 2),
            "total_advance_out": round(total_advance_out, 2),
            "total_advance_in": round(total_advance_in, 2),
            "total_payments_in": round(total_payments_in, 2),
            "total_payments_out": round(total_payments_out, 2),
            "total_returns": round(total_returns, 2),
            "debits": round(debits, 2),
            "credits": round(credits, 2),
            "net_period_change": net_period_change,
            "closing_balance": closing_balance,
            "activity_count": activity_count,
            "details": {
                "purchases": purchases_list,
                "sales": sales_list,
                "advance_out": adv_out_list,
                "advance_in": adv_in_list,
                "payments_in": pay_in_list,
                "payments_out": pay_out_list,
            },
        }

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def record_closing(
        party_id: int,
        user_id: int,
        notes: str = "",
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Computes and records an official customer closing snapshot.
        Generates closing_no, serializes detailed items, and logs audit trail.
        """
        with get_session() as s:
            calc = ClosingService.compute_closing(party_id, as_of=as_of, session=s)

            closing = CustomerClosing(
                closing_no="TEMP",
                party_id=int(party_id),
                party_name=calc["party_name"],
                party_phone=calc["party_phone"],
                period_start=calc["period_start"],
                period_end=calc["period_end"],
                opening_balance=calc["opening_balance"],
                total_purchases=calc["total_purchases"],
                total_sales=calc["total_sales"],
                total_advance_out=calc["total_advance_out"],
                total_advance_in=calc["total_advance_in"],
                total_payments_in=calc["total_payments_in"],
                total_payments_out=calc["total_payments_out"],
                total_returns=calc["total_returns"],
                net_period_change=calc["net_period_change"],
                closing_balance=calc["closing_balance"],
                status="closed",
                notes=notes.strip() if notes else None,
                details_json=json.dumps(calc["details"], default=str),
                created_by_user_id=int(user_id) if user_id else None,
                created_at=datetime.now(),
            )
            s.add(closing)
            s.flush()

            # Assign real closing_no based on generated auto-increment id
            closing.closing_no = _closing_no(closing.id)
            s.flush()

            result = {
                "id": closing.id,
                "closing_no": closing.closing_no,
                "party_id": closing.party_id,
                "party_name": closing.party_name,
                "party_phone": closing.party_phone,
                "period_start": _format_dt(closing.period_start),
                "period_end": _format_dt(closing.period_end),
                "opening_balance": _f(closing.opening_balance),
                "total_purchases": _f(closing.total_purchases),
                "total_sales": _f(closing.total_sales),
                "total_advance_out": _f(closing.total_advance_out),
                "total_advance_in": _f(closing.total_advance_in),
                "total_payments_in": _f(closing.total_payments_in),
                "total_payments_out": _f(closing.total_payments_out),
                "total_returns": _f(closing.total_returns),
                "net_period_change": _f(closing.net_period_change),
                "closing_balance": _f(closing.closing_balance),
                "status": closing.status,
                "notes": closing.notes or "",
                "created_at": _format_dt(closing.created_at),
                "details": calc["details"],
            }

            AuditService.record(
                "customer.closing",
                "customer_closing",
                closing.id,
                f"Closed {closing.closing_no} for {closing.party_name}: Opening ₦{closing.opening_balance:,.2f}, "
                f"Net ₦{closing.net_period_change:,.2f}, Closing Balance ₦{closing.closing_balance:,.2f}",
                details={"closing_no": closing.closing_no, "balance": _f(closing.closing_balance)},
                session=s,
            )
            s.commit()
            return result

    @staticmethod
    def get_closing_by_id(closing_id: int) -> Optional[Dict[str, Any]]:
        with get_session() as s:
            c = s.get(CustomerClosing, int(closing_id))
            if not c:
                return None
            details = {}
            if c.details_json:
                try:
                    details = json.loads(c.details_json)
                except Exception:
                    details = {}
            user = s.get(User, c.created_by_user_id) if c.created_by_user_id else None
            return {
                "id": c.id,
                "closing_no": c.closing_no,
                "party_id": c.party_id,
                "party_name": c.party_name,
                "party_phone": c.party_phone or "",
                "period_start": _format_dt(c.period_start),
                "period_end": _format_dt(c.period_end),
                "opening_balance": _f(c.opening_balance),
                "total_purchases": _f(c.total_purchases),
                "total_sales": _f(c.total_sales),
                "total_advance_out": _f(c.total_advance_out),
                "total_advance_in": _f(c.total_advance_in),
                "total_payments_in": _f(c.total_payments_in),
                "total_payments_out": _f(c.total_payments_out),
                "total_returns": _f(c.total_returns),
                "net_period_change": _f(c.net_period_change),
                "closing_balance": _f(c.closing_balance),
                "status": c.status,
                "notes": c.notes or "",
                "created_by": user.username if user else "System",
                "created_at": _format_dt(c.created_at),
                "details": details,
            }

    @staticmethod
    def list_closings(
        party_id: Optional[int] = None,
        limit: int = 100,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        search: str = "",
    ) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(CustomerClosing).order_by(CustomerClosing.period_end.desc(), CustomerClosing.id.desc())
            if party_id:
                q = q.filter(CustomerClosing.party_id == int(party_id))
            if date_from:
                q = q.filter(CustomerClosing.period_end >= date_from)
            if date_to:
                q = q.filter(CustomerClosing.period_end <= date_to)
            if search:
                term = f"%{search.strip().lower()}%"
                q = q.filter(
                    or_(
                        func.lower(CustomerClosing.closing_no).like(term),
                        func.lower(CustomerClosing.party_name).like(term),
                        func.lower(CustomerClosing.party_phone).like(term),
                    )
                )
            if limit:
                q = q.limit(limit)

            rows = q.all()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append({
                    "id": r.id,
                    "closing_no": r.closing_no,
                    "party_id": r.party_id,
                    "party_name": r.party_name,
                    "party_phone": r.party_phone or "",
                    "period_start": _format_dt(r.period_start),
                    "period_end": _format_dt(r.period_end),
                    "opening_balance": _f(r.opening_balance),
                    "total_purchases": _f(r.total_purchases),
                    "total_sales": _f(r.total_sales),
                    "total_advance_out": _f(r.total_advance_out),
                    "total_advance_in": _f(r.total_advance_in),
                    "total_payments_in": _f(r.total_payments_in),
                    "total_payments_out": _f(r.total_payments_out),
                    "total_returns": _f(r.total_returns),
                    "net_period_change": _f(r.net_period_change),
                    "closing_balance": _f(r.closing_balance),
                    "status": r.status,
                    "notes": r.notes or "",
                    "created_at": _format_dt(r.created_at),
                })
            return out

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def auto_close_active_customers_for_today(
        user_id: int, notes: str = "Evening auto-closing round-up"
    ) -> List[Dict[str, Any]]:
        """
        Finds all customers who have unclosed activity (activity since their last closing)
        and creates an official closing period for each. Useful for evening batch closing.
        """
        with get_session() as s:
            parties = (
                s.query(Party)
                .filter(Party.is_active == True)  # noqa: E712
                .order_by(Party.display_name.asc())
                .all()
            )
            party_ids = [p.id for p in parties]

        closed_list = []
        now = datetime.now()
        for pid in party_ids:
            try:
                calc = ClosingService.compute_closing(pid, as_of=now)
                # Only record if there was actual activity in the period or an outstanding balance
                if calc["activity_count"] > 0 or abs(calc["opening_balance"]) > 0.005:
                    rec = ClosingService.record_closing(pid, user_id=user_id, notes=notes, as_of=now)
                    closed_list.append(rec)
            except Exception:
                continue

        return closed_list
