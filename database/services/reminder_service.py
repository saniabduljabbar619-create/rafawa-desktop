# database/services/reminder_service.py
"""
Who still owes us money - the list behind the daily debtor reminder.

Two kinds of money outstanding, per customer (or supplier, or named walk-in):
  * Advance OUT      money we handed over that has not been used up / paid back yet
  * Sales            unpaid or part-paid sales

Every line carries WHEN it was given (and how many days ago that is), what it was, how much was paid, what is
left and its current status, so the people to chase can be tracked. Amounts come from the same shared reading
of the books as everything else (database/services/open_items.py).

    ReminderService.collect(today=None) -> {
        "as_of": date, "groups": [group, ...], "parties": int, "total": float,
        "advance_out_total": float, "sales_total": float,
    }
    group = {"party_id", "walkin_name", "name", "phone", "items": [item, ...], "balance",
             "advance_out_balance", "sales_balance", "oldest_days"}
    item  = {"type": "advance_out" | "sale", "ref", "doc_id", "given_at", "days", "total", "paid", "balance",
             "status": "unused" | "partial" | "unpaid"}

Groups are ordered longest-waiting first, then biggest balance first.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from database.models.party import Party
from database.services.open_items import open_advances, open_documents
from database.services.walkin_account import is_shared_walkin
from database.session import get_session


def _as_date(dt) -> Optional[date]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.date()
    return dt if isinstance(dt, date) else None


class ReminderService:

    @staticmethod
    def collect(today: Optional[date] = None) -> Dict[str, Any]:
        today = today or date.today()
        groups: Dict[Tuple[int, str], Dict[str, Any]] = {}

        def group_for(party_id: int, walkin_name: str) -> Dict[str, Any]:
            key = (party_id, walkin_name)
            if key not in groups:
                groups[key] = {"party_id": party_id, "walkin_name": walkin_name, "name": "", "phone": "",
                               "items": [], "balance": 0.0, "advance_out_balance": 0.0,
                               "sales_balance": 0.0, "oldest_days": 0}
            return groups[key]

        def add(g: Dict[str, Any], item: Dict[str, Any]) -> None:
            g["items"].append(item)
            g["balance"] = round(g["balance"] + item["balance"], 2)
            bucket = "advance_out_balance" if item["type"] == "advance_out" else "sales_balance"
            g[bucket] = round(g[bucket] + item["balance"], 2)
            g["oldest_days"] = max(g["oldest_days"], item["days"])

        with get_session() as s:
            for d in open_documents(s, "sale"):
                given = _as_date(d["date"])
                add(group_for(d["party_id"], d["walkin_name"]), {
                    "type": "sale", "ref": d["ref"], "doc_id": d["id"], "given_at": d["date"],
                    "days": max((today - given).days, 0) if given else 0,
                    "total": d["total"], "paid": d["paid"], "balance": d["balance"],
                    "status": d["status"],
                })
            for a in open_advances(s, "given"):
                given = _as_date(a["date"])
                add(group_for(a["party_id"], ""), {
                    "type": "advance_out", "ref": a["ref"], "doc_id": a["id"], "given_at": a["date"],
                    "days": max((today - given).days, 0) if given else 0,
                    "total": a["amount"], "paid": a["used"], "balance": a["remaining"],
                    "status": a["status"],
                })
            parties = {int(p.id): p for p in
                       s.query(Party).filter(Party.id.in_({k[0] for k in groups} or {0})).all()}
            for (pid, wname), g in groups.items():
                party = parties.get(pid)
                name = party.display_name if party else f"Party #{pid}"
                if party is not None and party.walkin_code:
                    name = f"{name} ({party.walkin_code})"
                if wname and is_shared_walkin(pid, s):
                    name = wname
                elif is_shared_walkin(pid, s):
                    name = "Walk-in (no name)"
                g["name"], g["phone"] = name, (party.phone or "") if party else ""
                g["items"].sort(key=lambda i: (-i["days"], i["ref"]))

        ordered: List[Dict[str, Any]] = sorted(groups.values(),
                                               key=lambda g: (-g["oldest_days"], -g["balance"], g["name"].lower()))
        return {
            "as_of": today, "groups": ordered, "parties": len(ordered),
            "total": round(sum(g["balance"] for g in ordered), 2),
            "advance_out_total": round(sum(g["advance_out_balance"] for g in ordered), 2),
            "sales_total": round(sum(g["sales_balance"] for g in ordered), 2),
        }
