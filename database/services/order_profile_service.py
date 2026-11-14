# database/services/order_profile_service.py
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from database.models.company import Company
from database.models.product import Product
from database.models.purchase_order import PurchaseOrder
from database.models.purchase_order_item import PurchaseOrderItem
from database.models.purchase_order_supplier import PurchaseOrderSupplier
from database.models.sales_order import SalesOrder
from database.models.sales_order_item import SalesOrderItem
from database.models.order_money import OrderMoney
from database.models.stock_movement import StockMovement
from database.services.audit_service import AuditService
from database.services.company_service import CompanyService
from database.services.order_hub_service import OrderHubService
from database.services.order_money_service import OrderMoneyService
from database.services.order_payment_service import OrderPaymentService
from database.services.purchase_order_service import PurchaseOrderService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.services.sales_order_service import SalesOrderService
from database.session import get_session
from shared.auth.permissions import INTAKE_ROLES, require_admin, require_role
from shared.auth.session import require_user_id


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class OrderProfileService:
    """
    Dedicated service for Partner Profiles (Companies & Suppliers) in the Orders domain.

    Supports:
      1. Resolving partner identities across Sales (Company) & Purchase (Supplier) pools.
      2. Tracking unapplied advance balances (IN for Sales Orders, OUT for Purchase Orders).
      3. Creating new single-row order activities (Sale Order or Purchase Order) with Product,
         Quality, Bags, Weight (kg), and Price.
      4. Single-row tracking and finalization of orders (Company Weight, Company Price, Accepted Qty).
      5. Explicitly applying recorded advances to orders during or after finalization.
    """

    @staticmethod
    def resolve_partner(partner_id: int, kind: str = "sales") -> Dict[str, Any]:
        """
        Resolves a partner's profile including both company_id and supplier_id
        (since counterparties are synced by name across both sides).
        """
        kind = "sales" if str(kind).lower() in ("sales", "company") else "purchase"
        with get_session() as s:
            name = ""
            comp_id = None
            supp_id = None
            phone = ""
            address = ""
            contact = ""

            if kind == "sales":
                comp = s.get(Company, int(partner_id))
                if comp:
                    name = comp.name
                    comp_id = int(comp.id)
                    phone = comp.phone or ""
                    address = comp.address or ""
                    contact = comp.contact_person or ""
            else:
                supp = s.get(PurchaseOrderSupplier, int(partner_id))
                if supp:
                    name = supp.name
                    supp_id = int(supp.id)
                    phone = supp.phone or ""
                    address = supp.address or ""
                    contact = supp.contact_person or ""

            if not name:
                # Fallback check other side
                if kind == "sales":
                    supp = s.get(PurchaseOrderSupplier, int(partner_id))
                    if supp:
                        name = supp.name
                        supp_id = int(supp.id)
                        phone = supp.phone or ""
                        address = supp.address or ""
                        contact = supp.contact_person or ""
                else:
                    comp = s.get(Company, int(partner_id))
                    if comp:
                        name = comp.name
                        comp_id = int(comp.id)
                        phone = comp.phone or ""
                        address = comp.address or ""
                        contact = comp.contact_person or ""

            if not name:
                raise ValueError("Company / Supplier partner not found.")

            # Look up corresponding counterpart if not yet found
            if comp_id is None:
                c = s.query(Company).filter(func.lower(Company.name) == name.strip().lower()).first()
                if c:
                    comp_id = int(c.id)
                    if not phone:
                        phone = c.phone or ""
                    if not address:
                        address = c.address or ""
                    if not contact:
                        contact = c.contact_person or ""
            if supp_id is None:
                p = s.query(PurchaseOrderSupplier).filter(func.lower(PurchaseOrderSupplier.name) == name.strip().lower()).first()
                if p:
                    supp_id = int(p.id)
                    if not phone:
                        phone = p.phone or ""
                    if not address:
                        address = p.address or ""
                    if not contact:
                        contact = p.contact_person or ""

        return {
            "name": name,
            "company_id": comp_id,
            "supplier_id": supp_id,
            "phone": phone,
            "address": address,
            "contact_person": contact,
            "primary_kind": kind,
            "primary_id": comp_id if kind == "sales" and comp_id else (supp_id or comp_id or partner_id),
        }

    @staticmethod
    def get_advance_balances(company_id: Optional[int], supplier_id: Optional[int]) -> Dict[str, float]:
        """
        Calculates available unapplied advances:
        - advance_in_available: money the company gave us for Sale Orders.
        - advance_out_available: money we gave the supplier for Purchase Orders.
        """
        with get_session() as s:
            adv_in = 0.0
            adv_out = 0.0

            if company_id:
                in_val = s.query(func.coalesce(func.sum(OrderMoney.amount), 0)).filter(
                    OrderMoney.kind == "sales",
                    OrderMoney.partner_id == company_id,
                    OrderMoney.direction == "in",
                    OrderMoney.status == "completed",
                ).scalar()
                adv_in += float(in_val or 0.0)

            if supplier_id:
                out_val = s.query(func.coalesce(func.sum(OrderMoney.amount), 0)).filter(
                    OrderMoney.kind == "purchase",
                    OrderMoney.partner_id == supplier_id,
                    OrderMoney.direction == "out",
                    OrderMoney.status == "completed",
                ).scalar()
                adv_out += float(out_val or 0.0)

        return {
            "advance_in": adv_in,
            "advance_out": adv_out,
            "total_advance": adv_in + adv_out,
        }

    @staticmethod
    def all_partners_summary(kind: str = "all", search: str = "") -> List[Dict[str, Any]]:
        """
        Retrieves all partner summaries (Companies & Suppliers) in unified batch queries:
        Includes orders count, active orders, outstanding debt/balance, Advance IN, and Advance OUT.
        """
        from collections import defaultdict
        OrderHubService.sync_partners()
        with get_session() as s:
            companies = s.query(Company).filter(Company.is_active == True).order_by(Company.name).all()  # noqa: E712
            suppliers = s.query(PurchaseOrderSupplier).filter(PurchaseOrderSupplier.is_active == True).order_by(PurchaseOrderSupplier.name).all()  # noqa: E712

            supp_by_name = {p.name.strip().lower(): p for p in suppliers}
            comp_by_name = {c.name.strip().lower(): c for c in companies}

            adv_bals = defaultdict(float)
            for m_kind, m_pid, m_dir, m_amt in s.query(
                OrderMoney.kind, OrderMoney.partner_id, OrderMoney.direction,
                func.coalesce(func.sum(OrderMoney.amount), 0)
            ).filter(
                OrderMoney.status == "completed"
            ).group_by(OrderMoney.kind, OrderMoney.partner_id, OrderMoney.direction).all():
                adv_bals[(m_kind, int(m_pid), m_dir)] = float(m_amt or 0.0)

            rows = OrderHubService.board(limit=5000)
            orders_by_partner = defaultdict(list)
            for r in rows:
                orders_by_partner[r["partner"].strip().lower()].append(r)

            result = []
            seen_names = set()

            if kind == "sales":
                partners_list = [
                    ("sales", c.id, c.name, c.contact_person, c.phone, c.address, c.id,
                     supp_by_name[c.name.strip().lower()].id if c.name.strip().lower() in supp_by_name else None)
                    for c in companies
                ]
            elif kind == "purchase":
                partners_list = [
                    ("purchase", p.id, p.name, p.contact_person, p.phone, p.address,
                     comp_by_name[p.name.strip().lower()].id if p.name.strip().lower() in comp_by_name else None,
                     p.id)
                    for p in suppliers
                ]
            else:
                partners_list = []
                for c in companies:
                    c_name_k = c.name.strip().lower()
                    seen_names.add(c_name_k)
                    supp_id = supp_by_name[c_name_k].id if c_name_k in supp_by_name else None
                    partners_list.append(("sales", c.id, c.name, c.contact_person, c.phone, c.address, c.id, supp_id))
                for p in suppliers:
                    p_name_k = p.name.strip().lower()
                    if p_name_k not in seen_names:
                        seen_names.add(p_name_k)
                        partners_list.append(("purchase", p.id, p.name, p.contact_person, p.phone, p.address, None, p.id))

            term = (search or "").strip().lower()
            for p_kind, p_id, p_name, contact, phone, address, comp_id, supp_id in partners_list:
                if term and term not in f"{p_name} {contact or ''} {phone or ''}".lower():
                    continue

                p_orders = orders_by_partner.get(p_name.strip().lower(), [])
                tot_orders = len(p_orders)
                active_orders = sum(1 for o in p_orders if o["status"] in ("draft", "in_transit"))
                outstanding = round(sum(_f(o["balance"]) for o in p_orders if o.get("balance") is not None and _f(o["balance"]) > 0.005), 2)

                adv_in = adv_bals.get(("sales", comp_id, "in"), 0.0) if comp_id else 0.0
                adv_out = adv_bals.get(("purchase", supp_id, "out"), 0.0) if supp_id else 0.0

                badge = "Company" if p_kind == "sales" and not supp_id else ("Supplier" if p_kind == "purchase" and not comp_id else "Partner")
                if comp_id and supp_id:
                    badge = "Company & Supplier"

                result.append({
                    "id": p_id,
                    "kind": p_kind,
                    "company_id": comp_id,
                    "supplier_id": supp_id,
                    "name": p_name,
                    "badge": badge,
                    "contact_person": contact or "",
                    "phone": phone or "",
                    "address": address or "",
                    "orders": tot_orders,
                    "active": active_orders,
                    "outstanding": outstanding,
                    "advance_in": adv_in,
                    "advance_out": adv_out,
                })

            result.sort(key=lambda r: (-r["outstanding"], -r["active"], r["name"].lower()))
            return result

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def list_partner_orders(company_id: Optional[int], supplier_id: Optional[int]) -> List[Dict[str, Any]]:
        """
        Returns all orders for this partner across both Sales Orders and Purchase Orders,
        newest first, flattened into a single-row structure with product, quality,
        quantities, company verified data, and payment status.
        """
        orders: List[Dict[str, Any]] = []

        with get_session() as s:
            # 1. Sales Orders
            if company_id:
                so_list = (
                    s.query(SalesOrder)
                    .filter(SalesOrder.buyer_company_id == company_id)
                    .order_by(SalesOrder.created_at.desc())
                    .all()
                )
                for so in so_list:
                    item = so.items[0] if so.items else None
                    prod_name = item.product.name if item and item.product else "-"
                    prod_type = item.product_type_snapshot if item else "crops"
                    is_fert = _is_fert(prod_type)

                    init_bags = _f(item.dispatched_qty_bags) if item else 0.0
                    init_weight = _f(item.dispatched_weight_kg) if item else 0.0
                    init_price = _f(item.my_price) if item else 0.0

                    comp_bags = _f(item.company_qty_bags) if item and item.company_qty_bags is not None else None
                    comp_weight = _f(item.company_weight_kg) if item and item.company_weight_kg is not None else None
                    comp_price = _f(item.company_price) if item and item.company_price is not None else None
                    acc_bags = _f(item.accepted_qty_bags) if item and item.accepted_qty_bags is not None else None
                    acc_weight = _f(item.accepted_weight_kg) if item and item.accepted_weight_kg is not None else None
                    rej_bags = _f(item.rejected_qty_bags) if item and item.rejected_qty_bags is not None else None
                    rej_weight = _f(item.rejected_weight_kg) if item and item.rejected_weight_kg is not None else None

                    final_amt = _f(so.final_total_amount) if so.final_total_amount is not None else None
                    quality = item.notes or "" if item else ""

                    # Payment info
                    bal_info = OrderPaymentService.get_balance("sales_order", so.id)
                    paid = bal_info.get("paid", 0.0)
                    balance = bal_info.get("balance")

                    # Estimated total if not finalized
                    est_total = init_bags * init_price if is_fert else init_weight * init_price

                    orders.append({
                        "id": so.id,
                        "order_no": so.order_no,
                        "kind": "sales",
                        "partner_id": company_id,
                        "created_at": so.created_at,
                        "expected_delivery_date": so.expected_delivery_date,
                        "dispatched_at": so.dispatched_at,
                        "confirmed_at": so.confirmed_at,
                        "status": so.status,
                        "product_id": item.product_id if item else None,
                        "product_name": prod_name,
                        "product_type": prod_type,
                        "quality": quality,
                        "initial_qty_bags": init_bags,
                        "initial_weight_kg": init_weight,
                        "initial_price": init_price,
                        "company_qty_bags": comp_bags,
                        "company_weight_kg": comp_weight,
                        "company_price": comp_price,
                        "accepted_qty_bags": acc_bags,
                        "accepted_weight_kg": acc_weight,
                        "rejected_qty_bags": rej_bags,
                        "rejected_weight_kg": rej_weight,
                        "final_total_amount": final_amt,
                        "estimated_total": est_total,
                        "paid_amount": paid,
                        "balance": balance,
                        "payment_status": bal_info.get("status", "unpaid"),
                        "notes": so.notes or "",
                    })

            # 2. Purchase Orders
            if supplier_id:
                po_list = (
                    s.query(PurchaseOrder)
                    .filter(PurchaseOrder.supplier_id == supplier_id)
                    .order_by(PurchaseOrder.created_at.desc())
                    .all()
                )
                for po in po_list:
                    item = po.items[0] if po.items else None
                    prod_name = item.product.name if item and item.product else "-"
                    prod_type = item.product_type_snapshot if item else "crops"
                    is_fert = _is_fert(prod_type)

                    init_bags = _f(item.ordered_qty_bags) if item else 0.0
                    init_weight = _f(item.ordered_weight_kg) if item else 0.0
                    init_price = _f(item.expected_price) if item else 0.0

                    comp_bags = _f(item.received_qty_bags) if item and item.received_qty_bags is not None else None
                    comp_weight = _f(item.received_weight_kg) if item and item.received_weight_kg is not None else None
                    comp_price = _f(item.received_price) if item and item.received_price is not None else None
                    acc_bags = _f(item.accepted_qty_bags) if item and item.accepted_qty_bags is not None else None
                    acc_weight = _f(item.accepted_weight_kg) if item and item.accepted_weight_kg is not None else None
                    rej_bags = _f(item.rejected_qty_bags) if item and item.rejected_qty_bags is not None else None
                    rej_weight = _f(item.rejected_weight_kg) if item and item.rejected_weight_kg is not None else None

                    final_amt = _f(po.final_total_amount) if po.final_total_amount is not None else None
                    quality = item.notes or "" if item else ""

                    # Payment info
                    bal_info = OrderPaymentService.get_balance("purchase_order", po.id)
                    paid = bal_info.get("paid", 0.0)
                    balance = bal_info.get("balance")

                    # Estimated total if not finalized
                    est_total = init_bags * init_price if is_fert else init_weight * init_price

                    orders.append({
                        "id": po.id,
                        "order_no": po.order_no,
                        "kind": "purchase",
                        "partner_id": supplier_id,
                        "created_at": po.created_at,
                        "expected_delivery_date": po.expected_delivery_date,
                        "dispatched_at": po.dispatched_at,
                        "confirmed_at": po.confirmed_at,
                        "status": po.status,
                        "product_id": item.product_id if item else None,
                        "product_name": prod_name,
                        "product_type": prod_type,
                        "quality": quality,
                        "initial_qty_bags": init_bags,
                        "initial_weight_kg": init_weight,
                        "initial_price": init_price,
                        "company_qty_bags": comp_bags,
                        "company_weight_kg": comp_weight,
                        "company_price": comp_price,
                        "accepted_qty_bags": acc_bags,
                        "accepted_weight_kg": acc_weight,
                        "rejected_qty_bags": rej_bags,
                        "rejected_weight_kg": rej_weight,
                        "final_total_amount": final_amt,
                        "estimated_total": est_total,
                        "paid_amount": paid,
                        "balance": balance,
                        "payment_status": bal_info.get("status", "unpaid"),
                        "notes": po.notes or "",
                    })

        # Sort by created_at desc
        orders.sort(key=lambda o: o["created_at"] or datetime.min, reverse=True)
        return orders

    @staticmethod
    @require_admin
    def create_activity(
        kind: str,
        partner_id: int,
        product_id: int,
        quality: str,
        bags: float,
        weight_kg: float,
        price: float,
        expected_delivery=None,
        driver_id: Optional[int] = None,
        notes: str = "",
        dispatch_now: bool = True,
    ) -> int:
        """
        Creates a new single-row order activity:
        - kind: 'sales' (Sale Order) or 'purchase' (Purchase Order)
        - Records Product, Quality, Bags, Weight (kg), Price
        - If dispatch_now=True, automatically marks dispatched/in_transit
        """
        kind = "sales" if str(kind).lower() in ("sales", "sale") else "purchase"
        uid = int(require_user_id())

        with get_session() as s:
            product = s.get(Product, int(product_id))
            if not product:
                raise ValueError("Selected product not found.")
            is_fert = _is_fert(product.product_type)

        bags = _f(bags)
        weight_kg = _f(weight_kg)
        price = _f(price)

        if is_fert:
            if bags <= 0:
                raise ValueError("Please enter the number of bags.")
            weight_kg = 0.0
        else:
            if weight_kg <= 0:
                raise ValueError("Please enter the weight in kg.")
            if bags <= 0:
                raise ValueError("Please enter the bags count.")

        if price <= 0:
            raise ValueError("Please enter a valid price.")

        item_dict = {
            "product_id": int(product_id),
            "dispatched_qty_bags" if kind == "sales" else "ordered_qty_bags": bags,
            "dispatched_weight_kg" if kind == "sales" else "ordered_weight_kg": weight_kg,
            "my_price" if kind == "sales" else "expected_price": price,
            "notes": (quality or "").strip() or None,
        }

        if kind == "sales":
            order_id = SalesOrderService.create_draft(
                buyer_company_id=int(partner_id),
                items=[item_dict],
                created_by_user_id=uid,
                notes=notes,
                expected_delivery_date=expected_delivery,
            )
            if dispatch_now:
                SalesOrderService.dispatch(order_id, uid, driver_id=driver_id)
        else:
            order_id = PurchaseOrderService.create_draft(
                supplier_id=int(partner_id),
                items=[item_dict],
                created_by_user_id=uid,
                notes=notes,
                expected_delivery_date=expected_delivery,
            )
            if dispatch_now:
                PurchaseOrderService.mark_in_transit(order_id, uid, driver_id=driver_id)

        with get_session() as s:
            AuditService.record(
                f"{kind}_order.create_activity",
                f"{kind}_order",
                order_id,
                f"Created {kind} order with {product.name} ({quality or 'standard'})",
                session=s,
            )
            s.commit()

        return int(order_id)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def finalize_order(
        kind: str,
        order_id: int,
        company_price: float,
        company_weight_kg: Optional[float] = None,
        company_qty_bags: Optional[float] = None,
        accepted_qty: Optional[float] = None,
        rejected_qty: float = 0.0,
        notes: str = "",
        use_advance: bool = False,
        advance_amount: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Finalizes an order on a single row:
        - Sets verified Company/Supplier Price, Weight/Bags, Accepted/Rejected quantities.
        - Confirms the order.
        - If use_advance=True, detects available advances for this partner and applies payment!
        """
        kind = "sales" if str(kind).lower() in ("sales", "sale") else "purchase"
        uid = int(require_user_id())

        with get_session() as s:
            if kind == "sales":
                order = s.get(SalesOrder, int(order_id))
            else:
                order = s.get(PurchaseOrder, int(order_id))

            if not order:
                raise ValueError("Order not found.")

            if order.status == "draft":
                # If still draft, dispatch first so it can be confirmed
                if kind == "sales":
                    SalesOrderService.dispatch(order.id, uid)
                else:
                    PurchaseOrderService.mark_in_transit(order.id, uid)
                s.refresh(order)

            item = order.items[0] if order.items else None
            if not item:
                raise ValueError("Order has no product items.")

            item_id = int(item.id)
            is_fert = _is_fert(item.product_type_snapshot)
            partner_id = int(order.buyer_company_id if kind == "sales" else order.supplier_id)

        company_price = _f(company_price)
        if company_price <= 0:
            raise ValueError("Please enter the confirmed company/supplier price.")

        if is_fert:
            rcv_qty = _f(company_qty_bags)
            if rcv_qty <= 0:
                raise ValueError("Please enter the received bags count.")
            unit = "bags"
        else:
            rcv_qty = _f(company_weight_kg)
            if rcv_qty <= 0:
                raise ValueError("Please enter the confirmed weight (kg).")
            unit = "kg"

        rej_qty = _f(rejected_qty)
        if accepted_qty is not None and _f(accepted_qty) > 0:
            acc_qty = _f(accepted_qty)
            rej_qty = max(0.0, rcv_qty - acc_qty)
        else:
            acc_qty = max(0.0, rcv_qty - rej_qty)

        word = "company" if kind == "sales" else "received"
        conf = {
            "item_id": item_id,
            f"{word}_price": company_price,
            f"{word}_qty_bags" if is_fert else f"{word}_weight_kg": rcv_qty,
            f"accepted_qty_bags" if is_fert else f"accepted_weight_kg": acc_qty,
            f"rejected_qty_bags" if is_fert else f"rejected_weight_kg": rej_qty,
        }

        if kind == "sales":
            res = SalesOrderService.confirm(int(order_id), [conf], uid)
        else:
            res = PurchaseOrderService.confirm(int(order_id), [conf], uid)

        final_total = _f(res.get("final_total_amount"))

        applied_payment_id = None
        if use_advance:
            # Check for available advance
            direction = "in" if kind == "sales" else "out"
            available_advances = OrderMoneyService.get_available_advances(kind, partner_id, direction=direction)
            if available_advances:
                first_adv = available_advances[0]
                adv_id = first_adv["id"]
                adv_amt = first_adv["amount"]
                to_apply = min(final_total, adv_amt)
                if advance_amount is not None and _f(advance_amount) > 0:
                    to_apply = min(to_apply, _f(advance_amount))
                applied_payment_id = OrderMoneyService.apply_to_order(adv_id, int(order_id), amount=to_apply)

        return {
            "ok": True,
            "order_id": int(order_id),
            "final_total_amount": final_total,
            "applied_payment_id": applied_payment_id,
        }

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def apply_advance(kind: str, order_id: int, money_id: int, amount: Optional[float] = None) -> int:
        """Explicitly applies a recorded advance from the profile to this finalized order."""
        kind = "sales" if str(kind).lower() in ("sales", "sale") else "purchase"
        return OrderMoneyService.apply_to_order(int(money_id), int(order_id), amount=amount)

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def record_advance(kind: str, partner_id: int, direction: str, amount: float,
                       method: str = "transfer", note: str = "", entry_date=None) -> int:
        """
        Records an advance on the partner's profile.
        Direction: 'in' (Sale Orders advance from company) or 'out' (Purchase Orders advance to supplier).
        This advance stays safely held until explicitly used.
        """
        kind = "sales" if str(kind).lower() in ("sales", "sale") else "purchase"
        direction = "in" if str(direction).lower() == "in" else "out"
        return OrderMoneyService.add_money(
            kind=kind,
            partner_id=int(partner_id),
            direction=direction,
            amount=amount,
            method=method,
            note=note,
            entry_date=entry_date or date.today(),
        )

    @staticmethod
    @require_role(*INTAKE_ROLES)
    def edit_activity(
        kind: str,
        order_id: int,
        product_id: Optional[int] = None,
        quality: Optional[str] = None,
        initial_bags: Optional[float] = None,
        initial_weight_kg: Optional[float] = None,
        initial_price: Optional[float] = None,
        expected_delivery=None,
        notes: Optional[str] = None,
        company_price: Optional[float] = None,
        company_weight_kg: Optional[float] = None,
        company_qty_bags: Optional[float] = None,
        accepted_qty: Optional[float] = None,
        rejected_qty: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Allows editing any order activity parameters:
        - Product, Quality, Initial Quantity (Bags/Kg), Initial Price
        - Expected delivery date & notes
        - Company verified price, Company Weight/Bags, Accepted/Rejected quantities
        - Recalculates line amount, order totals, and syncs StockMovement where applicable.
        """
        kind = "sales" if str(kind).lower() in ("sales", "sale") else "purchase"
        uid = int(require_user_id())

        with get_session() as s:
            if kind == "sales":
                order = s.get(SalesOrder, int(order_id))
            else:
                order = s.get(PurchaseOrder, int(order_id))

            if not order:
                raise ValueError("Order not found.")

            if not order.items:
                raise ValueError("Order has no product line to edit.")

            item = order.items[0]

            if expected_delivery is not None:
                order.expected_delivery_date = expected_delivery
            if notes is not None:
                order.notes = (notes or "").strip()

            if product_id is not None and int(product_id) > 0:
                prod = s.get(Product, int(product_id))
                if not prod:
                    raise ValueError(f"Product #{product_id} not found.")
                item.product_id = int(prod.id)
                item.product_type_snapshot = "fertilizer" if _is_fert(prod.product_type) else "crops"

            is_fert = _is_fert(item.product_type_snapshot)

            if quality is not None:
                item.notes = (quality or "").strip() or None

            # Initial / Ordered specs
            if initial_bags is not None:
                b = _f(initial_bags)
                if kind == "sales":
                    item.dispatched_qty_bags = b
                else:
                    item.ordered_qty_bags = b

            if initial_weight_kg is not None:
                w = 0.0 if is_fert else _f(initial_weight_kg)
                if kind == "sales":
                    item.dispatched_weight_kg = w
                else:
                    item.ordered_weight_kg = w

            if initial_price is not None:
                p = _f(initial_price)
                if kind == "sales":
                    item.my_price = p
                else:
                    item.expected_price = p

            # Sync StockMovement if dispatched in_transit
            if kind == "sales":
                movements = (
                    s.query(StockMovement)
                    .filter(
                        StockMovement.reference_type == "sales_order_item",
                        StockMovement.reference_id == item.id,
                    )
                    .all()
                )
                for sm in movements:
                    sm.product_id = item.product_id
                    sm.qty_bags = float(item.dispatched_qty_bags)
                    sm.total_weight_kg = 0.0 if is_fert else float(item.dispatched_weight_kg)
                    sm.unit_rate = float(item.my_price)

            # Company / Confirmed specs
            if company_price is not None:
                cp = _f(company_price)
                if kind == "sales":
                    item.company_price = cp
                else:
                    item.received_price = cp

            if company_qty_bags is not None:
                cb = _f(company_qty_bags)
                if kind == "sales":
                    item.company_qty_bags = cb
                else:
                    item.received_qty_bags = cb

            if company_weight_kg is not None:
                cw = 0.0 if is_fert else _f(company_weight_kg)
                if kind == "sales":
                    item.company_weight_kg = cw
                else:
                    item.received_weight_kg = cw

            if accepted_qty is not None:
                aq = _f(accepted_qty)
                if is_fert:
                    item.accepted_qty_bags = aq
                else:
                    item.accepted_weight_kg = aq

            if rejected_qty is not None:
                rq = _f(rejected_qty)
                if is_fert:
                    item.rejected_qty_bags = rq
                else:
                    item.rejected_weight_kg = rq

            # Recalculate totals if confirmed or if company fields are populated
            cur_cp = item.company_price if kind == "sales" else item.received_price
            cur_acc = item.accepted_qty_bags if is_fert else item.accepted_weight_kg

            if order.status == "confirmed" or (cur_cp is not None and cur_acc is not None):
                eff_p = cur_cp if (cur_cp is not None and cur_cp > 0) else (item.my_price if kind == "sales" else item.expected_price)
                eff_q = cur_acc if cur_acc is not None else (item.dispatched_qty_bags if is_fert else item.dispatched_weight_kg if kind == "sales" else item.ordered_qty_bags if is_fert else item.ordered_weight_kg)
                final_val = round(_f(eff_q) * _f(eff_p), 2)
                item.final_line_amount = final_val
                order.final_total_amount = final_val
                order.payment_due = final_val

                # If purchase order confirmed, sync stock movement for intake
                if kind == "purchase" and order.status == "confirmed":
                    po_movs = (
                        s.query(StockMovement)
                        .filter(
                            StockMovement.reference_type == "purchase_order_item",
                            StockMovement.reference_id == item.id,
                        )
                        .all()
                    )
                    for sm in po_movs:
                        sm.product_id = item.product_id
                        sm.qty_bags = float(item.accepted_qty_bags or 0.0) if is_fert else 0.0
                        sm.total_weight_kg = 0.0 if is_fert else float(item.accepted_weight_kg or 0.0)
                        sm.unit_rate = float(eff_p)

            s.commit()

            AuditService.record(
                f"{kind}_order.edit",
                f"{kind}_order",
                int(order_id),
                f"Edited {kind} order specs (Price/Qty/Bags/Kg)",
                session=s,
            )

        return {"ok": True, "order_id": int(order_id)}
