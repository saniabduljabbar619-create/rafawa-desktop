# database/services/order_money_service.py
"""
Orders: money given / received (outside the order price), per-order expenses, repayments, and the partner statement.

Rules in one place:
  * Two sheets, kept apart from the order's own total and from the cash drawer.
  * Money given/received is a record on the partner's account (or one order). It becomes real order payment only when
    someone applies it to an order (sales: money they gave us; purchase: money we gave them).
  * An expense is either charged to the partner (recoverable: a receivable, never part of the order value) or our own
    cost. Repayments are recorded against recoverable expenses, oldest first or one expense at a time.
  * Nothing is deleted - rows are voided with a reason.
  * Admin and cashier may add and apply; only an admin may void.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from database.models.order_money import OrderExpense, OrderExpenseRecovery, OrderMoney
from database.models.purchase_order import PurchaseOrder
from database.models.sales_order import SalesOrder
from database.models.user import User
from database.services.audit_service import AuditService
from database.services.order_payment_service import OrderPaymentService
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_admin, require_role
from shared.auth.session import require_user_id

KINDS = ("sales", "purchase")
EXPENSE_CATEGORIES = ["Driver", "Fuel", "Food", "Loading / offloading", "Other"]
EPS = 0.005


def _f(v) -> float:
    return float(v or 0)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("kind must be 'sales' or 'purchase'.")
    return kind


def _pay_type(kind: str) -> str:
    return "sales_order" if kind == "sales" else "purchase_order"


def _order(s, kind: str, order_id: int):
    model = SalesOrder if kind == "sales" else PurchaseOrder
    o = s.get(model, int(order_id))
    if o is None:
        raise ValueError("Order not found.")
    partner = o.buyer_company_id if kind == "sales" else o.supplier_id
    return o, int(partner)


def _day(d) -> date:
    if d is None or d == "":
        return date.today()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _users(s) -> Dict[int, str]:
    return {int(u.id): u.username for u in s.query(User).all()}


class OrderMoneyService:

    # ================================================================== money given / received
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def add_money(kind: str, partner_id: int, direction: str, amount: float, order_id: Optional[int] = None,
                  method: str = "", note: str = "", entry_date=None) -> int:
        _kind(kind)
        direction = (direction or "").strip().lower()
        if direction not in ("in", "out"):
            raise ValueError("Say whether they gave us the money or we gave it to them.")
        amount = round(_f(amount), 2)
        if amount <= 0:
            raise ValueError("The amount must be more than zero.")
        with get_session() as s:
            if order_id:
                _o, owner = _order(s, kind, order_id)
                if owner != int(partner_id):
                    raise ValueError("That order belongs to someone else.")
            row = OrderMoney(kind=kind, partner_id=int(partner_id), order_id=int(order_id) if order_id else None,
                             direction=direction, amount=amount, method=(method or "").strip().lower() or None,
                             note=(note or "").strip() or None, entry_date=_day(entry_date),
                             created_by_user_id=int(require_user_id()))
            s.add(row)
            s.flush()
            AuditService.record("order_money.add", "order_money", int(row.id),
                                f"{'Received from' if direction == 'in' else 'Paid to'} partner #{partner_id}: "
                                f"{amount:,.2f}", session=s)
            s.commit()
            return int(row.id)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_money(kind: str, partner_id: Optional[int] = None, order_id: Optional[int] = None) -> List[Dict[str, Any]]:
        _kind(kind)
        with get_session() as s:
            q = s.query(OrderMoney).filter(OrderMoney.kind == kind)
            if partner_id is not None:
                q = q.filter(OrderMoney.partner_id == int(partner_id))
            if order_id is not None:
                q = q.filter(OrderMoney.order_id == int(order_id))
            rows = q.order_by(OrderMoney.entry_date.asc(), OrderMoney.id.asc()).all()
            users = _users(s)
            model = SalesOrder if kind == "sales" else PurchaseOrder
            nos = {int(i): n for i, n in s.query(model.id, model.order_no)
                   .filter(model.id.in_([r.order_id for r in rows if r.order_id] or [0])).all()}
            return [{"id": int(r.id), "date": r.entry_date, "direction": r.direction, "amount": _f(r.amount),
                     "method": r.method or "", "note": r.note or "", "status": r.status,
                     "order_id": int(r.order_id) if r.order_id else None, "order_no": nos.get(int(r.order_id or 0), ""),
                     "by": users.get(int(r.created_by_user_id), ""), "void_reason": r.void_reason or "",
                     "applied_payment_id": r.applied_payment_id} for r in rows]

    @staticmethod
    @require_admin
    def void_money(money_id: int, reason: str = "") -> None:
        with get_session() as s:
            r = s.get(OrderMoney, int(money_id))
            if r is None:
                raise ValueError("Entry not found.")
            if r.status == "voided":
                return
            if r.status == "applied":
                raise ValueError("This was applied to an order payment - void that payment on the order first.")
            r.status, r.voided_at = "voided", datetime.now()
            r.voided_by_user_id, r.void_reason = int(require_user_id()), (reason or "").strip() or None
            AuditService.record("order_money.void", "order_money", int(r.id), f"Voided {_f(r.amount):,.2f}"
                                + (f": {reason}" if reason else ""), session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def apply_to_order(money_id: int, order_id: int) -> int:
        """Turn a money entry into a real payment on an order. Sales: money they gave us. Purchase: money we gave them."""
        with get_session() as s:
            r = s.get(OrderMoney, int(money_id))
            if r is None:
                raise ValueError("Entry not found.")
            if r.status != "completed":
                raise ValueError("Only an entry that is still open can be applied.")
            want = "in" if r.kind == "sales" else "out"
            if r.direction != want:
                raise ValueError("Only money they gave us can be applied to a Sales Order." if r.kind == "sales"
                                 else "Only money we gave them can be applied to a Purchase Order.")
            _o, owner = _order(s, r.kind, order_id)
            if owner != int(r.partner_id):
                raise ValueError("That order belongs to someone else.")
            amount, kind, partner = _f(r.amount), r.kind, int(r.partner_id)
        pid = OrderPaymentService.record_payment(
            _pay_type(kind), int(order_id), amount, int(require_user_id()), method=r.method or "",
            notes=f"From the money sheet (#{int(money_id)})", is_advance=True)
        with get_session() as s:
            r = s.get(OrderMoney, int(money_id))
            r.status, r.applied_payment_id, r.order_id = "applied", int(pid), int(order_id)
            AuditService.record("order_money.apply", "order_money", int(money_id),
                                f"Applied {amount:,.2f} to order #{order_id}", session=s)
            s.commit()
        return int(pid)

    # ================================================================== expenses
    @staticmethod
    def _recovered_map(s, expense_ids) -> Dict[int, float]:
        if not expense_ids:
            return {}
        return {int(i): _f(t) for i, t in
                s.query(OrderExpenseRecovery.expense_id, func.coalesce(func.sum(OrderExpenseRecovery.amount), 0))
                .filter(OrderExpenseRecovery.expense_id.in_(expense_ids))
                .filter(OrderExpenseRecovery.status != "voided").group_by(OrderExpenseRecovery.expense_id).all()}

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def add_expense(kind: str, order_id: int, category: str, amount: float, description: str = "",
                    recoverable: Optional[bool] = None, paid_to: str = "", method: str = "", expense_date=None) -> int:
        _kind(kind)
        category = (category or "").strip()
        if not category:
            raise ValueError("Say what the expense was for (driver, fuel, food ...).")
        amount = round(_f(amount), 2)
        if amount <= 0:
            raise ValueError("The amount must be more than zero.")
        if recoverable is None:
            recoverable = (kind == "purchase")              # the supplier asked us to pay: they will pay us back
        with get_session() as s:
            o, partner = _order(s, kind, order_id)
            if o.status == "cancelled":
                raise ValueError("This order was cancelled.")
            row = OrderExpense(kind=kind, order_id=int(order_id), partner_id=partner, category=category,
                               description=(description or "").strip() or None, amount=amount,
                               paid_to=(paid_to or "").strip() or None, method=(method or "").strip().lower() or None,
                               recoverable=bool(recoverable), expense_date=_day(expense_date),
                               created_by_user_id=int(require_user_id()))
            s.add(row)
            s.flush()
            row.expense_no = f"OEX-{int(row.id):05d}"
            AuditService.record("order_expense.add", "order_expense", int(row.id),
                                f"{row.expense_no}: {category} {amount:,.2f} on {o.order_no}"
                                + (" (charged to partner)" if recoverable else " (our cost)"), session=s)
            s.commit()
            return int(row.id)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_expenses(kind: str, order_id: Optional[int] = None,
                      partner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        _kind(kind)
        with get_session() as s:
            q = s.query(OrderExpense).filter(OrderExpense.kind == kind)
            if order_id is not None:
                q = q.filter(OrderExpense.order_id == int(order_id))
            if partner_id is not None:
                q = q.filter(OrderExpense.partner_id == int(partner_id))
            rows = q.order_by(OrderExpense.expense_date.asc(), OrderExpense.id.asc()).all()
            rec = OrderMoneyService._recovered_map(s, [int(r.id) for r in rows])
            users = _users(s)
            model = SalesOrder if kind == "sales" else PurchaseOrder
            nos = {int(i): n for i, n in s.query(model.id, model.order_no)
                   .filter(model.id.in_([r.order_id for r in rows] or [0])).all()}
            out = []
            for r in rows:
                voided = r.status == "voided"
                got = rec.get(int(r.id), 0.0)
                left = 0.0 if (voided or not r.recoverable) else max(_f(r.amount) - got, 0.0)
                if voided:
                    state = "voided"
                elif not r.recoverable:
                    state = "our cost"
                elif left <= EPS:
                    state = "recovered"
                elif got > EPS:
                    state = "part recovered"
                else:
                    state = "unrecovered"
                out.append({"id": int(r.id), "no": r.expense_no or "", "date": r.expense_date, "category": r.category,
                            "description": r.description or "", "amount": _f(r.amount), "paid_to": r.paid_to or "",
                            "method": r.method or "", "recoverable": bool(r.recoverable), "status": r.status,
                            "order_id": int(r.order_id), "order_no": nos.get(int(r.order_id), ""),
                            "recovered": 0.0 if voided else got, "left": left, "state": state,
                            "by": users.get(int(r.created_by_user_id), ""), "void_reason": r.void_reason or ""})
            return out

    @staticmethod
    @require_admin
    def void_expense(expense_id: int, reason: str = "") -> None:
        with get_session() as s:
            r = s.get(OrderExpense, int(expense_id))
            if r is None:
                raise ValueError("Expense not found.")
            if r.status == "voided":
                return
            if OrderMoneyService._recovered_map(s, [int(r.id)]).get(int(r.id), 0.0) > EPS:
                raise ValueError("Part of this was paid back - void those repayments first.")
            r.status, r.voided_at = "voided", datetime.now()
            r.voided_by_user_id, r.void_reason = int(require_user_id()), (reason or "").strip() or None
            AuditService.record("order_expense.void", "order_expense", int(r.id),
                                f"Voided {r.expense_no} {_f(r.amount):,.2f}" + (f": {reason}" if reason else ""),
                                session=s)
            s.commit()

    # ------------------------------------------------------------------ repayments
    @staticmethod
    def _recover(s, expense_ids: List[int], amount: float, method: str, note: str, day) -> List[int]:
        """Spread a repayment over the given expenses, in the order given, never more than each one still owes."""
        left_total = round(_f(amount), 2)
        ids = []
        for eid in expense_ids:
            if left_total <= EPS:
                break
            e = s.get(OrderExpense, int(eid))
            got = OrderMoneyService._recovered_map(s, [int(eid)]).get(int(eid), 0.0)
            owed = max(_f(e.amount) - got, 0.0)
            if e.status == "voided" or not e.recoverable or owed <= EPS:
                continue
            part = round(min(owed, left_total), 2)
            rec = OrderExpenseRecovery(expense_id=int(eid), amount=part, method=(method or "").strip().lower() or None,
                                       note=(note or "").strip() or None, entry_date=_day(day),
                                       created_by_user_id=int(require_user_id()))
            s.add(rec)
            s.flush()
            ids.append(int(rec.id))
            left_total = round(left_total - part, 2)
            AuditService.record("order_expense.recovery", "order_expense", int(eid),
                                f"{e.expense_no}: {part:,.2f} paid back", session=s)
        if left_total > EPS:
            raise ValueError(f"That is {left_total:,.2f} more than is still owed back.")
        return ids

    @staticmethod
    def _open_ids(s, kind: str, order_id=None, partner_id=None) -> List[int]:
        q = s.query(OrderExpense).filter(OrderExpense.kind == kind, OrderExpense.status == "active",
                                         OrderExpense.recoverable.is_(True))
        if order_id is not None:
            q = q.filter(OrderExpense.order_id == int(order_id))
        if partner_id is not None:
            q = q.filter(OrderExpense.partner_id == int(partner_id))
        return [int(r.id) for r in q.order_by(OrderExpense.expense_date.asc(), OrderExpense.id.asc()).all()]

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def record_recovery(amount: float, expense_id: Optional[int] = None, kind: str = "", order_id: Optional[int] = None,
                        partner_id: Optional[int] = None, method: str = "", note: str = "", entry_date=None) -> List[int]:
        """The partner paid some of it back. For one expense, or spread (oldest first) over an order / a partner."""
        amount = round(_f(amount), 2)
        if amount <= 0:
            raise ValueError("The amount must be more than zero.")
        with get_session() as s:
            if expense_id is not None:
                ids = [int(expense_id)]
            else:
                _kind(kind)
                if order_id is None and partner_id is None:
                    raise ValueError("Say which order or partner this repayment is for.")
                ids = OrderMoneyService._open_ids(s, kind, order_id, partner_id)
            out = OrderMoneyService._recover(s, ids, amount, method, note, entry_date)
            s.commit()
            return out

    @staticmethod
    @require_admin
    def void_recovery(recovery_id: int, reason: str = "") -> None:
        with get_session() as s:
            r = s.get(OrderExpenseRecovery, int(recovery_id))
            if r is None:
                raise ValueError("Repayment not found.")
            if r.status == "voided":
                return
            r.status, r.voided_at = "voided", datetime.now()
            r.voided_by_user_id, r.void_reason = int(require_user_id()), (reason or "").strip() or None
            AuditService.record("order_expense.recovery_void", "order_expense", int(r.expense_id),
                                f"Repayment {_f(r.amount):,.2f} voided" + (f": {reason}" if reason else ""), session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_recoveries(kind: str, order_id: Optional[int] = None,
                        partner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        _kind(kind)
        with get_session() as s:
            q = (s.query(OrderExpenseRecovery, OrderExpense)
                 .join(OrderExpense, OrderExpense.id == OrderExpenseRecovery.expense_id)
                 .filter(OrderExpense.kind == kind))
            if order_id is not None:
                q = q.filter(OrderExpense.order_id == int(order_id))
            if partner_id is not None:
                q = q.filter(OrderExpense.partner_id == int(partner_id))
            users = _users(s)
            return [{"id": int(r.id), "expense_no": e.expense_no or "", "category": e.category, "date": r.entry_date,
                     "amount": _f(r.amount), "method": r.method or "", "note": r.note or "", "status": r.status,
                     "by": users.get(int(r.created_by_user_id), "")}
                    for r, e in q.order_by(OrderExpenseRecovery.entry_date.asc(), OrderExpenseRecovery.id.asc()).all()]

    # ================================================================== the partner statement
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def statement(kind: str, partner_id: int) -> Dict[str, Any]:
        """
        Where we stand with one company / supplier. Positive "net" means THEY owe US; negative means WE owe THEM.
        Nothing is netted behind anyone's back - these are the four figures side by side, plus their sum.
        """
        from database.services.order_sheet_service import OrderSheetService
        _kind(kind)
        orders = OrderSheetService.orders_of(kind, int(partner_id))
        owed = round(sum(_f(o["balance"]) for o in orders if o["balance"] is not None and o["status"] != "cancelled"), 2)
        orders_signed = owed if kind == "sales" else -owed          # sales: they owe us; purchase: we owe them
        money = [m for m in OrderMoneyService.list_money(kind, partner_id=partner_id) if m["status"] == "completed"]
        m_in = round(sum(m["amount"] for m in money if m["direction"] == "in"), 2)
        m_out = round(sum(m["amount"] for m in money if m["direction"] == "out"), 2)
        exps = [e for e in OrderMoneyService.list_expenses(kind, partner_id=partner_id) if e["status"] == "active"]
        to_recover = round(sum(e["left"] for e in exps), 2)
        our_cost = round(sum(e["amount"] for e in exps if not e["recoverable"]), 2)
        net = round(orders_signed - m_in + m_out + to_recover, 2)
        return {"orders": orders_signed, "orders_owed": owed, "money_in": m_in, "money_out": m_out,
                "money_net": round(m_in - m_out, 2), "to_recover": to_recover, "our_cost": our_cost, "net": net}
