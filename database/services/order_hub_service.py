# database/services/order_hub_service.py
"""
One place to see and track BOTH kinds of order, and one shared pool of order partners.

Sales Orders sell to *companies*, Purchase Orders buy from *suppliers*; those are two isolated tables
(see Company / PurchaseOrderSupplier). The business treats them as one list of counterparties, so every
partner exists on both sides: `sync_partners()` copies any that is missing (by name) in either
direction, and the two create() services call it, so a company added for a Sales Order appears in the
Purchase Order supplier picker and vice versa.

`board()` and `overview()` give the unified tracking view (status, dispatch age, overdue delivery,
payment state); `timeline()` is one order's history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from database.models.company import Company
from database.models.purchase_order import PurchaseOrder
from database.models.purchase_order_item import PurchaseOrderItem
from database.models.purchase_order_supplier import PurchaseOrderSupplier
from database.models.sales_order import SalesOrder
from database.models.sales_order_item import SalesOrderItem
from database.models.user import User
from database.session import get_session

KINDS = ("sales", "purchase")
STATUSES = ("draft", "in_transit", "confirmed", "cancelled")
ACTIVE = ("draft", "in_transit")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.replace(tzinfo=None) if dt is not None and getattr(dt, "tzinfo", None) else dt


def _line_value(ptype: str, weight, bags, price) -> float:
    return _f(bags) * _f(price) if str(ptype or "").lower() == "fertilizer" else _f(weight) * _f(price)


class OrderHubService:

    # ------------------------------------------------------------------ shared partners
    @staticmethod
    def sync_partners() -> int:
        """Make every active company a supplier and every active supplier a company. Idempotent."""
        created = 0
        with get_session() as s:
            companies = s.query(Company).filter(Company.is_active == True).all()            # noqa: E712
            suppliers = s.query(PurchaseOrderSupplier).filter(PurchaseOrderSupplier.is_active == True).all()  # noqa: E712
            company_names = {r[0].strip().lower() for r in s.query(Company.name).all()}
            supplier_names = {r[0].strip().lower() for r in s.query(PurchaseOrderSupplier.name).all()}
            for c in companies:
                if c.name.strip().lower() not in supplier_names:
                    s.add(PurchaseOrderSupplier(name=c.name, address=c.address, phone=c.phone,
                                                contact_person=c.contact_person, notes=c.notes,
                                                created_by_user_id=c.created_by_user_id))
                    supplier_names.add(c.name.strip().lower())
                    created += 1
            for p in suppliers:
                if p.name.strip().lower() not in company_names:
                    s.add(Company(name=p.name, address=p.address, phone=p.phone,
                                  contact_person=p.contact_person, notes=p.notes,
                                  created_by_user_id=p.created_by_user_id))
                    company_names.add(p.name.strip().lower())
                    created += 1
            s.commit()
        return created

    # ------------------------------------------------------------------ tracking board
    @staticmethod
    def board(kind: str = "", status: str = "", search: str = "", limit: int = 500) -> List[Dict[str, Any]]:
        """
        Every order of both kinds, newest first. kind: "" | "sales" | "purchase".
        status: "" (all) | "active" (draft + in transit) | one of STATUSES.
        """
        from database.services.order_payment_service import OrderPaymentService
        now = datetime.now()
        rows: List[Dict[str, Any]] = []
        term = (search or "").strip().lower()

        with get_session() as s:
            specs = []
            if kind in ("", "sales"):
                specs.append(("sales", SalesOrder, SalesOrderItem, Company, "buyer_company_id",
                              "sales_order_id", "sales_order"))
            if kind in ("", "purchase"):
                specs.append(("purchase", PurchaseOrder, PurchaseOrderItem, PurchaseOrderSupplier, "supplier_id",
                              "purchase_order_id", "purchase_order"))
            for k, model, item_model, partner_model, partner_col, item_fk, pay_type in specs:
                q = s.query(model)
                if status == "active":
                    q = q.filter(model.status.in_(ACTIVE))
                elif status in STATUSES:
                    q = q.filter(model.status == status)
                orders = q.order_by(model.created_at.desc()).limit(limit).all()
                if not orders:
                    continue
                partners = {int(p.id): p.name for p in s.query(partner_model).filter(
                    partner_model.id.in_({int(getattr(o, partner_col)) for o in orders})).all()}
                items: Dict[int, List[Any]] = {}
                for it in s.query(item_model).filter(getattr(item_model, item_fk).in_([o.id for o in orders])).all():
                    items.setdefault(int(getattr(it, item_fk)), []).append(it)
                for o in orders:
                    partner = partners.get(int(getattr(o, partner_col)), "?")
                    if term and term not in partner.lower() and term not in (o.order_no or "").lower():
                        continue
                    if o.final_total_amount is not None:
                        value, estimated = _f(o.final_total_amount), False
                    else:
                        value, estimated = 0.0, True
                        for it in items.get(int(o.id), []):
                            if k == "sales":
                                value += _line_value(it.product_type_snapshot, it.dispatched_weight_kg,
                                                     it.dispatched_qty_bags, it.my_price)
                            else:
                                value += _line_value(it.product_type_snapshot, it.ordered_weight_kg,
                                                     it.ordered_qty_bags, it.expected_price)
                    expected = _naive(o.expected_delivery_date)
                    dispatched = _naive(o.dispatched_at)
                    in_transit = o.status == "in_transit"
                    pay = OrderPaymentService.get_balance(pay_type, o.id) if o.status == "confirmed" else None
                    rows.append({
                        "kind": k, "id": int(o.id), "order_no": o.order_no, "partner": partner,
                        "status": o.status, "created_at": _naive(o.created_at), "dispatched_at": dispatched,
                        "confirmed_at": _naive(o.confirmed_at), "expected": expected,
                        "days_out": max((now - dispatched).days, 0) if in_transit and dispatched else None,
                        "overdue": bool(in_transit and expected and expected < now),
                        "value": value, "estimated": estimated,
                        "payment_status": pay["status"] if pay else "",
                        "balance": pay["balance"] if pay else None,
                    })
        rows.sort(key=lambda r: r["created_at"] or datetime.min, reverse=True)
        return rows[:limit]

    @staticmethod
    def overview() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k in KINDS:
            rows = OrderHubService.board(kind=k, limit=5000)
            out[k] = {
                "draft": sum(1 for r in rows if r["status"] == "draft"),
                "in_transit": sum(1 for r in rows if r["status"] == "in_transit"),
                "confirmed": sum(1 for r in rows if r["status"] == "confirmed"),
                "cancelled": sum(1 for r in rows if r["status"] == "cancelled"),
                "overdue": sum(1 for r in rows if r["overdue"]),
                "in_transit_value": sum(r["value"] for r in rows if r["status"] == "in_transit"),
                "outstanding": sum(_f(r["balance"]) for r in rows if r["balance"] is not None),
            }
        return out

    # ------------------------------------------------------------------ one order's history
    @staticmethod
    def timeline(kind: str, order_id: int) -> List[Dict[str, Any]]:
        model = SalesOrder if kind == "sales" else PurchaseOrder
        with get_session() as s:
            o = s.get(model, int(order_id))
            if o is None:
                raise ValueError("Order not found.")
            users = {int(u.id): u.username for u in s.query(User).filter(User.id.in_(
                {i for i in (o.created_by_user_id, o.dispatched_by_user_id, o.confirmed_by_user_id,
                             o.cancelled_by_user_id) if i})).all()}
            ev = [("Created (draft)", o.created_at, o.created_by_user_id),
                  ("Dispatched - on the road" if kind == "sales" else "Marked in transit", o.dispatched_at,
                   o.dispatched_by_user_id),
                  ("Confirmed - delivery settled", o.confirmed_at, o.confirmed_by_user_id),
                  ("Cancelled", o.cancelled_at, o.cancelled_by_user_id)]
            return [{"event": name, "at": _naive(at), "by": users.get(int(uid or 0), "")}
                    for name, at, uid in ev if at is not None]
