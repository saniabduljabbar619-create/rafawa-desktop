# database/services/quick_purchase_order_service.py
"""
Quick Purchase orders - a shortfall we are sourcing, with a status and supplier routing.

    draft  --send-->  pending  --receive (all lines)-->  completed
      |                  |  \--route--> same order, another supplier for the lines the first one lacks
      |                  \-- sell_available -> cancelled, or completed if part was already received
      \-- cancel / delete (never touches stock: nothing is bought before the goods are received)

The Purchase (stock in, supplier payable) is created ONLY on receive, one per supplier, through the
existing QuickPurchaseService.buy_shortfall - this module owns no money or stock logic of its own.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from database.models.party import Party
from database.models.product import Product
from database.models.quick_purchase_order import (
    QuickPurchaseLine, QuickPurchaseOrder, QuickPurchaseRoute,
)
from database.services.audit_service import AuditService
from database.services.quick_purchase_service import QuickPurchaseService
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_role
from shared.auth.session import get_session as get_login_session

STATUSES = ("draft", "pending", "completed", "cancelled")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _uid() -> int:
    return int(getattr(get_login_session(), "user_id", 0) or 0)


def _is_fert(ptype) -> bool:
    return str(ptype or "").lower() in ("fertilizer", "fertilizers")


class QuickPurchaseOrderService:

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _load(s, order_id: int, lock: bool = False) -> QuickPurchaseOrder:
        q = s.query(QuickPurchaseOrder).filter(QuickPurchaseOrder.id == int(order_id))
        if lock:
            q = q.with_for_update()
        o = q.first()
        if o is None:
            raise ValueError("Quick Purchase not found.")
        return o

    @staticmethod
    def _need_status(o: QuickPurchaseOrder, *allowed: str) -> None:
        if o.status not in allowed:
            raise ValueError(f"This Quick Purchase is {o.status} - that is only possible when it is "
                             f"{' or '.join(allowed)}.")

    @staticmethod
    def _check_supplier(s, supplier_id) -> int:
        p = s.get(Party, int(supplier_id or 0)) if supplier_id else None
        if p is None or not p.is_active:
            raise ValueError("Pick a supplier.")
        return int(p.id)

    @staticmethod
    def _make_line(s, i: int, raw: Dict[str, Any], *, require_supplier: bool) -> QuickPurchaseLine:
        prod = s.get(Product, int(raw.get("product_id") or 0))
        if prod is None:
            raise ValueError("Product not found.")
        fert = _is_fert(prod.product_type)
        if fert:
            bags, kg = int(raw.get("qty_bags") or 0), 0.0
            if bags <= 0:
                raise ValueError(f"Enter how many bags of {prod.name}.")
        else:
            kg = _f(raw.get("total_weight_kg"))
            if kg <= 0:
                raise ValueError(f"Enter how many kg of {prod.name}.")
            bags = max(int(raw.get("qty_bags") or 0), 1)
        cost = _f(raw.get("unit_cost"))
        if cost < 0:
            raise ValueError("Cost cannot be negative.")
        sid = raw.get("supplier_id")
        sup = QuickPurchaseOrderService._check_supplier(s, sid) if (sid or require_supplier) else None
        return QuickPurchaseLine(sort_order=i, product_id=int(prod.id), qty_bags=bags, total_weight_kg=kg,
                                 unit_cost=cost, supplier_id=sup, state="open")

    @staticmethod
    def _open_lines(o: QuickPurchaseOrder) -> List[QuickPurchaseLine]:
        return [ln for ln in o.lines if ln.state == "open"]

    # ------------------------------------------------------------------ writing
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def create(lines: List[Dict[str, Any]], customer_name: str = "", notes: str = "",
               send: bool = False) -> int:
        """Save a new Quick Purchase as a draft (or straight to pending with send=True)."""
        if not lines:
            raise ValueError("Add at least one product.")
        with get_session() as s:
            o = QuickPurchaseOrder(status="draft", customer_name=(customer_name or "").strip() or None,
                                   notes=(notes or "").strip() or None, created_by_user_id=_uid())
            s.add(o)
            s.flush()
            o.order_no = f"QP-{int(o.id):05d}"
            for i, raw in enumerate(lines):
                o.lines.append(QuickPurchaseOrderService._make_line(s, i, raw, require_supplier=False))
            AuditService.record("qp.create", "quick_purchase", int(o.id), f"{o.order_no} saved as draft",
                                session=s)
            oid = int(o.id)
            s.commit()
        if send:
            QuickPurchaseOrderService.send(oid)
        return oid

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def update_draft(order_id: int, lines: List[Dict[str, Any]], customer_name: str = "", notes: str = "") -> None:
        if not lines:
            raise ValueError("Add at least one product.")
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft")
            o.customer_name = (customer_name or "").strip() or None
            o.notes = (notes or "").strip() or None
            o.lines.clear()
            s.flush()
            for i, raw in enumerate(lines):
                o.lines.append(QuickPurchaseOrderService._make_line(s, i, raw, require_supplier=False))
            AuditService.record("qp.edit", "quick_purchase", int(o.id), f"{o.order_no} draft edited", session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def send(order_id: int) -> None:
        """draft -> pending: every line needs a supplier and a cost; stock is not touched."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft")
            for ln in o.lines:
                if not ln.supplier_id:
                    raise ValueError(f"Pick a supplier for {ln.product.name} before sending.")
                if _f(ln.unit_cost) <= 0:
                    raise ValueError(f"Enter the dealer cost for {ln.product.name} before sending.")
                s.add(QuickPurchaseRoute(line_id=ln.id, supplier_id=ln.supplier_id, outcome="asked",
                                         by_user_id=_uid()))
            o.status = "pending"
            o.sent_at = datetime.now()
            AuditService.record("qp.send", "quick_purchase", int(o.id), f"{o.order_no} sent - now pending",
                                session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def route(order_id: int, line_ids: List[int], new_supplier_id: int, note: str = "",
              unit_cost: Optional[float] = None) -> None:
        """The current supplier does not have these lines: ask another supplier instead."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "pending")
            sid = QuickPurchaseOrderService._check_supplier(s, new_supplier_id)
            wanted = {int(i) for i in line_ids or []}
            lines = [ln for ln in QuickPurchaseOrderService._open_lines(o) if int(ln.id) in wanted]
            if not lines:
                raise ValueError("Pick at least one line that is still waiting for goods.")
            if unit_cost is not None and _f(unit_cost) <= 0:
                raise ValueError("Enter the dealer cost.")
            for ln in lines:
                if int(ln.supplier_id or 0) == sid:
                    raise ValueError(f"{ln.product.name} is already asked from that supplier - pick another.")
                for r in ln.routes:
                    if r.outcome == "asked":
                        r.outcome = "unavailable"
                        r.note = (note or "").strip()[:255] or r.note
                ln.supplier_id = sid
                if unit_cost is not None:
                    ln.unit_cost = _f(unit_cost)
                s.add(QuickPurchaseRoute(line_id=ln.id, supplier_id=sid, outcome="asked", by_user_id=_uid()))
            AuditService.record("qp.route", "quick_purchase", int(o.id),
                                f"{o.order_no}: {len(lines)} line(s) routed to another supplier", session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def receive(order_id: int, line_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        The goods arrived: buy them now (one real Purchase per supplier -> stock in, payable owed).
        line_ids=None receives every line still waiting. The order completes once none are left.
        """
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "pending")
            wanted = {int(i) for i in line_ids} if line_ids else None
            groups: Dict[int, List[Dict[str, Any]]] = {}
            for ln in QuickPurchaseOrderService._open_lines(o):
                if wanted is not None and int(ln.id) not in wanted:
                    continue
                groups.setdefault(int(ln.supplier_id), []).append({
                    "_line_id": int(ln.id), "product_id": int(ln.product_id),
                    "product_type": ln.product.product_type, "qty_bags": int(ln.qty_bags),
                    "total_weight_kg": _f(ln.total_weight_kg), "unit_cost": _f(ln.unit_cost)})
            order_no = o.order_no
        if not groups:
            raise ValueError("Pick at least one line that is still waiting for goods.")

        bought, failed = [], []
        for sup_id, short in groups.items():
            res = QuickPurchaseService.buy_shortfall(
                sup_id, [{k: v for k, v in x.items() if k != "_line_id"} for x in short],
                _uid(), f"Quick Purchase {order_no}")
            (bought if res.get("ok") else failed).append((sup_id, short, res))

        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            for sup_id, short, res in bought:
                for x in short:
                    ln = s.get(QuickPurchaseLine, x["_line_id"])
                    ln.state, ln.purchase_id = "received", int(res["purchase_id"])
                    for r in ln.routes:
                        if r.outcome == "asked" and int(r.supplier_id) == sup_id:
                            r.outcome = "received"
            s.flush()
            s.refresh(o)
            if not QuickPurchaseOrderService._open_lines(o):
                o.status, o.completed_at = "completed", datetime.now()
            AuditService.record("qp.receive", "quick_purchase", int(o.id),
                                f"{o.order_no}: {sum(len(x[1]) for x in bought)} line(s) received"
                                f"{', ' + str(sum(len(x[1]) for x in failed)) + ' failed' if failed else ''}"
                                f" - {o.status}", session=s)
            status = o.status
            s.commit()
        return {"status": status, "purchases": [int(r["purchase_id"]) for _, _, r in bought],
                "errors": [str(r.get("error")) for _, _, r in failed]}

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def cancel(order_id: int, reason: str = "") -> None:
        """Withdraw it. Only possible while nothing was received (otherwise use sell_available)."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft", "pending")
            if any(ln.state == "received" for ln in o.lines):
                raise ValueError("Part of this was already received - use 'Sell what's in stock' to close it.")
            QuickPurchaseOrderService._close(s, o, "cancelled", (reason or "").strip() or "Cancelled")
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def sell_available(order_id: int) -> Dict[str, Any]:
        """
        The supplier(s) cannot supply the rest: give up the unsourced lines and carry on with what we
        have in stock. Returns what is in stock now for those products so the cashier can sell it.
        """
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft", "pending")
            open_lines = QuickPurchaseOrderService._open_lines(o)
            items = [{"product_id": int(ln.product_id), "product_type": ln.product.product_type,
                      "qty_bags": int(ln.qty_bags), "total_weight_kg": _f(ln.total_weight_kg)}
                     for ln in open_lines]
            received = any(ln.state == "received" for ln in o.lines)
            for ln in open_lines:
                ln.state = "dropped"
                for r in ln.routes:
                    if r.outcome == "asked":
                        r.outcome = "unavailable"
            QuickPurchaseOrderService._close(
                s, o, "completed" if received else "cancelled", "Closed - selling what is in stock")
            s.commit()
        stock = QuickPurchaseService.check_availability(items) if items else {"human": "", "available": [],
                                                                               "short": []}
        return {"in_stock": stock["human"], "available": stock["available"], "short": stock["short"]}

    @staticmethod
    def _close(s, o: QuickPurchaseOrder, status: str, reason: str) -> None:
        o.status, o.close_reason = status, reason[:255]
        if status == "cancelled":
            o.cancelled_at = datetime.now()
        else:
            o.completed_at = datetime.now()
        AuditService.record("qp.close", "quick_purchase", int(o.id), f"{o.order_no} {status}: {reason}", session=s)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def delete(order_id: int) -> None:
        """Remove it entirely - only if nothing was ever bought for it."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            if o.status == "completed" or any(ln.state == "received" for ln in o.lines):
                raise ValueError("Goods were already received for this Quick Purchase - it cannot be deleted.")
            no = o.order_no
            s.delete(o)
            AuditService.record("qp.delete", "quick_purchase", int(order_id), f"{no} deleted", session=s)
            s.commit()

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _dict(s, o: QuickPurchaseOrder) -> Dict[str, Any]:
        lines = []
        for ln in o.lines:
            fert = _is_fert(ln.product.product_type)
            qty = int(ln.qty_bags) if fert else _f(ln.total_weight_kg)
            lines.append({
                "id": int(ln.id), "product_id": int(ln.product_id), "product": ln.product.name,
                "product_type": ln.product.product_type, "qty_bags": int(ln.qty_bags),
                "total_weight_kg": _f(ln.total_weight_kg), "qty": qty, "unit": "bag" if fert else "kg",
                "unit_cost": _f(ln.unit_cost), "amount": round(qty * _f(ln.unit_cost), 2),
                "supplier_id": int(ln.supplier_id) if ln.supplier_id else None,
                "supplier": ln.supplier.display_name if ln.supplier else "",
                "state": ln.state, "purchase_id": int(ln.purchase_id) if ln.purchase_id else None,
                "routes": [{"supplier": r.supplier.display_name, "outcome": r.outcome, "note": r.note or "",
                            "at": r.created_at} for r in ln.routes],
            })
        return {"id": int(o.id), "order_no": o.order_no, "status": o.status,
                "customer_name": o.customer_name or "", "notes": o.notes or "",
                "close_reason": o.close_reason or "", "created_at": o.created_at, "sent_at": o.sent_at,
                "completed_at": o.completed_at, "lines": lines,
                "total": round(sum(x["amount"] for x in lines if x["state"] != "dropped"), 2)}

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def get(order_id: int) -> Dict[str, Any]:
        with get_session() as s:
            return QuickPurchaseOrderService._dict(s, QuickPurchaseOrderService._load(s, order_id))

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_orders(status: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(QuickPurchaseOrder)
            if status:
                q = q.filter(QuickPurchaseOrder.status == status)
            return [QuickPurchaseOrderService._dict(s, o)
                    for o in q.order_by(QuickPurchaseOrder.id.desc()).limit(500).all()]
