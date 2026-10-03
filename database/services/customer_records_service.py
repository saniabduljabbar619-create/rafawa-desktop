# database/services/customer_records_service.py
"""
What a customer's profile lists record by record (read-only, a handful of grouped queries):

    sales(party_id)      one row per sale to him: products, total, settled, balance, status
    purchases(party_id)  one row per purchase from him: the same
    products(party_id)   per product: what we bought from him and what we sold him
    history(party_id)    everything in date order - sales, purchases, payments, returns, offset/swap

Totals, paid and balance come from the ONE shared definition (document_balance), so they match every
other screen. The ids on sales/purchases rows are what the swap page takes.
"""
from __future__ import annotations

from typing import Any, Dict, List

from database.models.document_offset import DocumentOffset
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.purchase_return import PurchaseReturn
from database.models.sale import Sale
from database.models.sale_return import SaleReturn
from database.services.document_balance import document_balances
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_role


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _qty(item, kind: str) -> str:
    bags = int(item.qty_bags or 0)
    kg = _f(item.total_weight_kg)
    crops = str(item.product_type_snapshot or "").lower() == "crops"
    return f"{bags:g} bags" + (f"/{kg:,.0f}kg" if crops and kg else "")


def _summary(doc, kind: str) -> str:
    return ", ".join(f"{i.product.name if i.product else '?'} {_qty(i, kind)}" for i in doc.items)


def _line_value(item, kind: str) -> float:
    return _f(item.line_total)


class CustomerRecordsService:

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def documents(party_id: int, kind: str) -> List[Dict[str, Any]]:
        """kind "sale" (what we sold him) or "purchase" (what we bought from him), newest first."""
        model, col = (Sale, Sale.buyer_party_id) if kind == "sale" else (Purchase, Purchase.supplier_party_id)
        prefix = "SAL" if kind == "sale" else "PUR"
        with get_session() as s:
            docs = (s.query(model).filter(col == int(party_id)).filter(model.status != "draft")
                    .order_by(model.completed_at.desc(), model.id.desc()).all())
            bal = document_balances(s, kind, [d.id for d in docs])
            out = []
            for d in docs:
                b = bal.get(int(d.id))
                no = getattr(d, "sale_no" if kind == "sale" else "purchase_no", None) or f"{prefix}-{d.id}"
                out.append({
                    "id": int(d.id), "ref": no, "kind": kind, "date": d.completed_at or d.created_at,
                    "summary": _summary(d, kind), "total": b.net_total if b else _f(d.total_amount),
                    "paid": b.settled if b else 0.0, "balance": b.balance if b else 0.0,
                    "status": b.status if b else "", "voided": bool(b and b.voided),
                    "selectable": bool(b and not b.voided and b.balance > 0.005),
                })
        return out

    @staticmethod
    def sales(party_id: int) -> List[Dict[str, Any]]:
        return CustomerRecordsService.documents(party_id, "sale")

    @staticmethod
    def purchases(party_id: int) -> List[Dict[str, Any]]:
        return CustomerRecordsService.documents(party_id, "purchase")

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def products(party_id: int) -> List[Dict[str, Any]]:
        """Per product: bought from him (we paid / owe him) and sold to him (he paid / owes us)."""
        agg: Dict[int, Dict[str, Any]] = {}

        def bucket(item):
            p = item.product
            return agg.setdefault(int(item.product_id), {
                "product_id": int(item.product_id), "name": p.name if p else "?",
                "type": str(item.product_type_snapshot or "").lower(),
                "bought_bags": 0.0, "bought_kg": 0.0, "bought_value": 0.0,
                "sold_bags": 0.0, "sold_kg": 0.0, "sold_value": 0.0})

        with get_session() as s:
            for d in s.query(Purchase).filter(Purchase.supplier_party_id == int(party_id),
                                              Purchase.status.notin_(["draft", "voided"])).all():
                for i in d.items:
                    b = bucket(i)
                    b["bought_bags"] += _f(i.qty_bags)
                    b["bought_kg"] += _f(i.total_weight_kg)
                    b["bought_value"] += _f(i.line_total)
            for d in s.query(Sale).filter(Sale.buyer_party_id == int(party_id),
                                          Sale.status.notin_(["draft", "voided"])).all():
                for i in d.items:
                    b = bucket(i)
                    b["sold_bags"] += _f(i.qty_bags)
                    b["sold_kg"] += _f(i.total_weight_kg)
                    b["sold_value"] += _f(i.line_total)
        rows = sorted(agg.values(), key=lambda r: r["name"].lower())
        for r in rows:
            r["net"] = round(r["sold_value"] - r["bought_value"], 2)       # + he owes us more than we owe him
        return rows

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def history(party_id: int) -> List[Dict[str, Any]]:
        """Everything, oldest first, with a running position (+ he owes us, - we owe him)."""
        pid = int(party_id)
        events: List[Dict[str, Any]] = []
        for d in CustomerRecordsService.documents(pid, "sale"):
            if not d["voided"]:
                events.append({"date": d["date"], "type": "Sale", "ref": d["ref"], "detail": d["summary"],
                               "delta": d["total"]})
        for d in CustomerRecordsService.documents(pid, "purchase"):
            if not d["voided"]:
                events.append({"date": d["date"], "type": "Purchase", "ref": d["ref"], "detail": d["summary"],
                               "delta": -d["total"]})
        with get_session() as s:
            for p in (s.query(Payment).filter(Payment.party_id == pid, Payment.status != "voided",
                                              Payment.entity_type.in_(["sale", "purchase"]))
                      .filter(Payment.method != "advance").all()):
                inbound = p.direction == "in"
                if str(p.notes or "").startswith("Advance settlement"):
                    continue
                events.append({"date": p.created_at, "type": "Payment in" if inbound else "Payment out",
                               "ref": f"{'SAL' if p.entity_type == 'sale' else 'PUR'}-{p.entity_id}",
                               "detail": (p.method or "").title(), "delta": -_f(p.amount) if inbound else _f(p.amount)})
            for r in s.query(SaleReturn).filter(SaleReturn.party_id == pid).all():
                events.append({"date": r.created_at, "type": "Sale return", "ref": f"SAL-{r.sale_id}",
                               "detail": "", "delta": -abs(_f(r.amount))})
            for r in s.query(PurchaseReturn).filter(PurchaseReturn.party_id == pid).all():
                events.append({"date": r.created_at, "type": "Purchase return", "ref": f"PUR-{r.purchase_id}",
                               "detail": "", "delta": abs(_f(r.amount))})
            for o in s.query(DocumentOffset).filter(DocumentOffset.party_id == pid,
                                                    DocumentOffset.status == "active").all():
                events.append({"date": o.created_at, "type": "Swap" if o.kind == "swap" else "Set off",
                               "ref": f"PUR-{o.purchase_id} / SAL-{o.sale_id}", "detail": o.note or "",
                               "delta": 0.0, "amount": _f(o.amount)})
        events.sort(key=lambda e: (e["date"] is None, e["date"]))
        run = 0.0
        for e in events:
            run += e["delta"]
            e["running"] = round(run, 2)
        return events
