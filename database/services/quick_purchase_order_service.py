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

import json
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
               send: bool = False, buyer_party_id: Optional[int] = None,
               cart: Optional[List[Dict[str, Any]]] = None) -> int:
        """
        Save a new Quick Purchase as a draft (or straight to pending with send=True).
        cart: the checkout cart it was raised for (plain dicts) - kept so the sale can be rebuilt once the
        goods arrive. buyer_party_id: the registered customer, if the sale was for one.
        """
        if not lines:
            raise ValueError("Add at least one product.")
        with get_session() as s:
            o = QuickPurchaseOrder(status="draft", customer_name=(customer_name or "").strip() or None,
                                   notes=(notes or "").strip() or None, created_by_user_id=_uid(),
                                   buyer_party_id=int(buyer_party_id) if buyer_party_id else None,
                                   cart_json=json.dumps(cart, default=str) if cart else None)
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
                if not ln.supplier_id or (wanted is not None and int(ln.id) not in wanted):
                    continue                                  # a line with no supplier yet cannot be received
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
    @require_role(*INTAKE_ROLES)
    def edit_line(order_id: int, line_id: int, qty: Optional[float] = None, unit_cost: Optional[float] = None,
                  supplier_id: Optional[int] = None, note: str = "") -> None:
        """
        Fix up one line while the order is still open: the quantity the supplier really has, the price they
        quoted, or a different supplier (supplier_id=0 un-assigns it). Lowering the quantity keeps the rest as
        a separate unassigned line so nothing silently disappears - assign it to another supplier or drop it.
        """
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft", "pending")
            ln = next((x for x in o.lines if int(x.id) == int(line_id)), None)
            if ln is None or ln.state != "open":
                raise ValueError("Pick a line that is still waiting for goods.")
            fert = _is_fert(ln.product.product_type)
            what = []
            if unit_cost is not None:
                if _f(unit_cost) <= 0:
                    raise ValueError("Enter the dealer cost.")
                if abs(_f(unit_cost) - _f(ln.unit_cost)) > 0.004:
                    what.append(f"cost {_f(ln.unit_cost):,.2f} -> {_f(unit_cost):,.2f}")
                ln.unit_cost = _f(unit_cost)
            if qty is not None:
                q = float(int(qty)) if fert else round(_f(qty), 2)
                if q <= 0:
                    raise ValueError("Enter a quantity above zero - or drop the line.")
                cur = float(ln.qty_bags) if fert else _f(ln.total_weight_kg)
                bags = int(ln.qty_bags)
                if q < cur:
                    rem = cur - q
                    if fert:
                        keep_bags, rem_bags, keep_kg, rem_kg = int(q), int(rem), 0.0, 0.0
                    else:
                        keep_kg, rem_kg = q, round(rem, 2)
                        keep_bags = max(1, round(bags * q / cur))
                        rem_bags = max(1, bags - keep_bags)
                    s.add(QuickPurchaseLine(
                        order_id=o.id, sort_order=max(x.sort_order for x in o.lines) + 1, product_id=ln.product_id,
                        qty_bags=rem_bags, total_weight_kg=rem_kg, unit_cost=ln.unit_cost, supplier_id=None,
                        state="open"))
                    ln.qty_bags, ln.total_weight_kg = keep_bags, keep_kg
                    what.append(f"qty {cur:g} -> {q:g}, {rem:g} left unassigned")
                elif q > cur:
                    ln.qty_bags = int(q) if fert else max(bags, round(bags * q / cur))
                    ln.total_weight_kg = 0.0 if fert else q
                    what.append(f"qty {cur:g} -> {q:g}")
            if supplier_id is not None and int(supplier_id or 0) != int(ln.supplier_id or 0):
                new_sid = QuickPurchaseOrderService._check_supplier(s, supplier_id) if supplier_id else None
                for r in ln.routes:
                    if r.outcome == "asked":
                        r.outcome = "unavailable"
                        r.note = (note or "").strip()[:255] or r.note
                ln.supplier_id = new_sid
                if new_sid:
                    s.add(QuickPurchaseRoute(line_id=ln.id, supplier_id=new_sid, outcome="asked", by_user_id=_uid()))
                what.append("supplier changed" if new_sid else "supplier removed")
            if what:
                AuditService.record("qp.edit_line", "quick_purchase", int(o.id),
                                    f"{o.order_no} {ln.product.name}: " + "; ".join(what), session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def add_lines(order_id: int, lines: List[Dict[str, Any]]) -> None:
        """More products were added to the cart for the same Quick Purchase: put them on the order too."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft", "pending")
            n = max([x.sort_order for x in o.lines] or [-1]) + 1
            for i, raw in enumerate(lines):
                ln = QuickPurchaseOrderService._make_line(s, n + i, raw, require_supplier=False)
                o.lines.append(ln)
                s.flush()
                if o.status == "pending" and ln.supplier_id:
                    s.add(QuickPurchaseRoute(line_id=ln.id, supplier_id=ln.supplier_id, outcome="asked",
                                             by_user_id=_uid()))
            AuditService.record("qp.add_lines", "quick_purchase", int(o.id),
                                f"{o.order_no}: {len(lines)} line(s) added", session=s)
            s.commit()

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def mark_fulfilled(order_id: int, purchases: Dict[int, int], sale_id: Optional[int]) -> None:
        """
        The Sales Console bought the goods (one Purchase per supplier) and recorded the sale: close the order's
        lines for those suppliers as received and link the sale. Lines for any other supplier stay open.
        """
        purchases = {int(k): int(v) for k, v in purchases.items()}
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            for ln in QuickPurchaseOrderService._open_lines(o):
                pid = purchases.get(int(ln.supplier_id or 0))
                if pid:
                    ln.state, ln.purchase_id = "received", pid
                    for r in ln.routes:
                        if r.outcome == "asked" and int(r.supplier_id) == int(ln.supplier_id):
                            r.outcome = "received"
            o.sale_id = int(sale_id) if sale_id else o.sale_id
            s.flush()
            s.refresh(o)
            if not QuickPurchaseOrderService._open_lines(o):
                o.status, o.completed_at = "completed", datetime.now()
            AuditService.record("qp.fulfilled", "quick_purchase", int(o.id),
                                f"{o.order_no}: bought and sold from the Sales Console - {o.status}", session=s)
            s.commit()
        for pid in purchases.values():
            QuickPurchaseService.link_sale(pid, sale_id)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def drop_line(order_id: int, line_id: int, reason: str = "") -> None:
        """Give up one line (the supplier cannot supply it and nobody else is asked)."""
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            QuickPurchaseOrderService._need_status(o, "draft", "pending")
            ln = next((x for x in o.lines if int(x.id) == int(line_id)), None)
            if ln is None or ln.state != "open":
                raise ValueError("Pick a line that is still waiting for goods.")
            ln.state = "dropped"
            for r in ln.routes:
                if r.outcome == "asked":
                    r.outcome = "unavailable"
                    r.note = (reason or "").strip()[:255] or r.note
            AuditService.record("qp.drop_line", "quick_purchase", int(o.id),
                                f"{o.order_no}: {ln.product.name} given up", session=s)
            if not QuickPurchaseOrderService._open_lines(o):
                o.status = "completed" if any(x.state == "received" for x in o.lines) else "cancelled"
                o.completed_at = datetime.now()
                o.close_reason = "Every line settled"
            s.commit()

    # ------------------------------------------------------------------ confirm (the last checkout step)
    @staticmethod
    def _basis(s, o: QuickPurchaseOrder) -> Dict[str, Any]:
        """What confirming this order would buy, and what the customer can be sold - read-only."""
        if o.status not in ("draft", "pending", "completed") or o.sale_id:
            raise ValueError("This Quick Purchase is already finished.")
        open_lines = QuickPurchaseOrderService._open_lines(o)
        if any(not ln.supplier_id for ln in open_lines):
            raise ValueError("Assign a supplier to - or drop - the lines that have none yet.")
        if o.status == "completed" and not o.cart_json:
            raise ValueError("There is nothing left to confirm.")

        def qty(ln):
            return float(ln.qty_bags) if _is_fert(ln.product.product_type) else _f(ln.total_weight_kg)

        suppliers: Dict[int, Dict[str, Any]] = {}
        pool: Dict[int, List[float]] = {}                 # product -> [open qty, received qty, cost total]
        for ln in o.lines:
            if ln.state not in ("open", "received") or (ln.state == "received" and o.sale_id):
                continue
            row = pool.setdefault(int(ln.product_id), [0.0, 0.0, 0.0])
            row[0 if ln.state == "open" else 1] += qty(ln)
            row[2] += qty(ln) * _f(ln.unit_cost)
            if ln.state == "open":
                sp = suppliers.setdefault(int(ln.supplier_id), {"supplier_id": int(ln.supplier_id),
                                                                "name": ln.supplier.display_name, "amount": 0.0,
                                                                "line_ids": []})
                sp["amount"] = round(sp["amount"] + qty(ln) * _f(ln.unit_cost), 2)
                sp["line_ids"].append(int(ln.id))

        sale_lines: List[Dict[str, Any]] = []
        cart: Dict[int, Dict[str, Any]] = {}
        for c in (json.loads(o.cart_json) if o.cart_json else []):
            pid = int(c["product_id"])
            fert = _is_fert(c.get("product_type"))
            a = cart.setdefault(pid, {"product_id": pid, "name": c["name"], "product_type": c["product_type"],
                                      "wanted": 0.0, "bags": 0, "price": _f(c.get("unit_price"))})
            a["wanted"] += float(c["qty_bags"]) if fert else _f(c["total_weight_kg"])
            a["bags"] += int(c["qty_bags"])
        for pid, a in cart.items():
            fert = _is_fert(a["product_type"])
            have_bags, have_kg = QuickPurchaseService._on_hand(s, pid)
            have = float(have_bags) if fert else have_kg
            open_q, rec_q, cost_total = pool.get(pid, [0.0, 0.0, 0.0])
            sell = min(a["wanted"], have + open_q)
            qp_part = min(sell, open_q + rec_q)
            prod = s.get(Product, pid)
            sale_lines.append({
                **a, "have": have, "incoming": open_q, "sell_qty": round(sell, 2), "short": round(a["wanted"] - sell, 2),
                "qp_part": round(qp_part, 2), "stock_part": round(sell - qp_part, 2),
                "dealer_cost": round(cost_total / (open_q + rec_q), 4) if (open_q + rec_q) else 0.0,
                "book_cost": _f(prod.default_cost_rate) if prod else 0.0})
        return {"order_no": o.order_no, "buyer_party_id": o.buyer_party_id, "customer_name": o.customer_name or "",
                "has_sale": bool(sale_lines), "sale_lines": sale_lines,
                "suppliers": list(suppliers.values()), "open_total": round(sum(x["amount"] for x in suppliers.values()), 2)}

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def plan_confirm(order_id: int) -> Dict[str, Any]:
        with get_session() as s:
            return QuickPurchaseOrderService._basis(s, QuickPurchaseOrderService._load(s, order_id))

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def confirm(order_id: int, prices: Dict[int, float], supplier_paid: Optional[Dict[int, float]] = None,
                customer_paid: float = 0.0, supplier_method: Any = "cash", customer_method: str = "cash",
                buyer_party_id: Optional[int] = None, walkin_name: str = "",
                discounts: Optional[Dict[int, float]] = None, note: str = "") -> Dict[str, Any]:
        """
        The last checkout step: buy what is still waiting (one Purchase per supplier), sell the customer
        what can be sold at the prices given, then record what was paid to the suppliers / by the customer.
        Anything unpaid stays as a balance in the Payments center. Purchases are undone if the sale fails.
        """
        from database.services.payment_service import PaymentService
        from database.services.sales_service import SaleService
        supplier_paid = {int(k): _f(v) for k, v in (supplier_paid or {}).items()}
        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            basis = QuickPurchaseOrderService._basis(s, o)
            order_no = o.order_no
            line_info = {int(ln.id): {"product_id": int(ln.product_id), "product_type": ln.product.product_type,
                                      "qty_bags": int(ln.qty_bags), "total_weight_kg": _f(ln.total_weight_kg),
                                      "unit_cost": _f(ln.unit_cost)}
                         for ln in o.lines if ln.state == "open"}
        sale_items, total, discount_total = [], 0.0, 0.0
        for sl in basis["sale_lines"]:
            if sl["sell_qty"] <= 0:
                continue
            price = _f((prices or {}).get(sl["product_id"]))
            if price <= 0:
                raise ValueError(f"Set a selling price for {sl['name']}.")
            fert = _is_fert(sl["product_type"])
            sell = sl["sell_qty"]
            total_bags = int(sell) if fert else max(1, round(sl["bags"] * sell / sl["wanted"]))
            qp_bags = 0
            if sl["qp_part"] > 0:
                qp_bags = int(sl["qp_part"]) if fert else min(total_bags, max(1, round(total_bags * sl["qp_part"] / sell)))
            for lot, bags, cost in ((sl["qp_part"], qp_bags, sl["dealer_cost"]),
                                    (sl["stock_part"], total_bags - qp_bags, sl["book_cost"])):
                if lot <= 0.005:
                    continue
                sale_items.append({"product_id": sl["product_id"], "product_type": sl["product_type"],
                                   "qty_bags": int(lot) if fert else bags, "total_weight_kg": 0.0 if fert else lot,
                                   "unit_price": price, "unit_cost": cost})
            line_total = price * sell
            disc = max(0.0, min(_f((discounts or {}).get(sl["product_id"])), line_total))
            if disc:                                  # a line discount is a total: share it across the cost lots
                lots = [i for i in sale_items if i["product_id"] == sl["product_id"]]
                for i in lots:
                    own = i["qty_bags"] if fert else i["total_weight_kg"]
                    i["discount"] = round(disc * own / sell, 2)
            total += line_total
            discount_total += disc
        total = round(total - discount_total, 2)

        bought = []                                           # (supplier_id, line_ids, purchase result)
        for sp in basis["suppliers"]:
            res = QuickPurchaseService.buy_shortfall(
                sp["supplier_id"], [line_info[i] for i in sp["line_ids"]], _uid(), f"Quick Purchase {order_no}")
            if not res.get("ok"):
                QuickPurchaseOrderService._undo_purchases([r["purchase_id"] for _, _, r in bought])
                raise ValueError(f"Could not buy from {sp['name']}: {res.get('error')}")
            bought.append((sp["supplier_id"], sp["line_ids"], res))

        sale_id, reference = None, ""
        if sale_items:
            try:
                sale = SaleService.record({
                    "buyer_party_id": int(buyer_party_id or basis["buyer_party_id"] or 0),
                    "walkin_name": walkin_name or basis["customer_name"], "created_by_user_id": _uid(),
                    "items": sale_items, "discount": round(discount_total, 2), "total": total,
                    "note": (note + " - " if note else "") + f"Quick Purchase {order_no}"})
            except Exception:
                QuickPurchaseOrderService._undo_purchases([r["purchase_id"] for _, _, r in bought])
                raise
            sale_id, reference = int(sale.get("sale_id") or 0) or None, str(sale.get("reference") or "")

        with get_session() as s:
            o = QuickPurchaseOrderService._load(s, order_id, lock=True)
            for sup_id, line_ids, res in bought:
                for lid in line_ids:
                    ln = s.get(QuickPurchaseLine, lid)
                    ln.state, ln.purchase_id = "received", int(res["purchase_id"])
                    for r in ln.routes:
                        if r.outcome == "asked" and int(r.supplier_id) == sup_id:
                            r.outcome = "received"
            o.status, o.completed_at, o.sale_id = "completed", datetime.now(), sale_id
            AuditService.record("qp.confirm", "quick_purchase", int(o.id),
                                f"{o.order_no} confirmed: {len(bought)} purchase(s)"
                                f"{', sale ' + reference if reference else ''}", session=s)
            s.commit()
        for _, _, res in bought:
            QuickPurchaseService.link_sale(res["purchase_id"], sale_id)

        warnings, paid_out, paid_in = [], 0.0, 0.0
        for sup_id, _line_ids, res in bought:
            amt = min(supplier_paid.get(sup_id, 0.0), _f(res.get("total_amount")))
            if amt > 0.004:
                try:
                    PaymentService.record_purchase_payment(int(res["purchase_id"]), sup_id, amt, _uid(),
                                                           (supplier_method.get(sup_id) if isinstance(supplier_method, dict)
                                                            else supplier_method) or "cash",
                                                           f"Quick Purchase {order_no}")
                    paid_out += amt
                except Exception as e:
                    warnings.append(f"Supplier payment not recorded: {e}")
        if sale_id and _f(customer_paid) > 0.004:
            amt = min(_f(customer_paid), total)
            try:
                PaymentService.record_sale_payment(sale_id, int(buyer_party_id or basis["buyer_party_id"] or 0), amt,
                                                   _uid(), customer_method or "cash", f"Quick Purchase {order_no}")
                paid_in = amt
            except Exception as e:
                warnings.append(f"Customer payment not recorded: {e}")
        owed_to_suppliers = round(sum(_f(r.get("total_amount")) for _, _, r in bought) - paid_out, 2)
        return {"sale_id": sale_id, "reference": reference, "purchases": [int(r["purchase_id"]) for _, _, r in bought],
                "supplier_purchases": [{"supplier_id": sid, "purchase_id": int(r["purchase_id"]),
                                        "amount": _f(r.get("total_amount"))} for sid, _, r in bought],
                "sale_total": total, "customer_paid": paid_in, "customer_owes": round(total - paid_in, 2) if sale_id else 0.0,
                "paid_to_suppliers": paid_out, "owed_to_suppliers": owed_to_suppliers, "warnings": warnings}

    @staticmethod
    def _undo_purchases(purchase_ids: List[int]) -> None:
        for pid in purchase_ids:
            try:
                QuickPurchaseService.cancel_pending_quick_purchase(int(pid))
            except Exception:
                pass

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
                "supplier_id": int(ln.supplier_id) if ln.supplier_id else None, "assigned": bool(ln.supplier_id),
                "supplier": ln.supplier.display_name if ln.supplier else "",
                "state": ln.state, "purchase_id": int(ln.purchase_id) if ln.purchase_id else None,
                "routes": [{"supplier": r.supplier.display_name, "outcome": r.outcome, "note": r.note or "",
                            "at": r.created_at} for r in ln.routes],
            })
        return {"id": int(o.id), "order_no": o.order_no, "status": o.status,
                "customer_name": o.customer_name or "", "buyer_party_id": o.buyer_party_id,
                "cart": json.loads(o.cart_json) if o.cart_json else [], "sale_id": o.sale_id, "notes": o.notes or "",
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
