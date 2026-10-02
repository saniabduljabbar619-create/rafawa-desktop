# database/services/order_sheet_service.py
"""
One normalised view over Sales Orders and Purchase Orders, for the order sheet screen.

The two order services keep their own vocabulary (a sales order is *dispatched* and the company
*confirms*; a purchase order is *marked in transit* and we *receive*). The sheet screen works with one
vocabulary instead, and this module translates in both directions - the business rules (stock moves,
validation, totals) stay in SalesOrderService / PurchaseOrderService; nothing is re-implemented here.

    kind   "sales" | "purchase"
    line   {id, product_id, name, sku, type, kg, bags, price,
            rcv_qty, rcv_price, rej_qty, acc_qty, final, qty_unit}
           qty is kg for crops and bags for fertilizer (the unit the other side verifies in)

Permissions (enforced here, not only by hiding buttons): creating/editing a draft, dispatching, reverting
and cancelling are admin-only; entering the delivery result, confirming and taking payments are open to
admin and cashier.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.models.product import Product
from database.services.audit_service import AuditService
from database.services.order_hub_service import OrderHubService
from database.services.order_payment_service import OrderPaymentService
from database.services.purchase_order_service import PurchaseOrderService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.services.company_service import CompanyService
from database.services.sales_order_service import SalesOrderService
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_admin, require_role
from shared.auth.session import require_user_id

KINDS = ("sales", "purchase")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _check(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("kind must be 'sales' or 'purchase'.")
    return kind


def _is_fert(t: str) -> bool:
    return str(t or "").lower() in ("fertilizer", "fertilizers")


def _pay_type(kind: str) -> str:
    return "sales_order" if kind == "sales" else "purchase_order"


def _svc(kind: str):
    return SalesOrderService if kind == "sales" else PurchaseOrderService


def _id_key(kind: str) -> str:
    return "sales_order_id" if kind == "sales" else "purchase_order_id"


def _norm_line(kind: str, it: Dict[str, Any]) -> Dict[str, Any]:
    fert = _is_fert(it["product_type_snapshot"])
    if kind == "sales":
        kg, bags, price = it["dispatched_weight_kg"], it["dispatched_qty_bags"], it["my_price"]
        rcv_kg, rcv_bags, rcv_price = it["company_weight_kg"], it["company_qty_bags"], it["company_price"]
    else:
        kg, bags, price = it["ordered_weight_kg"], it["ordered_qty_bags"], it["expected_price"]
        rcv_kg, rcv_bags, rcv_price = it["received_weight_kg"], it["received_qty_bags"], it["received_price"]
    rcv = rcv_bags if fert else rcv_kg
    acc = it["accepted_qty_bags"] if fert else it["accepted_weight_kg"]
    rej = it["rejected_qty_bags"] if fert else it["rejected_weight_kg"]
    return {"id": int(it["item_id"]), "product_id": int(it["product_id"]), "name": it["product_name"],
            "type": "fertilizer" if fert else "crops", "kg": 0.0 if fert else _f(kg), "bags": _f(bags),
            "price": _f(price), "rcv_qty": rcv, "rcv_price": rcv_price, "rej_qty": rej, "acc_qty": acc,
            "final": it["final_line_amount"], "qty_unit": "bags" if fert else "kg"}


def estimate(kind: str, lines: List[Dict[str, Any]]) -> float:
    """What the order is worth as written (price x kg for crops, x bags for fertilizer)."""
    return round(sum(_f(l["price"]) * (_f(l["bags"]) if l["type"] == "fertilizer" else _f(l["kg"])) for l in lines), 2)


def _to_item(kind: str, line: Dict[str, Any]) -> Dict[str, Any]:
    """A normalised line -> the underlying service's item payload."""
    fert = line["type"] == "fertilizer"
    if kind == "sales":
        return {"product_id": line["product_id"], "dispatched_weight_kg": 0 if fert else line["kg"],
                "dispatched_qty_bags": line["bags"], "my_price": line["price"]}
    return {"product_id": line["product_id"], "ordered_weight_kg": 0 if fert else line["kg"],
            "ordered_qty_bags": line["bags"], "expected_price": line["price"]}


class OrderSheetService:

    # ------------------------------------------------------------------ pickers
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def products(kind: str) -> List[Dict[str, Any]]:
        """Products to put on an order, with the usual price for that side as a default."""
        _check(kind)
        with get_session() as s:
            rows = s.query(Product).filter(Product.is_active == True).order_by(Product.name).all()  # noqa: E712
            return [{"id": int(p.id), "name": p.name, "sku": p.sku,
                     "type": "fertilizer" if _is_fert(p.product_type) else "crops",
                     "price": _f(p.default_sale_rate if kind == "sales" else p.default_cost_rate)} for p in rows]

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def partner(kind: str, partner_id: int) -> Optional[Dict[str, Any]]:
        return (CompanyService if _check(kind) == "sales" else PurchaseOrderSupplierService).get(int(partner_id))

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def partners(kind: str, search: str = "") -> List[Dict[str, Any]]:
        """Every active partner with their order counts and what is outstanding."""
        _check(kind)
        OrderHubService.sync_partners()
        found = (CompanyService if kind == "sales" else PurchaseOrderSupplierService).search(search, limit=1000)
        rows = OrderHubService.board(kind=kind, limit=5000)
        out = []
        for p in found:
            mine = [r for r in rows if r["partner"] == p["name"]]
            out.append({**p, "orders": len(mine), "active": sum(1 for r in mine if r["status"] in ("draft", "in_transit")),
                        "outstanding": round(sum(_f(r["balance"]) for r in mine if r["balance"] is not None), 2),
                        "last": max((r["created_at"] for r in mine if r["created_at"]), default=None)})
        return sorted(out, key=lambda r: (-r["outstanding"], r["name"].lower()))

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def orders_of(kind: str, partner_id: int) -> List[Dict[str, Any]]:
        """The partner's orders, newest first, normalised."""
        _check(kind)
        listed = (SalesOrderService.list_for_company(partner_id) if kind == "sales"
                  else PurchaseOrderService.list_for_supplier(partner_id))
        out = []
        for o in listed:
            out.append({"id": int(o[_id_key(kind)]), "order_no": o["order_no"], "status": o["status"],
                        "created_at": o["created_at"], "dispatched_at": o["dispatched_at"],
                        "confirmed_at": o["confirmed_at"], "expected": o["expected_delivery_date"],
                        "final": o["final_total_amount"], "payment_status": o["payment_status"],
                        "balance": o["payment_balance"]})
        return out

    # ------------------------------------------------------------------ read one order
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def detail(kind: str, order_id: int) -> Dict[str, Any]:
        _check(kind)
        d = _svc(kind).get_order_detail(int(order_id))
        partner_id = d["buyer_company_id"] if kind == "sales" else d["supplier_id"]
        p = OrderSheetService.partner(kind, partner_id) or {}
        lines = [_norm_line(kind, it) for it in d["items"]]
        pay = OrderPaymentService.get_balance(_pay_type(kind), int(order_id))
        payments = OrderPaymentService.list_payments(_pay_type(kind), int(order_id))
        live = [x for x in payments if x["status"] != "voided"]
        total = d["final_total_amount"] if d["final_total_amount"] is not None else estimate(kind, lines)
        paid = round(sum(x["amount"] for x in live), 2)
        return {
            "kind": kind, "id": int(order_id), "order_no": d["order_no"], "status": d["status"],
            "partner_id": int(partner_id), "partner_name": p.get("name", "?"), "partner_phone": p.get("phone", ""),
            "partner_address": p.get("address", ""), "created_at": d["created_at"], "dispatched_at": d["dispatched_at"],
            "confirmed_at": d["confirmed_at"], "expected": d["expected_delivery_date"], "driver": d["driver"],
            "notes": d["notes"] or "", "lines": lines, "estimate": estimate(kind, lines),
            "final": d["final_total_amount"], "total": total, "estimated": d["final_total_amount"] is None,
            "paid": paid, "balance": (round(max(total - paid, 0), 2) if d["final_total_amount"] is not None else None),
            "pay_status": pay["status"], "payments": payments,
            "timeline": OrderHubService.timeline(kind, int(order_id)),
        }

    # ------------------------------------------------------------------ draft
    @staticmethod
    @require_admin
    def create(kind: str, partner_id: int, lines: List[Dict[str, Any]], notes: str = "", expected=None) -> int:
        _check(kind)
        uid = int(require_user_id())
        items = [_to_item(kind, l) for l in lines]
        oid = (SalesOrderService.create_draft(int(partner_id), items, uid, notes, expected) if kind == "sales"
               else PurchaseOrderService.create_draft(int(partner_id), items, uid, notes, expected))
        with get_session() as s:
            AuditService.record(f"{kind}_order.create", f"{kind}_order", oid, f"Draft started with {len(items)} line(s)",
                                session=s)
            s.commit()
        return int(oid)

    @staticmethod
    @require_admin
    def save_draft(kind: str, order_id: int, lines: List[Dict[str, Any]], notes: str = "", expected=None) -> None:
        """Replace a draft's lines / notes / expected date (the underlying service refuses anything but a draft)."""
        _check(kind)
        d = _svc(kind).get_order_detail(int(order_id))
        items = [_to_item(kind, l) for l in lines]
        if kind == "sales":
            SalesOrderService.update_draft(int(order_id), d["buyer_company_id"], items, notes, expected)
        else:
            PurchaseOrderService.update_draft(int(order_id), d["supplier_id"], items, notes, expected)

    @staticmethod
    @require_admin
    def dispatch(kind: str, order_id: int, driver_id: Optional[int] = None) -> None:
        """Sales: goods leave the warehouse (stock out). Purchase: goods are on the road (no stock yet)."""
        _check(kind)
        uid = int(require_user_id())
        if kind == "sales":
            SalesOrderService.dispatch(int(order_id), uid, driver_id)
        else:
            PurchaseOrderService.mark_in_transit(int(order_id), uid, driver_id)
        with get_session() as s:
            AuditService.record(f"{kind}_order.dispatch", f"{kind}_order", int(order_id), "Order on the road", session=s)
            s.commit()

    @staticmethod
    @require_admin
    def revert(kind: str, order_id: int) -> None:
        _check(kind)
        _svc(kind).revert_to_draft(int(order_id), int(require_user_id()))
        with get_session() as s:
            AuditService.record(f"{kind}_order.revert", f"{kind}_order", int(order_id), "Reverted to draft", session=s)
            s.commit()

    @staticmethod
    @require_admin
    def cancel(kind: str, order_id: int, reason: str = "") -> None:
        _check(kind)
        _svc(kind).cancel(int(order_id), int(require_user_id()), reason)
        with get_session() as s:
            AuditService.record(f"{kind}_order.cancel", f"{kind}_order", int(order_id),
                                "Order cancelled" + (f": {reason}" if reason else ""), session=s)
            s.commit()

    # ------------------------------------------------------------------ delivery result
    @staticmethod
    def _confs(kind: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """[{id, type, rcv_qty, rej_qty, rcv_price}] -> the underlying services' confirmation payloads."""
        out = []
        for r in results:
            fert = r["type"] == "fertilizer"
            unit = "qty_bags" if fert else "weight_kg"
            word = "company" if kind == "sales" else "received"
            conf: Dict[str, Any] = {"item_id": int(r["id"])}
            if r.get("rcv_qty") is not None:
                rcv = _f(r["rcv_qty"])
                rej = _f(r.get("rej_qty"))
                conf[f"{word}_{unit}"] = rcv
                conf[f"accepted_{unit}"] = round(rcv - rej, 4)
                conf[f"rejected_{unit}"] = rej
            if r.get("rcv_price") is not None:
                conf[f"{word}_price"] = _f(r["rcv_price"])
            out.append(conf)
        return out

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def save_result(kind: str, order_id: int, results: List[Dict[str, Any]]) -> None:
        """Keep what has been verified so far without finalising (no stock, no status change)."""
        _check(kind)
        _svc(kind).save_confirmation_progress(int(order_id), OrderSheetService._confs(kind, results))

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def confirm(kind: str, order_id: int, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Finalise: every line must have its received quantity, rejected quantity and price."""
        _check(kind)
        res = _svc(kind).confirm(int(order_id), OrderSheetService._confs(kind, results), int(require_user_id()))
        with get_session() as s:
            AuditService.record(f"{kind}_order.confirm", f"{kind}_order", int(order_id),
                                f"Confirmed, total {res['final_total_amount']:,.2f}", session=s)
            s.commit()
        return res

    # ------------------------------------------------------------------ money
    @staticmethod
    @require_role(*INTAKE_ROLES)
    def pay(kind: str, order_id: int, amount: float, method: str = "cash", note: str = "") -> int:
        """
        Money against the order. Before the order is confirmed there is no final price to cap it against,
        so it is kept as an advance and reconciles automatically at confirmation.
        """
        _check(kind)
        d = _svc(kind).get_order_detail(int(order_id))
        if d["status"] == "cancelled":
            raise ValueError("This order was cancelled.")
        pid = OrderPaymentService.record_payment(_pay_type(kind), int(order_id), _f(amount), int(require_user_id()),
                                                 method=method, notes=note, is_advance=d["status"] != "confirmed")
        with get_session() as s:
            AuditService.record(f"{kind}_order.payment", f"{kind}_order", int(order_id),
                                f"Payment {_f(amount):,.2f} ({method})", session=s)
            s.commit()
        return pid

    @staticmethod
    @require_admin
    def void_payment(payment_id: int, reason: str) -> None:
        OrderPaymentService.void_payment(int(payment_id), int(require_user_id()), reason)
