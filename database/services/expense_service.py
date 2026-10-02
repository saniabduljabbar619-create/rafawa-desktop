# database/services/expense_service.py
"""
Business expenses (rent, wages, fuel...). Admin only; every change is audited.

An expense is never edited into a different amount silently or deleted: edits are written to
the audit log with before/after, and a wrong entry is voided with a reason.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func

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
METHODS = ["cash", "transfer", "pos", "cheque"]


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _no(expense_id: int) -> str:
    return f"EXP-{int(expense_id):05d}"


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _row(e: Expense) -> Dict[str, Any]:
    return {
        "id": int(e.id), "expense_no": e.expense_no or _no(e.id), "date": e.expense_date,
        "category": e.category, "description": e.description, "paid_to": e.paid_to or "",
        "amount": _f(e.amount), "method": e.method or "", "reference": e.reference or "",
        "notes": e.notes or "", "status": e.status, "void_reason": e.void_reason or "",
        "created_at": e.created_at,
    }


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    amount = _f(data.get("amount"))
    if amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    category = str(data.get("category") or "").strip()
    if not category:
        raise ValueError("Choose a category.")
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


class ExpenseService:

    @staticmethod
    @require_admin
    def add(data: Dict[str, Any]) -> int:
        vals = _clean(data)
        with get_session() as s:
            e = Expense(created_by_user_id=int(require_user_id()), **vals)
            s.add(e)
            s.flush()
            e.expense_no = _no(e.id)
            AuditService.record("expense.add", "expense", e.id,
                                f"{e.expense_no}: {e.category} {vals['amount']:,.2f} - {e.description}", session=s)
            s.commit()
            return int(e.id)

    @staticmethod
    @require_admin
    def update(expense_id: int, data: Dict[str, Any]) -> None:
        vals = _clean(data)
        with get_session() as s:
            e = s.query(Expense).filter(Expense.id == int(expense_id)).with_for_update().first()
            if e is None:
                raise ValueError("Expense not found.")
            if e.status == "voided":
                raise ValueError("A voided expense cannot be edited.")
            before = {k: str(getattr(e, k)) for k in vals if str(getattr(e, k)) != str(vals[k])}
            for k, v in vals.items():
                setattr(e, k, v)
            if before:
                AuditService.record("expense.edit", "expense", e.id, f"{e.expense_no} edited",
                                    {"before": before, "after": {k: str(vals[k]) for k in before}}, session=s)
            s.commit()

    @staticmethod
    @require_admin
    def void(expense_id: int, reason: str) -> None:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("Give a reason for voiding this expense.")
        with get_session() as s:
            e = s.query(Expense).filter(Expense.id == int(expense_id)).with_for_update().first()
            if e is None:
                raise ValueError("Expense not found.")
            if e.status == "voided":
                return
            e.status = "voided"
            e.voided_at = datetime.now()
            e.voided_by_user_id = int(require_user_id())
            e.void_reason = reason[:255]
            AuditService.record("expense.void", "expense", e.id,
                                f"{e.expense_no} voided ({_f(e.amount):,.2f}): {reason}", session=s)
            s.commit()

    @staticmethod
    @require_admin
    def list_expenses(start: Optional[date] = None, end: Optional[date] = None, category: str = "",
             search: str = "", include_voided: bool = False, limit: int = 1000) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(Expense)
            if not include_voided:
                q = q.filter(Expense.status == "active")
            if start:
                q = q.filter(Expense.expense_date >= start)
            if end:
                q = q.filter(Expense.expense_date <= end)
            if category:
                q = q.filter(Expense.category == category)
            term = (search or "").strip()
            if term:
                like = f"%{term}%"
                q = q.filter((Expense.description.like(like)) | (Expense.paid_to.like(like))
                             | (Expense.reference.like(like)) | (Expense.expense_no.like(like)))
            rows = q.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(int(limit)).all()
            return [_row(e) for e in rows]

    @staticmethod
    @require_admin
    def categories() -> List[str]:
        with get_session() as s:
            used = [r[0] for r in s.query(Expense.category).distinct().all() if r[0]]
        return DEFAULT_CATEGORIES + sorted(c for c in used if c not in DEFAULT_CATEGORIES)

    @staticmethod
    @require_admin
    def summary(start: Optional[date] = None, end: Optional[date] = None, category: str = "",
                search: str = "") -> Dict[str, Any]:
        """Totals for the chosen filter, a per-category split and the last six months' trend."""
        rows = ExpenseService.list_expenses(start, end, category, search, limit=100000)
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
                return _f(s.query(func.coalesce(func.sum(Expense.amount), 0))
                          .filter(Expense.status == "active", Expense.expense_date >= a,
                                  Expense.expense_date <= b).scalar())
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
