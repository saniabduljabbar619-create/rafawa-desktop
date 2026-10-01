# database/services/customer_directory_service.py
"""
Everything the Customers directory shows about each customer, for ALL of them
in a handful of grouped queries (never one lookup per row):

    receivable   what they still owe us on non-voided sales (shared document_balance rules)
    deposit      money advances they gave us that have not been used up yet
    sales_count  number of non-voided sales
    last_sale_at date of their most recent sale
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, Optional

from sqlalchemy import func

from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.models.sale import Sale
from database.services.document_balance import document_balances
from database.session import get_session


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class CustomerDirectoryService:

    @staticmethod
    def summaries(party_ids: Optional[Iterable[int]] = None) -> Dict[int, Dict[str, object]]:
        ids = None if party_ids is None else sorted({int(i) for i in party_ids})
        if ids is not None and not ids:
            return {}
        with get_session() as s:
            q = s.query(Sale.id, Sale.buyer_party_id, Sale.created_at).filter(Sale.status != "voided")
            if ids is not None:
                q = q.filter(Sale.buyer_party_id.in_(ids))
            sales = q.all()
            balances = document_balances(s, "sale", [r[0] for r in sales])

            out: Dict[int, Dict[str, object]] = defaultdict(lambda: {
                "receivable": 0.0, "deposit": 0.0, "sales_count": 0, "last_sale_at": None})
            for sid, pid, created in sales:
                row = out[int(pid)]
                bal = balances.get(int(sid))
                if bal is not None:
                    row["receivable"] += bal.balance
                row["sales_count"] += 1
                if created and (row["last_sale_at"] is None or created > row["last_sale_at"]):
                    row["last_sale_at"] = created

            # deposits: received money advances minus what has been paid back / applied
            aq = s.query(Advance.id, Advance.party_id, Advance.amount).filter(
                Advance.direction == "received", Advance.advance_type == "money",
                Advance.status == "completed")
            if ids is not None:
                aq = aq.filter(Advance.party_id.in_(ids))
            advances = aq.all()
            if advances:
                adv_ids = [a[0] for a in advances]
                used: Dict[int, float] = defaultdict(float)
                for chunk_start in range(0, len(adv_ids), 500):
                    chunk = adv_ids[chunk_start:chunk_start + 500]
                    for aid, total in (s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                                       .filter(Payment.entity_type == "advance", Payment.entity_id.in_(chunk),
                                               Payment.status != "voided").group_by(Payment.entity_id)):
                        used[int(aid)] += _f(total)
                    for aid, total in (s.query(AdvanceAllocation.advance_id,
                                               func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                                       .filter(AdvanceAllocation.advance_id.in_(chunk))
                                       .group_by(AdvanceAllocation.advance_id)):
                        used[int(aid)] += _f(total)
                for aid, pid, amount in advances:
                    out[int(pid)]["deposit"] += max(_f(amount) - used.get(int(aid), 0.0), 0.0)
        return dict(out)

    @staticmethod
    def empty() -> Dict[str, object]:
        return {"receivable": 0.0, "deposit": 0.0, "sales_count": 0, "last_sale_at": None}

    @staticmethod
    def days_since(when: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
        if when is None:
            return None
        return max(((now or datetime.now()).date() - when.date()).days, 0)
