# database/services/expense_service.py
"""
The two outflow books, kept apart like alg-erp keeps them:

    Expenses       day-to-day running costs (rent, wages, fuel, electricity ...)
    Expenditures   larger outlays (equipment, vehicles, building work, a bulk stock trip ...)

Same behaviour, separate tables and numbering (EXP-/EXD-). Admin only; every change is audited.
A row is never deleted: a wrong one is voided with a reason (and can be brought back), edits are
written to the audit log with before/after.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from database.models.expenditure import Expenditure
from database.models.expense import Expense
from database.services.audit_service import AuditService
from database.session import get_session
from shared.auth.permissions import require_admin
from shared.auth.session import require_user_id

DEFAULT_CATEGORIES = [
    "Rent", "Salaries & Wages", "Transport & Fuel", "Electricity & Water", "Repairs & Maintenance",
    "Loading & Offloading", "Packaging", "Communication", "Taxes & Levies", "Office & Supplies",
    "Meals & Welfare", "Miscellaneous",
]
EXPENDITURE_CATEGORIES = [
    "Equipment & Machinery", "Vehicles", "Building & Renovation", "Land & Property", "Furniture & Fittings",
    "Computers & Software", "Bulk Stock Trip", "Other Major Outlay",
]
METHODS = ["cash", "transfer", "pos", "cheque"]


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _no(expense_id: int, prefix: str = "EXP") -> str:
    return f"{prefix}-{int(expense_id):05d}"


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _row(e, prefix: str = "EXP") -> Dict[str, Any]:
    return {
        "id": int(e.id), "expense_no": e.expense_no or _no(e.id, prefix), "date": e.expense_date,
        "category": e.category, "description": e.description, "paid_to": e.paid_to or "",
        "amount": _f(e.amount), "method": e.method or "", "reference": e.reference or "",
        "notes": e.notes or "", "status": e.status, "void_reason": e.void_reason or "",
        "created_at": e.created_at,
    }


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    amount = _f(data.get("amount"))
    if amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    category = str(data.get("category") or "").strip() or "General"
    description = str(data.get("description") or "").strip()
    if not description:
        raise ValueError("Describe what the money was spent on.")
    when = _as_date(data.get("date") or date.today())
    if when > date.today() + timedelta(days=1):
        raise ValueError("An expense cannot be dated in the future.")
    method = str(data.get("method") or "cash").strip().lower()
    return {
        "expense_date": when, "category": category[:60], "description": description[:255],
        "paid_to": (str(data.get("paid_to") or "").strip()[:120] or None), "amount": round(amount, 2),
        "method": method[:30], "reference": (str(data.get("reference") or "").strip()[:80] or None),
        "notes": (str(data.get("notes") or "").strip() or None),
    }


class _OutflowService:
    MODEL = Expense
    PREFIX = "EXP"
    AUDIT = "expense"
    NOUN = "Expense"
    DEFAULTS = DEFAULT_CATEGORIES


    @classmethod
    @require_admin
    def add(cls, data: Dict[str, Any]) -> int:
        vals = _clean(data)
        with get_session() as s:
            e = cls.MODEL(created_by_user_id=int(require_user_id()), **vals)
            s.add(e)
            s.flush()
            e.expense_no = _no(e.id, cls.PREFIX)
            AuditService.record(f"{cls.AUDIT}.add", cls.AUDIT, e.id,
                                f"{e.expense_no}: {e.category} {vals['amount']:,.2f} - {e.description}", session=s)
            s.commit()
            return int(e.id)

    @classmethod
    @require_admin
    def update(cls, expense_id: int, data: Dict[str, Any]) -> None:
        vals = _clean(data)
        with get_session() as s:
            e = s.query(cls.MODEL).filter(cls.MODEL.id == int(expense_id)).with_for_update().first()
            if e is None:
                raise ValueError(f"{cls.NOUN} not found.")
            if e.status == "voided":
                raise ValueError(f"A voided {cls.NOUN.lower()} cannot be edited.")
            before = {k: str(getattr(e, k)) for k in vals if str(getattr(e, k)) != str(vals[k])}
            for k, v in vals.items():
                setattr(e, k, v)
            if before:
                AuditService.record(f"{cls.AUDIT}.edit", cls.AUDIT, e.id, f"{e.expense_no} edited",
                                    {"before": before, "after": {k: str(vals[k]) for k in before}}, session=s)
            s.commit()

    @classmethod
    @require_admin
    def void(cls, expense_id: int, reason: str) -> None:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("Give a reason for voiding this expense.")
        with get_session() as s:
            e = s.query(cls.MODEL).filter(cls.MODEL.id == int(expense_id)).with_for_update().first()
            if e is None:
                raise ValueError(f"{cls.NOUN} not found.")
            if e.status == "voided":
                return
            e.status = "voided"
            e.voided_at = datetime.now()
            e.voided_by_user_id = int(require_user_id())
            e.void_reason = reason[:255]
            AuditService.record(f"{cls.AUDIT}.void", cls.AUDIT, e.id,
                                f"{e.expense_no} voided ({_f(e.amount):,.2f}): {reason}", session=s)
            s.commit()

    @classmethod
    @require_admin
    def restore(cls, expense_id: int) -> None:
        """Bring a voided row back."""
        with get_session() as s:
            e = s.query(cls.MODEL).filter(cls.MODEL.id == int(expense_id)).with_for_update().first()
            if e is None:
                raise ValueError(f"{cls.NOUN} not found.")
            if e.status != "voided":
                return
            e.status = "active"
            e.voided_at = e.voided_by_user_id = e.void_reason = None
            AuditService.record(f"{cls.AUDIT}.restore", cls.AUDIT, e.id, f"{e.expense_no} brought back", session=s)
            s.commit()


    @classmethod
    @require_admin
    def list_expenses(cls, start: Optional[date] = None, end: Optional[date] = None, category: str = "",
             search: str = "", include_voided: bool = False, limit: int = 1000) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(cls.MODEL)
            if not include_voided:
                q = q.filter(cls.MODEL.status == "active")
            if start:
                q = q.filter(cls.MODEL.expense_date >= start)
            if end:
                q = q.filter(cls.MODEL.expense_date <= end)
            if category:
                q = q.filter(cls.MODEL.category == category)
            term = (search or "").strip()
            if term:
                like = f"%{term}%"
                q = q.filter((cls.MODEL.description.like(like)) | (cls.MODEL.paid_to.like(like))
                             | (cls.MODEL.reference.like(like)) | (cls.MODEL.expense_no.like(like)))
            rows = q.order_by(cls.MODEL.expense_date.desc(), cls.MODEL.id.desc()).limit(int(limit)).all()
            return [_row(e, cls.PREFIX) for e in rows]

    @classmethod
    @require_admin
    def categories(cls, ) -> List[str]:
        with get_session() as s:
            used = [r[0] for r in s.query(cls.MODEL.category).distinct().all() if r[0]]
        return cls.DEFAULTS + sorted(c for c in used if c not in cls.DEFAULTS)

    @classmethod
    @require_admin
    def summary(cls, start: Optional[date] = None, end: Optional[date] = None, category: str = "",
                search: str = "") -> Dict[str, Any]:
        """Totals for the chosen filter, a per-category split and the last six months' trend."""
        rows = cls.list_expenses(start, end, category, search, limit=100000)
        total = sum(r["amount"] for r in rows)
        by_cat: Dict[str, float] = {}
        for r in rows:
            by_cat[r["category"]] = by_cat.get(r["category"], 0.0) + r["amount"]
        cats = [{"category": c, "amount": a, "share": (a / total if total else 0.0)}
                for c, a in sorted(by_cat.items(), key=lambda kv: -kv[1])]

        today = date.today()
        month_start = today.replace(day=1)
        prev_end = month_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        with get_session() as s:
            def span(a, b):
                return _f(s.query(func.coalesce(func.sum(cls.MODEL.amount), 0))
                          .filter(cls.MODEL.status == "active", cls.MODEL.expense_date >= a,
                                  cls.MODEL.expense_date <= b).scalar())
            today_total = span(today, today)
            week_total = span(today - timedelta(days=today.weekday()), today)
            month_total = span(month_start, today)
            prev_month = span(prev_start, prev_end)
            trend = []
            cursor = month_start
            for _ in range(6):
                nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                trend.append({"month": cursor.strftime("%b %Y"), "amount": span(cursor, nxt - timedelta(days=1))})
                cursor = (cursor - timedelta(days=1)).replace(day=1)
            trend.reverse()
        change = ((month_total - prev_month) / prev_month) if prev_month > 0 else None
        return {
            "total": total, "count": len(rows), "by_category": cats, "trend": trend,
            "today": today_total, "week": week_total, "month": month_total,
            "prev_month": prev_month, "month_change": change,
        }


class ExpenseService(_OutflowService):
    """Day-to-day running costs."""


class ExpenditureService(_OutflowService):
    """Larger outlays - its own table and numbering."""
    MODEL = Expenditure
    PREFIX = "EXD"
    AUDIT = "expenditure"
    NOUN = "Expenditure"
    DEFAULTS = EXPENDITURE_CATEGORIES
