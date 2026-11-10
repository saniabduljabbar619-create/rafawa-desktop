# database/services/open_items.py
"""
What is still open: sales / purchases that are not fully settled, and advances that are not fully used.

One reading of the books, shared by the balance statement (printed for a customer / supplier) and the daily
debtor reminder, so they can never disagree with each other or with the Payment Center. Balances come from
`document_balance.document_balances` (returns, price edits, advance allocations and offsets already applied).

    open_documents(s, "sale" | "purchase", party_id=None, walkin_name=None)
    open_advances(s, "given" | "received", party_id=None)

`given`    = Advance OUT: money we handed the party; they still owe it back (as goods or cash).
`received` = Advance IN : money the party handed us; we still owe it back (as goods or cash).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func

from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.services.document_balance import _sum_by, document_balances

_EPS = 0.005


def open_documents(s, kind: str, party_id: Optional[int] = None,
                   walkin_name: Optional[str] = None) -> List[Dict]:
    """Sales (kind="sale") or purchases (kind="purchase") that still owe money, oldest first.

    walkin_name: for the shared walk-in account only - restrict to that one walk-in's documents ("" = unnamed).
    """
    if kind not in ("sale", "purchase"):
        raise ValueError("kind must be 'sale' or 'purchase'")
    model, party_col, prefix = ((Sale, Sale.buyer_party_id, "SAL") if kind == "sale"
                                else (Purchase, Purchase.supplier_party_id, "PUR"))
    q = (s.query(model.id, party_col, model.walkin_name, model.completed_at, model.created_at)
         .filter(model.status.notin_(["draft", "voided"])))
    if party_id is not None:
        q = q.filter(party_col == int(party_id))
    if walkin_name is not None:
        q = q.filter(func.coalesce(model.walkin_name, "") == (walkin_name or ""))
    rows = q.order_by(model.completed_at.asc(), model.id.asc()).all()
    if not rows:
        return []
    balances = document_balances(s, kind, [int(r[0]) for r in rows])
    out: List[Dict] = []
    for doc_id, pid, wname, done, created in rows:
        b = balances.get(int(doc_id))
        if b is None or b.voided or b.balance <= _EPS:
            continue
        out.append({
            "kind": kind, "id": int(doc_id), "ref": f"{prefix}-{int(doc_id)}",
            "party_id": int(pid), "walkin_name": (wname or "").strip(),
            "date": done or created,
            "total": float(b.net_total), "paid": float(min(b.settled, b.net_total)),
            "balance": float(b.balance), "status": b.status,
        })
    return out


def open_advances(s, direction: str, party_id: Optional[int] = None) -> List[Dict]:
    """Money advances (direction "given" = OUT, "received" = IN) with something still unused, oldest first."""
    if direction not in ("given", "received"):
        raise ValueError("direction must be 'given' or 'received'")
    q = (s.query(Advance)
         .filter(Advance.advance_type == "money", Advance.status == "completed", Advance.direction == direction))
    if party_id is not None:
        q = q.filter(Advance.party_id == int(party_id))
    advances = q.order_by(Advance.created_at.asc(), Advance.id.asc()).all()
    if not advances:
        return []
    ids = [int(a.id) for a in advances]
    paid = _sum_by(s, Payment.entity_id, Payment.amount, ids,
                   Payment.entity_type == "advance", Payment.status != "voided")
    allocated = _sum_by(s, AdvanceAllocation.advance_id, AdvanceAllocation.amount, ids)
    out: List[Dict] = []
    for a in advances:
        amount = float(a.amount or 0)
        used = paid.get(int(a.id), 0.0) + allocated.get(int(a.id), 0.0)
        remaining = amount - used
        if remaining <= _EPS:
            continue
        out.append({
            "kind": "advance", "id": int(a.id), "ref": f"ADV-{int(a.id)}",
            "party_id": int(a.party_id), "direction": direction, "date": a.created_at,
            "amount": amount, "used": float(min(used, amount)), "remaining": float(remaining),
            "status": "partial" if used > _EPS else "unused",
            "notes": (a.notes or "").strip(),
        })
    return out
