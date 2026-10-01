# database/services/purchase_order_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime

from database.session import get_session
from database.models.purchase_order import PurchaseOrder
from database.models.purchase_order_item import PurchaseOrderItem
from database.models.product import Product
from database.models.stock_movement import StockMovement


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


def _validate_and_build_item(s, order_id: int, it: Dict[str, Any]) -> "PurchaseOrderItem":
    pid = int(it.get("product_id") or 0)
    if pid <= 0:
        raise ValueError("Every line needs a product_id.")

    product = s.get(Product, pid)
    if product is None:
        raise ValueError(f"Product #{pid} not found.")
    ptype = "fertilizer" if _is_fert(product.product_type) else "crops"

    weight = _f(it.get("ordered_weight_kg"))
    bags = _f(it.get("ordered_qty_bags"))
    expected_price = _f(it.get("expected_price"))

    if ptype == "fertilizer":
        if bags <= 0:
            raise ValueError(f"{product.name}: enter a bag quantity greater than zero.")
        weight = 0.0
    else:
        # Crops needs BOTH weight and bags, matching the exact same
        # dual-tracking fix already applied on the Sales Order side
        # (see intake_crops_dialog.py's own comment on this point).
        if weight <= 0:
            raise ValueError(f"{product.name}: enter a weight (kg) greater than zero.")
        if bags <= 0:
            raise ValueError(f"{product.name}: enter a bags quantity greater than zero.")

    return PurchaseOrderItem(
        purchase_order_id=order_id,
        product_id=pid,
        product_type_snapshot=ptype,
        ordered_weight_kg=weight,
        ordered_qty_bags=bags,
        expected_price=expected_price,
        notes=(it.get("notes") or "").strip() or None,
    )


class PurchaseOrderService:
    """
    Buying crops/fertilizer from a supplier, entirely separate from the
    existing, immediate Purchase/Quick Purchase flow. Mirrors Sales
    Order's own isolation and lifecycle structure, with one confirmed,
    deliberate difference: stock only ever changes at ONE moment, not
    two:

      in_transit (mark_in_transit) - the supplier has acknowledged or is
                  sending the order. PURELY informational - no
                  StockMovement at all here. We're the receiving party;
                  we can't trust a quantity we haven't verified
                  ourselves, unlike Sales Order's dispatch (where WE are
                  the one sending, so we know for certain).

      confirm   - we've physically received, weighed/counted, and
                  verified what actually arrived. The ONLY point stock
                  genuinely changes: accepted quantity enters Available
                  Stock via "purchase_in". Rejected quantity never
                  enters stock at all - nothing to reverse, since
                  nothing was ever added for it in the first place
                  (unlike Sales Order's rejected, which reverses a
                  movement that already happened at dispatch).

    Draft commits nothing at all - no StockMovement, no party effect -
    so it can be freely edited or cancelled without needing to reverse
    anything.
    """

    # ------------------------------------------------------------------
    # CREATE (draft) - commits nothing to stock
    # ------------------------------------------------------------------
    @staticmethod
    def create_draft(supplier_id: int, items: List[Dict[str, Any]],
                     created_by_user_id: int, notes: str = "",
                     expected_delivery_date=None) -> int:
        supplier_id = int(supplier_id or 0)
        created_by_user_id = int(created_by_user_id or 0)

        if supplier_id <= 0:
            raise ValueError("supplier_id is required.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")
        if not items:
            raise ValueError("At least one product line is required.")

        with get_session() as s:
            order = PurchaseOrder(
                order_no="",  # set below, once the real id exists
                supplier_id=supplier_id,
                status="draft",
                created_by_user_id=created_by_user_id,
                notes=(notes or "").strip() or None,
                expected_delivery_date=expected_delivery_date,
            )
            s.add(order)
            s.flush()
            order.order_no = f"PO-{order.id}"

            for it in items:
                s.add(_validate_and_build_item(s, order.id, it))

            s.flush()
            order_id = order.id

        return order_id

    # ------------------------------------------------------------------
    # UPDATE DRAFT - fully editable, any time, as long as nothing has
    # moved to in_transit yet.
    # ------------------------------------------------------------------
    @staticmethod
    def update_draft(purchase_order_id: int, supplier_id: int,
                     items: List[Dict[str, Any]], notes: str = "",
                     expected_delivery_date=None) -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)
        supplier_id = int(supplier_id or 0)

        if supplier_id <= 0:
            raise ValueError("supplier_id is required.")
        if not items:
            raise ValueError("At least one product line is required.")

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Only a draft order can be edited this way - this order "
                    f"is currently '{order.status}'."
                )

            s.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.purchase_order_id == purchase_order_id
            ).delete(synchronize_session=False)

            order.supplier_id = supplier_id
            order.notes = (notes or "").strip() or None
            order.expected_delivery_date = expected_delivery_date

            for it in items:
                s.add(_validate_and_build_item(s, purchase_order_id, it))

        return {"ok": True, "purchase_order_id": purchase_order_id, "status": "draft"}

    # ------------------------------------------------------------------
    # MARK IN TRANSIT - the supplier has acknowledged/is sending. NO
    # StockMovement at all - see the class docstring for the full
    # reasoning on why this differs from Sales Order's dispatch().
    # ------------------------------------------------------------------
    @staticmethod
    def mark_in_transit(purchase_order_id: int, marked_by_user_id: int, driver_id: int = None) -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)
        marked_by_user_id = int(marked_by_user_id or 0)

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Only a draft order can be marked in-transit - this "
                    f"order is currently '{order.status}'."
                )

            items = s.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.purchase_order_id == purchase_order_id
            ).all()
            if not items:
                raise ValueError("This order has no product lines.")

            order.status = "in_transit"
            order.dispatched_by_user_id = marked_by_user_id
            order.dispatched_at = datetime.now()
            if driver_id:
                order.driver_id = int(driver_id)

        return {"ok": True, "purchase_order_id": purchase_order_id, "status": "in_transit"}

    # ------------------------------------------------------------------
    # REVERT TO DRAFT - since nothing ever touched stock at in_transit,
    # this is simpler than Sales Order's own revert: nothing to reverse
    # in StockMovement at all, just clear status and any saved progress.
    # ------------------------------------------------------------------
    @staticmethod
    def revert_to_draft(purchase_order_id: int, reverted_by_user_id: int) -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Can only revert an in-transit order back to draft - "
                    f"this order is currently '{order.status}'."
                )

            items = s.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.purchase_order_id == purchase_order_id
            ).all()
            for it in items:
                it.received_weight_kg = None
                it.received_qty_bags = None
                it.received_price = None
                it.accepted_weight_kg = None
                it.accepted_qty_bags = None
                it.rejected_weight_kg = None
                it.rejected_qty_bags = None
                it.final_line_amount = None

            order.status = "draft"
            order.dispatched_by_user_id = None
            order.dispatched_at = None

        return {"ok": True, "purchase_order_id": purchase_order_id, "status": "draft"}

    # ------------------------------------------------------------------
    # SAVE CONFIRMATION PROGRESS - what's been verified so far, without
    # finalizing. No StockMovement, no status change - mirrors Sales
    # Order's save_confirmation_progress exactly.
    # ------------------------------------------------------------------
    @staticmethod
    def save_confirmation_progress(purchase_order_id: int,
                                   item_confirmations: List[Dict[str, Any]]) -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Confirmation progress can only be saved for an "
                    f"in-transit order - this order is currently '{order.status}'."
                )

            by_id = {
                it.id: it for it in
                s.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order_id == purchase_order_id).all()
            }

            saved_count = 0
            for conf in (item_confirmations or []):
                item_id = int(conf.get("item_id") or 0)
                item = by_id.get(item_id)
                if item is None:
                    continue

                is_fert = _is_fert(item.product_type_snapshot)
                if is_fert:
                    if conf.get("received_qty_bags") is not None:
                        item.received_qty_bags = _f(conf.get("received_qty_bags"))
                    if conf.get("accepted_qty_bags") is not None:
                        item.accepted_qty_bags = _f(conf.get("accepted_qty_bags"))
                    if conf.get("rejected_qty_bags") is not None:
                        item.rejected_qty_bags = _f(conf.get("rejected_qty_bags"))
                else:
                    if conf.get("received_weight_kg") is not None:
                        item.received_weight_kg = _f(conf.get("received_weight_kg"))
                    if conf.get("accepted_weight_kg") is not None:
                        item.accepted_weight_kg = _f(conf.get("accepted_weight_kg"))
                    if conf.get("rejected_weight_kg") is not None:
                        item.rejected_weight_kg = _f(conf.get("rejected_weight_kg"))

                if conf.get("received_price") is not None:
                    item.received_price = _f(conf.get("received_price"))

                saved_count += 1

        return {"ok": True, "purchase_order_id": purchase_order_id, "lines_saved": saved_count}

    # ------------------------------------------------------------------
    # CONFIRM - we've received and verified what actually arrived. The
    # ONLY point stock genuinely changes for a Purchase Order.
    # ------------------------------------------------------------------
    @staticmethod
    def confirm(purchase_order_id: int,
               item_confirmations: List[Dict[str, Any]],
               confirmed_by_user_id: int) -> Dict[str, Any]:
        """
        item_confirmations: [{"item_id", "received_weight_kg" OR
                              "received_qty_bags" (whichever matches the
                              line's own product_type_snapshot),
                              "received_price", "accepted_weight_kg" OR
                              "accepted_qty_bags", "rejected_weight_kg" OR
                              "rejected_qty_bags"}, ...]
        """
        purchase_order_id = int(purchase_order_id or 0)
        confirmed_by_user_id = int(confirmed_by_user_id or 0)
        if confirmed_by_user_id <= 0:
            raise ValueError("confirmed_by_user_id is required.")

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Only an in-transit order can be confirmed - this order "
                    f"is currently '{order.status}'."
                )

            by_id = {
                it.id: it for it in
                s.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order_id == purchase_order_id).all()
            }
            if not item_confirmations:
                raise ValueError("At least one line's confirmation is required.")

            confirmed_ids = {int(c.get("item_id") or 0) for c in item_confirmations}
            missing = set(by_id.keys()) - confirmed_ids
            if missing:
                raise ValueError(
                    f"Every line must be confirmed before finalizing this "
                    f"order - {len(missing)} line(s) still missing. Use "
                    f"'Save Progress' if verification isn't complete yet."
                )

            total = 0.0
            for conf in item_confirmations:
                item_id = int(conf.get("item_id") or 0)
                item = by_id.get(item_id)
                if item is None:
                    raise ValueError(f"Purchase order item #{item_id} does not belong to this order.")

                is_fert = _is_fert(item.product_type_snapshot)
                received_price = _f(conf.get("received_price"))

                if is_fert:
                    received_qty = _f(conf.get("received_qty_bags"))
                    accepted = _f(conf.get("accepted_qty_bags"))
                    rejected = _f(conf.get("rejected_qty_bags"))
                    unit_label = "bags"
                else:
                    received_qty = _f(conf.get("received_weight_kg"))
                    accepted = _f(conf.get("accepted_weight_kg"))
                    rejected = _f(conf.get("rejected_weight_kg"))
                    unit_label = "kg"

                if received_qty <= 0:
                    raise ValueError(f"Line #{item_id}: received quantity must be > 0.")
                # A negative accepted/rejected/price would still satisfy the
                # sum check below (e.g. 150 + -50 = 100) and inflate the total.
                if accepted < 0 or rejected < 0 or received_price < 0:
                    raise ValueError(
                        f"Line #{item_id}: accepted, rejected and price cannot be negative.")
                # Never just trusted from the caller - accepted + rejected
                # must genuinely account for the whole received quantity.
                if abs((accepted + rejected) - received_qty) > 0.01:
                    raise ValueError(
                        f"Line #{item_id}: accepted ({accepted:,.2f}) + rejected "
                        f"({rejected:,.2f}) must equal the received quantity "
                        f"({received_qty:,.2f} {unit_label})."
                    )

                final_line_amount = round(accepted * received_price, 2)

                if is_fert:
                    item.received_qty_bags = received_qty
                    item.accepted_qty_bags = accepted
                    item.rejected_qty_bags = rejected
                else:
                    item.received_weight_kg = received_qty
                    item.accepted_weight_kg = accepted
                    item.rejected_weight_kg = rejected

                item.received_price = received_price
                item.final_line_amount = final_line_amount
                total += final_line_amount

                # Only ACCEPTED goods ever enter stock. Rejected goods
                # never get a StockMovement at all - nothing to reverse,
                # since nothing was ever added for them (unlike Sales
                # Order's rejected, which reverses a real dispatch that
                # already happened).
                if accepted > 0:
                    if is_fert:
                        bags_in = accepted
                    else:
                        # Crops are dual-tracked (kg + bags). Only kg is
                        # verified at confirmation, so the bags that come in
                        # are the ordered bags in proportion to the accepted
                        # share of the ordered weight - writing 0 here made
                        # stock_bags lag stock_kg more with every order.
                        ord_kg = float(item.ordered_weight_kg or 0)
                        ord_bags = float(item.ordered_qty_bags or 0)
                        bags_in = round(ord_bags * accepted / ord_kg) if ord_kg > 0 else 0
                    s.add(StockMovement(
                        product_id=item.product_id,
                        movement_type="purchase_in",
                        qty_bags=bags_in,
                        total_weight_kg=0 if is_fert else accepted,
                        unit_rate=received_price,
                        reference_type="purchase_order_item",
                        reference_id=item.id,
                        # No party_id here - the supplier is a
                        # PurchaseOrderSupplier, not a Party, and this
                        # column has an enforced foreign key to
                        # parties.id. reference_type/reference_id
                        # already give real traceability back to this
                        # order.
                        created_by_user_id=confirmed_by_user_id,
                    ))

            order.status = "confirmed"
            order.confirmed_by_user_id = confirmed_by_user_id
            order.confirmed_at = datetime.now()
            order.final_total_amount = round(total, 2)

        return {"ok": True, "purchase_order_id": purchase_order_id, "status": "confirmed",
                "final_total_amount": round(total, 2)}

    # ------------------------------------------------------------------
    # CANCEL - only from draft, since nothing has touched stock yet
    # ------------------------------------------------------------------
    @staticmethod
    def cancel(purchase_order_id: int, cancelled_by_user_id: int, reason: str = "") -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)
        cancelled_by_user_id = int(cancelled_by_user_id or 0)

        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Cannot cancel - this order is already '{order.status}'."
                )

            order.status = "cancelled"
            order.cancelled_by_user_id = cancelled_by_user_id
            order.cancelled_at = datetime.now()
            if reason:
                order.notes = ((order.notes or "") + f"\nCancelled: {reason}").strip()

        return {"ok": True, "purchase_order_id": purchase_order_id, "status": "cancelled"}

    # ------------------------------------------------------------------
    # "In Transit" - a derived view, every order marked in_transit but
    # not yet confirmed. Purely informational for PO (no stock effect).
    # ------------------------------------------------------------------
    @staticmethod
    def in_transit_summary() -> List[Dict[str, Any]]:
        with get_session() as s:
            from database.models.driver import Driver

            orders = s.query(PurchaseOrder).filter(PurchaseOrder.status == "in_transit").all()
            driver_ids = {o.driver_id for o in orders if o.driver_id}
            drivers = {
                d.id: {"name": d.name, "phone": d.phone or "", "vehicle_number": d.vehicle_number or ""}
                for d in s.query(Driver).filter(Driver.id.in_(driver_ids)).all()
            } if driver_ids else {}

            out = []
            for order in orders:
                items = s.query(PurchaseOrderItem).filter(
                    PurchaseOrderItem.purchase_order_id == order.id
                ).all()
                out.append({
                    "purchase_order_id": order.id,
                    "order_no": order.order_no,
                    "supplier_id": order.supplier_id,
                    "dispatched_at": order.dispatched_at,
                    "expected_delivery_date": order.expected_delivery_date,
                    "driver": drivers.get(order.driver_id),
                    "items": [
                        {"item_id": it.id, "product_id": it.product_id,
                        "product_type_snapshot": it.product_type_snapshot,
                        "ordered_weight_kg": float(it.ordered_weight_kg),
                        "ordered_qty_bags": float(it.ordered_qty_bags),
                        "expected_price": float(it.expected_price)}
                        for it in items
                    ],
                })
        return out

    # ------------------------------------------------------------------
    # Full detail for one order
    # ------------------------------------------------------------------
    @staticmethod
    def get_order_detail(purchase_order_id: int) -> Dict[str, Any]:
        purchase_order_id = int(purchase_order_id or 0)
        with get_session() as s:
            order = s.get(PurchaseOrder, purchase_order_id)
            if order is None:
                raise ValueError("Purchase Order not found.")

            items = s.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.purchase_order_id == purchase_order_id
            ).all()
            product_ids = {it.product_id for it in items}
            products = {
                p.id: p.name for p in
                s.query(Product).filter(Product.id.in_(product_ids)).all()
            } if product_ids else {}

            driver_info = None
            if order.driver_id:
                from database.models.driver import Driver
                drv = s.get(Driver, order.driver_id)
                if drv:
                    driver_info = {
                        "id": drv.id, "name": drv.name, "phone": drv.phone or "",
                        "vehicle_number": drv.vehicle_number or "",
                    }

            payment_info = None
            if order.status == "confirmed":
                from database.services.order_payment_service import OrderPaymentService
                payment_info = OrderPaymentService.get_balance("purchase_order", order.id)

            return {
                "purchase_order_id": order.id,
                "order_no": order.order_no,
                "supplier_id": order.supplier_id,
                "status": order.status,
                "created_at": order.created_at,
                "dispatched_at": order.dispatched_at,
                "confirmed_at": order.confirmed_at,
                "expected_delivery_date": order.expected_delivery_date,
                "driver": driver_info,
                "payment": payment_info,
                "final_total_amount": (
                    float(order.final_total_amount)
                    if order.final_total_amount is not None else None
                ),
                "notes": order.notes,
                "items": [
                    {
                        "item_id": it.id,
                        "product_id": it.product_id,
                        "product_name": products.get(it.product_id, f"Product#{it.product_id}"),
                        "product_type_snapshot": it.product_type_snapshot,
                        "ordered_weight_kg": float(it.ordered_weight_kg),
                        "ordered_qty_bags": float(it.ordered_qty_bags),
                        "expected_price": float(it.expected_price),
                        "received_weight_kg": (float(it.received_weight_kg) if it.received_weight_kg is not None else None),
                        "received_qty_bags": (float(it.received_qty_bags) if it.received_qty_bags is not None else None),
                        "received_price": (float(it.received_price) if it.received_price is not None else None),
                        "accepted_weight_kg": (float(it.accepted_weight_kg) if it.accepted_weight_kg is not None else None),
                        "accepted_qty_bags": (float(it.accepted_qty_bags) if it.accepted_qty_bags is not None else None),
                        "rejected_weight_kg": (float(it.rejected_weight_kg) if it.rejected_weight_kg is not None else None),
                        "rejected_qty_bags": (float(it.rejected_qty_bags) if it.rejected_qty_bags is not None else None),
                        "final_line_amount": (float(it.final_line_amount) if it.final_line_amount is not None else None),
                    }
                    for it in items
                ],
            }

    # ------------------------------------------------------------------
    # LIST FOR ONE SUPPLIER - every Purchase Order for this supplier,
    # newest first, with payment status included for confirmed orders.
    # ------------------------------------------------------------------
    @staticmethod
    def list_for_supplier(supplier_id: int) -> List[Dict[str, Any]]:
        supplier_id = int(supplier_id or 0)
        with get_session() as s:
            orders = (
                s.query(PurchaseOrder)
                .filter(PurchaseOrder.supplier_id == supplier_id)
                .order_by(PurchaseOrder.created_at.desc())
                .all()
            )

        from database.services.order_payment_service import OrderPaymentService
        out = []
        for o in orders:
            payment = None
            if o.status == "confirmed":
                payment = OrderPaymentService.get_balance("purchase_order", o.id)
            out.append({
                "purchase_order_id": o.id,
                "order_no": o.order_no,
                "status": o.status,
                "created_at": o.created_at,
                "dispatched_at": o.dispatched_at,
                "confirmed_at": o.confirmed_at,
                "expected_delivery_date": o.expected_delivery_date,
                "final_total_amount": float(o.final_total_amount) if o.final_total_amount is not None else None,
                "payment_status": payment["status"] if payment else "",
                "payment_balance": payment["balance"] if payment else None,
            })
        return out