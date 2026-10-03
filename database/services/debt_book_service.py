# database/services/debt_book_service.py
"""
The Debt Center's own book (see models/debt.py): people we owe and people who owe us, one sheet each.

STANDALONE by design - it never reads or writes customers, suppliers, sales, purchases, payments or the
drawer, and nothing else reads it. Admin only; every change is audited; a row is crossed out (removed),
never deleted, and can be brought back.

    owed      the amount of one debt row (creditor: they lent/supplied us; debtor: we lent/gave them)
    payments  each saved against ONE row (creditor: we paid them; debtor: they paid us); a row's paid-so-far
              is the sum of its payments, so a later payment never replaces an earlier one
    balance = sum(owed) - sum(payments)  over rows and payments not removed  (negative = overpaid)
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from database.models.debt import DebtEntry, DebtPayment, DebtPerson
from database.services.audit_service import AuditService
from database.session import get_session
from shared.auth.permissions import require_admin
from shared.auth.session import require_user_id

KINDS = ("creditor", "debtor")
METHODS = ["cash", "transfer", "pos", "cheque", "goods", "other"]
EDITABLE = ("date", "details", "owed", "note")
EPS = 0.005


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(v) -> float:
    if v is None:
        return 0.0
    try:
        return round(float(str(v).replace(",", "").strip() or 0), 2)
    except ValueError:
        raise ValueError("Amounts must be numbers.")


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Type the date as day/month/year, e.g. 25/12/2026.")


def _check_kind(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind must be 'creditor' or 'debtor'.")
    return kind


def _person_row(p: DebtPerson, owed: float, paid: float, count: int, last) -> Dict[str, Any]:
    return {"id": int(p.id), "kind": p.kind, "name": p.name, "phone": p.phone or "", "address": p.address or "",
            "note": p.note or "", "archived": bool(p.is_archived), "owed": owed, "paid": paid,
            "balance": round(owed - paid, 2), "entries": count, "last": last}


def _clean_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    owed = _money(data.get("owed"))
    if owed <= 0:
        raise ValueError("Enter the amount of the debt.")
    details = str(data.get("details") or "").strip()
    if not details:
        raise ValueError("Write what it was for.")
    return {"entry_date": _as_date(data.get("date") or date.today()), "details": details[:255], "owed": owed,
            "note": (str(data.get("note") or "").strip() or None)}


def _paid_by_entry(s, entry_ids) -> Dict[int, float]:
    out: Dict[int, float] = {}
    ids = list(entry_ids)
    for i in range(0, len(ids), 500):
        for eid, total in (s.query(DebtPayment.entry_id, func.coalesce(func.sum(DebtPayment.amount), 0))
                           .filter(DebtPayment.entry_id.in_(ids[i:i + 500]), DebtPayment.is_removed == False)  # noqa: E712
                           .group_by(DebtPayment.entry_id)):
            out[int(eid)] = _f(total)
    return out


class DebtBookService:

    # ------------------------------------------------------------------ people
    @staticmethod
    @require_admin
    def add_person(kind: str, name: str, phone: str = "", address: str = "", note: str = "") -> int:
        kind = _check_kind(kind)
        name = (name or "").strip()
        if not name:
            raise ValueError("Enter the person's name.")
        with get_session() as s:
            dup = (s.query(DebtPerson.id).filter(DebtPerson.kind == kind, DebtPerson.is_archived == False,  # noqa: E712
                                                 DebtPerson.name.ilike(name)).first())
            if dup:
                raise ValueError(f"{name} is already in your {kind}s list.")
            p = DebtPerson(kind=kind, name=name[:150], phone=(phone or "").strip()[:40] or None,
                           address=(address or "").strip()[:255] or None, note=(note or "").strip() or None,
                           created_by_user_id=int(require_user_id()))
            s.add(p)
            s.flush()
            AuditService.record("debt.person.add", "debt_person", p.id, f"{kind.title()} added: {p.name}", session=s)
            s.commit()
            return int(p.id)

    @staticmethod
    @require_admin
    def update_person(person_id: int, name: str, phone: str = "", address: str = "", note: str = "") -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("Enter the person's name.")
        with get_session() as s:
            p = s.get(DebtPerson, int(person_id))
            if p is None:
                raise ValueError("Person not found.")
            p.name, p.phone = name[:150], (phone or "").strip()[:40] or None
            p.address, p.note = (address or "").strip()[:255] or None, (note or "").strip() or None
            AuditService.record("debt.person.edit", "debt_person", p.id, f"{p.name} details changed", session=s)
            s.commit()

    @staticmethod
    @require_admin
    def set_archived(person_id: int, archived: bool) -> None:
        with get_session() as s:
            p = s.get(DebtPerson, int(person_id))
            if p is None:
                raise ValueError("Person not found.")
            p.is_archived = bool(archived)
            AuditService.record("debt.person.archive" if archived else "debt.person.unarchive", "debt_person", p.id,
                                f"{p.name} {'archived' if archived else 'restored'}", session=s)
            s.commit()

    @staticmethod
    @require_admin
    def people(kind: str, search: str = "", include_archived: bool = False) -> List[Dict[str, Any]]:
        """Everyone in the list with their totals, biggest balance first."""
        kind = _check_kind(kind)
        term = (search or "").strip().lower()
        with get_session() as s:
            q = s.query(DebtPerson).filter(DebtPerson.kind == kind)
            if not include_archived:
                q = q.filter(DebtPerson.is_archived == False)  # noqa: E712
            people = q.all()
            totals: Dict[int, List[Any]] = {}
            ids = [p.id for p in people]
            if ids:
                live = s.query(DebtEntry).filter(DebtEntry.person_id.in_(ids), DebtEntry.is_removed == False).all()  # noqa: E712
                paid = _paid_by_entry(s, [e.id for e in live])
                for e in live:
                    t = totals.setdefault(int(e.person_id), [0.0, 0.0, 0, None])
                    t[0] += _f(e.owed)
                    t[1] += paid.get(int(e.id), 0.0)
                    t[2] += 1
                    if t[3] is None or e.entry_date > t[3]:
                        t[3] = e.entry_date
            rows = [_person_row(p, *(totals.get(int(p.id), [0.0, 0.0, 0, None]))) for p in people
                    if not term or term in p.name.lower() or term in (p.phone or "").lower()]
        return sorted(rows, key=lambda r: (-r["balance"], r["name"].lower()))

    @staticmethod
    @require_admin
    def overview() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k in KINDS:
            rows = DebtBookService.people(k)
            out[k] = {"people": len(rows), "owing": sum(1 for r in rows if r["balance"] > 0.005),
                      "balance": round(sum(r["balance"] for r in rows if r["balance"] > 0), 2),
                      "owed": round(sum(r["owed"] for r in rows), 2), "paid": round(sum(r["paid"] for r in rows), 2)}
        out["net"] = round(out["debtor"]["balance"] - out["creditor"]["balance"], 2)
        return out

    # ------------------------------------------------------------------ one person's sheet
    @staticmethod
    @require_admin
    def sheet(person_id: int) -> Dict[str, Any]:
        """The person and their rows in the order written, each with the running balance."""
        with get_session() as s:
            p = s.get(DebtPerson, int(person_id))
            if p is None:
                raise ValueError("Person not found.")
            entries = (s.query(DebtEntry).filter(DebtEntry.person_id == p.id)
                       .order_by(DebtEntry.entry_date.asc(), DebtEntry.id.asc()).all())
            pays: Dict[int, List[DebtPayment]] = {}
            for pm in (s.query(DebtPayment).filter(DebtPayment.entry_id.in_([e.id for e in entries] or [0]))
                       .order_by(DebtPayment.pay_date.asc(), DebtPayment.id.asc()).all()):
                pays.setdefault(int(pm.entry_id), []).append(pm)
            run = Decimal("0")
            owed = paid = Decimal("0")
            rows = []
            for e in entries:
                mine = pays.get(int(e.id), [])
                e_paid = sum((Decimal(str(m.amount or 0)) for m in mine if not m.is_removed), Decimal("0"))
                if not e.is_removed:
                    run += Decimal(str(e.owed or 0)) - e_paid
                    owed += Decimal(str(e.owed or 0))
                    paid += e_paid
                rows.append({"id": int(e.id), "date": e.entry_date, "details": e.details, "owed": _f(e.owed),
                             "paid": float(e_paid), "row_balance": round(_f(e.owed) - float(e_paid), 2),
                             "note": e.note or "", "running": float(run), "removed": bool(e.is_removed),
                             "removed_reason": e.removed_reason or "", "at": e.created_at,
                             "payments": [{"id": int(m.id), "date": m.pay_date, "amount": _f(m.amount),
                                           "method": m.method or "", "note": m.note or "",
                                           "removed": bool(m.is_removed), "removed_reason": m.removed_reason or ""}
                                          for m in mine]})
            person = _person_row(p, float(owed), float(paid), sum(1 for e in entries if not e.is_removed),
                                 max((e.entry_date for e in entries if not e.is_removed), default=None))
        return {"person": person, "rows": rows,
                "figures": {"owed": float(owed), "paid": float(paid), "balance": float(owed - paid)}}

    # ------------------------------------------------------------------ rows
    @staticmethod
    @require_admin
    def add_entry(person_id: int, data: Dict[str, Any]) -> int:
        vals = _clean_entry(data)
        with get_session() as s:
            p = s.get(DebtPerson, int(person_id))
            if p is None:
                raise ValueError("Person not found.")
            e = DebtEntry(person_id=p.id, created_by_user_id=int(require_user_id()), **vals)
            s.add(e)
            s.flush()
            AuditService.record("debt.entry.add", "debt_person", p.id,
                                f"{p.name}: {vals['details']} ({vals['owed']:,.2f})",
                                session=s)
            s.commit()
            return int(e.id)

    @staticmethod
    @require_admin
    def edit_entry(entry_id: int, changes: Dict[str, Any]) -> None:
        """Change one or more cells of a row; the change is kept in the audit trail."""
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
        with get_session() as s:
            e = s.get(DebtEntry, int(entry_id))
            if e is None:
                raise ValueError("Row not found.")
            if e.is_removed:
                raise ValueError("Bring the row back before changing it.")
            current = {"date": e.entry_date, "details": e.details, "owed": _f(e.owed), "note": e.note}
            merged = {**current, **changes}
            vals = _clean_entry(merged)
            already = _paid_by_entry(s, [e.id]).get(int(e.id), 0.0)
            if vals["owed"] < already - EPS:
                raise ValueError(f"{already:,.2f} has already been paid on this row, so the amount cannot be "
                                 "less than that.")
            before = {}
            for k, v in vals.items():
                old = getattr(e, k)
                if str(old) != str(v):
                    before[k] = str(old)
                    setattr(e, k, v)
            if before:
                e.updated_by_user_id, e.updated_at = int(require_user_id()), datetime.now()
                AuditService.record("debt.entry.edit", "debt_person", e.person_id, "Row changed",
                                    {"entry_id": e.id, "before": before,
                                     "after": {k: str(vals[k]) for k in before}}, session=s)
            s.commit()

    @staticmethod
    @require_admin
    def remove_entry(entry_id: int, reason: str = "") -> None:
        with get_session() as s:
            e = s.get(DebtEntry, int(entry_id))
            if e is None:
                raise ValueError("Row not found.")
            if e.is_removed:
                return
            e.is_removed, e.removed_at = True, datetime.now()
            e.removed_by_user_id, e.removed_reason = int(require_user_id()), (reason or "").strip()[:255] or None
            AuditService.record("debt.entry.remove", "debt_person", e.person_id,
                                f"Row removed: {e.details}" + (f" ({reason})" if reason else ""),
                                {"entry_id": e.id}, session=s)
            s.commit()

    @staticmethod
    @require_admin
    def restore_entry(entry_id: int) -> None:
        with get_session() as s:
            e = s.get(DebtEntry, int(entry_id))
            if e is None:
                raise ValueError("Row not found.")
            if not e.is_removed:
                return
            e.is_removed = False
            e.removed_at = e.removed_by_user_id = e.removed_reason = None
            AuditService.record("debt.entry.restore", "debt_person", e.person_id, f"Row brought back: {e.details}",
                                {"entry_id": e.id}, session=s)
            s.commit()

    # ------------------------------------------------------------------ payments (saved one by one, on one row)
    @staticmethod
    @require_admin
    def pay_entry(entry_id: int, data: Dict[str, Any]) -> int:
        """Save a payment against ONE debt row. Earlier payments on the row are never touched."""
        amount = _money(data.get("amount"))
        if amount <= 0:
            raise ValueError("Enter the amount paid.")
        with get_session() as s:
            e = s.get(DebtEntry, int(entry_id))
            if e is None:
                raise ValueError("Row not found.")
            if e.is_removed:
                raise ValueError("Bring the row back before paying on it.")
            left = round(_f(e.owed) - _paid_by_entry(s, [e.id]).get(int(e.id), 0.0), 2)
            if amount > left + EPS:
                raise ValueError(f"Only {max(left, 0):,.2f} is still unpaid on this row - that payment is more.")
            pm = DebtPayment(entry_id=e.id, pay_date=_as_date(data.get("date") or date.today()), amount=amount,
                             method=(str(data.get("method") or "").strip().lower()[:30] or None),
                             note=(str(data.get("note") or "").strip() or None),
                             created_by_user_id=int(require_user_id()))
            s.add(pm)
            s.flush()
            AuditService.record("debt.payment.add", "debt_person", e.person_id,
                                f"Payment {amount:,.2f} on: {e.details}", {"entry_id": e.id, "payment_id": pm.id},
                                session=s)
            s.commit()
            return int(pm.id)

    @staticmethod
    @require_admin
    def remove_payment(payment_id: int, reason: str = "") -> None:
        """Cross a payment out (kept in the history, no longer counted)."""
        with get_session() as s:
            pm = s.get(DebtPayment, int(payment_id))
            if pm is None:
                raise ValueError("Payment not found.")
            if pm.is_removed:
                return
            pm.is_removed, pm.removed_at = True, datetime.now()
            pm.removed_by_user_id, pm.removed_reason = int(require_user_id()), (reason or "").strip()[:255] or None
            AuditService.record("debt.payment.remove", "debt_person", pm.entry.person_id,
                                f"Payment {_f(pm.amount):,.2f} removed" + (f" ({reason})" if reason else ""),
                                {"entry_id": pm.entry_id, "payment_id": pm.id}, session=s)
            s.commit()

    @staticmethod
    @require_admin
    def restore_payment(payment_id: int) -> None:
        with get_session() as s:
            pm = s.get(DebtPayment, int(payment_id))
            if pm is None:
                raise ValueError("Payment not found.")
            if not pm.is_removed:
                return
            if pm.entry.is_removed:
                raise ValueError("Bring the row back first.")
            left = _f(pm.entry.owed) - _paid_by_entry(s, [pm.entry_id]).get(int(pm.entry_id), 0.0)
            if _f(pm.amount) > left + EPS:
                raise ValueError("Bringing this payment back would pay more than the row owes.")
            pm.is_removed = False
            pm.removed_at = pm.removed_by_user_id = pm.removed_reason = None
            AuditService.record("debt.payment.restore", "debt_person", pm.entry.person_id,
                                f"Payment {_f(pm.amount):,.2f} brought back",
                                {"entry_id": pm.entry_id, "payment_id": pm.id}, session=s)
            s.commit()
