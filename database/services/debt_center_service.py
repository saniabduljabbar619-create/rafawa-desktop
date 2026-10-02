# database/services/debt_center_service.py
"""
Debt Center: who we owe (creditors) and who owes us (debtors), and paying it down.

    creditors  parties we still owe on purchases   -> we pay OUT
    debtors    parties who still owe us on sales   -> we receive IN

Every balance comes from document_balance.document_balances (the one shared definition), so
returns, discounts, price adjustments, advance allocations and voids are already accounted for.
A payment here is the same Payment row every other screen writes (method + note), allocated to
the oldest open documents first (or only the documents the user ticked).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from database.models.party import Party
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.services.audit_service import AuditService
from database.services.document_balance import document_balances
from database.session import get_session
from shared.auth.permissions import require_admin
from shared.auth.session import require_user_id
from shared.config.constants import WALKIN_PARTY_ID

OVERDUE_DAYS = 30
EPS = 0.005
SIDES = ("creditors", "debtors")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _days(when, now) -> int:
    return max((now.date() - when.date()).days, 0) if when else 0


def _doc_model(side: str):
    if side == "creditors":
        return Purchase, Purchase.supplier_party_id, "purchase", "PUR"
    if side == "debtors":
        return Sale, Sale.buyer_party_id, "sale", "SAL"
    raise ValueError("side must be 'creditors' or 'debtors'")


def _open_docs(s, side: str, party_id: Optional[int] = None, include_settled: bool = False,
               now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    model, party_col, kind, prefix = _doc_model(side)
    now = now or datetime.now()
    q = s.query(model.id, party_col, model.completed_at, model.created_at, model.status).filter(
        model.status != "voided", party_col != WALKIN_PARTY_ID)
    if party_id:
        q = q.filter(party_col == int(party_id))
    rows = q.order_by(model.completed_at.asc(), model.id.asc()).all()
    balances = document_balances(s, kind, [r[0] for r in rows])
    out = []
    for did, pid, completed, created, _st in rows:
        b = balances.get(int(did))
        if b is None or (b.balance <= EPS and not include_settled):
            continue
        when = completed or created
        out.append({
            "kind": kind, "doc_id": int(did), "ref": f"{prefix}-{int(did)}", "party_id": int(pid),
            "date": when, "age_days": _days(when, now), "total": b.net_total, "settled": b.settled,
            "balance": b.balance, "status": b.status,
        })
    return out


class DebtCenterService:

    @staticmethod
    @require_admin
    def parties(side: str) -> List[Dict[str, Any]]:
        """One row per creditor/debtor, biggest debt first."""
        now = datetime.now()
        with get_session() as s:
            docs = _open_docs(s, side, now=now)
            other = _open_docs(s, "debtors" if side == "creditors" else "creditors", now=now)
            ids = {d["party_id"] for d in docs}
            info = {int(p.id): p for p in s.query(Party).filter(Party.id.in_(ids or {0})).all()} if ids else {}
        opposite: Dict[int, float] = defaultdict(float)
        for d in other:
            opposite[d["party_id"]] += d["balance"]
        grouped: Dict[int, Dict[str, Any]] = {}
        for d in docs:
            g = grouped.setdefault(d["party_id"], {
                "party_id": d["party_id"], "balance": 0.0, "docs": 0, "oldest_days": 0,
                "overdue": 0.0, "last_at": None})
            g["balance"] += d["balance"]
            g["docs"] += 1
            g["oldest_days"] = max(g["oldest_days"], d["age_days"])
            if d["age_days"] > OVERDUE_DAYS:
                g["overdue"] += d["balance"]
            if d["date"] and (g["last_at"] is None or d["date"] > g["last_at"]):
                g["last_at"] = d["date"]
        out = []
        for pid, g in grouped.items():
            p = info.get(pid)
            g["name"] = p.display_name if p else f"Party {pid}"
            g["phone"] = (p.phone or "") if p else ""
            g["opposite"] = opposite.get(pid, 0.0)
            out.append(g)
        return sorted(out, key=lambda r: -r["balance"])

    @staticmethod
    @require_admin
    def overview() -> Dict[str, Any]:
        cred = DebtCenterService.parties("creditors")
        debt = DebtCenterService.parties("debtors")
        payable = sum(r["balance"] for r in cred)
        receivable = sum(r["balance"] for r in debt)
        return {
            "payable": payable, "creditors": len(cred),
            "payable_overdue": sum(r["overdue"] for r in cred),
            "receivable": receivable, "debtors": len(debt),
            "receivable_overdue": sum(r["overdue"] for r in debt),
            "net": receivable - payable,
        }

    @staticmethod
    @require_admin
    def worksheet(party_id: int, include_settled: bool = False) -> Dict[str, Any]:
        """Everything about one party: what we owe them, what they owe us, and the payment trail."""
        from database.services.party_balance_service import PartyBalanceService
        party_id = int(party_id)
        with get_session() as s:
            p = s.query(Party).filter(Party.id == party_id).first()
            if p is None:
                raise ValueError("Party not found.")
            payables = _open_docs(s, "creditors", party_id, include_settled)
            receivables = _open_docs(s, "debtors", party_id, include_settled)
            doc_ids = {"purchase": [d["doc_id"] for d in _open_docs(s, "creditors", party_id, True)],
                       "sale": [d["doc_id"] for d in _open_docs(s, "debtors", party_id, True)]}
            pays = []
            for kind, ids in doc_ids.items():
                if not ids:
                    continue
                for pay in (s.query(Payment).filter(Payment.entity_type == kind, Payment.entity_id.in_(ids),
                                                    Payment.status != "voided")
                            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(300).all()):
                    pays.append({"id": int(pay.id), "date": pay.created_at, "kind": kind,
                                 "doc_id": int(pay.entity_id), "amount": _f(pay.amount),
                                 "direction": pay.direction, "method": pay.method or "",
                                 "notes": pay.notes or ""})
            pays.sort(key=lambda r: (r["date"] or datetime.min, r["id"]), reverse=True)
            head = {"party_id": party_id, "name": p.display_name, "phone": p.phone or "",
                    "address": p.address or "", "is_customer": bool(p.is_customer),
                    "is_supplier": bool(p.is_supplier)}
        from database.services.offset_service import OffsetService
        offsets = OffsetService.list_for_party(party_id)
        overall = PartyBalanceService.compute(party_id, int(require_user_id()), "admin").get("overall", {})
        we_owe = sum(d["balance"] for d in payables)
        they_owe = sum(d["balance"] for d in receivables)
        return {
            **head, "payables": payables, "receivables": receivables, "payments": pays[:300], "offsets": offsets,
            "we_owe": we_owe, "they_owe": they_owe, "net": they_owe - we_owe,
            "advance_out": _f(overall.get("advance_out")),   # money we advanced them
            "advance_in": _f(overall.get("advance_in")),     # deposits they hold with us
        }

    @staticmethod
    @require_admin
    def pay(party_id: int, side: str, amount: float, method: str = "cash", note: str = "",
            doc_ids: Optional[Iterable[int]] = None) -> Dict[str, Any]:
        """
        Pay a creditor (side="creditors") or receive from a debtor (side="debtors").

        The amount is spread over the oldest open documents first, or only over `doc_ids` when
        given. It may not exceed what those documents still owe: an overpayment would silently
        become a credit nobody can see. Everything is written in ONE transaction.
        """
        model, _col, kind, prefix = _doc_model(side)
        amount = round(_f(amount), 2)
        if amount <= 0:
            raise ValueError("Enter an amount greater than zero.")
        wanted = {int(i) for i in doc_ids} if doc_ids else None
        direction = "out" if side == "creditors" else "in"
        user_id = int(require_user_id())
        note_text = ("Debt Center" + (f": {note.strip()}" if (note or "").strip() else ""))[:255]

        with get_session() as s:
            docs = _open_docs(s, side, int(party_id))
            if wanted is not None:
                docs = [d for d in docs if d["doc_id"] in wanted]
            owed = round(sum(d["balance"] for d in docs), 2)
            if not docs:
                raise ValueError("Nothing is owed on the selected documents.")
            if amount > owed + EPS:
                raise ValueError(f"That is more than is owed ({owed:,.2f}). Enter {owed:,.2f} or less.")
            left = amount
            allocations = []
            for d in docs:
                if left <= EPS:
                    break
                part = round(min(left, d["balance"]), 2)
                s.add(Payment(entity_type=kind, entity_id=d["doc_id"], party_id=int(party_id),
                              direction=direction, amount=part, method=(method or "cash").strip().lower(),
                              notes=note_text, created_by_user_id=user_id, created_at=datetime.now()))
                allocations.append({"doc_id": d["doc_id"], "ref": d["ref"], "amount": part,
                                    "remaining": round(d["balance"] - part, 2)})
                left = round(left - part, 2)
            AuditService.record(
                f"debt.{'pay' if side == 'creditors' else 'receive'}", "party", int(party_id),
                f"{'Paid' if side == 'creditors' else 'Received'} {amount:,.2f} ({method}) across "
                f"{len(allocations)} document(s)", {"allocations": allocations, "note": note}, session=s)
            s.commit()
        return {"paid": amount, "allocations": allocations, "still_owed": round(owed - amount, 2)}
