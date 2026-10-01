# database/services/sales_order_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import func, case

from database.session import get_session
from database.models.sales_order import SalesOrder
from database.models.sales_order_item import SalesOrderItem
from database.models.product import Product
from database.models.stock_movement import StockMovement, STOCK_IN_TYPES, STOCK_OUT_TYPES


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


def _validate_and_build_item(s, order_id: int, it: Dict[str, Any]) -> "SalesOrderItem":
    pid = int(it.get("product_id") or 0)
    if pid <= 0:
        raise ValueError("Every line needs a product_id.")

    product = s.get(Product, pid)
    if product is None:
        raise ValueError(f"Product #{pid} not found.")
    ptype = "fertilizer" if _is_fert(product.product_type) else "crops"

    weight = _f(it.get("dispatched_weight_kg"))
    bags = _f(it.get("dispatched_qty_bags"))
    my_price = _f(it.get("my_price"))

    if ptype == "fertilizer":
        if bags <= 0:
            raise ValueError(f"{product.name}: enter a bag quantity greater than zero.")
        weight = 0.0
    else:
        # Crops needs BOTH weight and bags now - dual-tracked everywhere
        # else in this app (see intake_crops_dialog.py's own comment on
        # this exact point). This used to force bags to 0 here, silently
        # discarding whatever the UI sent - that's exactly what caused
        # the gap: bags qty was simply never actually kept for crops.
        if weight <= 0:
            raise ValueError(f"{product.name}: enter a weight (kg) greater than zero.")
        if bags <= 0:
            raise ValueError(f"{product.name}: enter a bags quantity greater than zero.")

    return SalesOrderItem(
        sales_order_id=order_id,
        product_id=pid,
        product_type_snapshot=ptype,
        dispatched_weight_kg=weight,
        dispatched_qty_bags=bags,
        my_price=my_price,
        notes=(it.get("notes") or "").strip() or None,
    )


class SalesOrderService:
    """
    Selling to a company - crops (by weight, kg) or fertilizer (by bags),
    matching the same dual-unit convention used throughout this app.
    Entirely separate from the existing, immediate Sale/checkout flow.
    Stock only ever changes at two specific moments, matching the
    client's own explicit principle:

      dispatch  - goods physically leave the warehouse. Deducted from
                  Available Stock via a real StockMovement (reusing the
                  existing "sale_out" type, which every stock calculation
                  in this app already recognizes - reference_type=
                  "sales_order_item" is what actually distinguishes a
                  Sales Order's movement from a regular sale's, for
                  traceability, without requiring a new movement_type
                  enum value or touching the six existing places that
                  compute on-hand stock).

      confirm   - the company confirms what they actually received.
                  Accepted quantity stays sold (no further movement -
                  it already left at dispatch and stayed gone). Rejected
                  quantity returns to Available Stock via "return_in".

    "In Transit" is deliberately NOT a separate stock bucket - it's a
    derived concept: whatever's been dispatched (in_transit status) but
    not yet confirmed. See in_transit_summary().

    Draft commits nothing at all - no StockMovement, no party effect -
    so it can be freely edited or cancelled without needing to reverse
    anything.
    """

    # ------------------------------------------------------------------
    # CREATE (draft) - commits nothing to stock
    # ------------------------------------------------------------------
    @staticmethod
    def create_draft(buyer_company_id: int, items: List[Dict[str, Any]],
                     created_by_user_id: int, notes: str = "",
                     expected_delivery_date=None) -> int:
        buyer_company_id = int(buyer_company_id or 0)
        created_by_user_id = int(created_by_user_id or 0)

        if buyer_company_id <= 0:
            raise ValueError("buyer_company_id is required.")
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")
        if not items:
            raise ValueError("At least one product line is required.")

        with get_session() as s:
            order = SalesOrder(
                order_no="",  # set below, once the real id exists
                buyer_company_id=buyer_company_id,
                status="draft",
                created_by_user_id=created_by_user_id,
                notes=(notes or "").strip() or None,
                expected_delivery_date=expected_delivery_date,
            )
            s.add(order)
            s.flush()
            order.order_no = f"SO-{order.id}"

            for it in items:
                s.add(_validate_and_build_item(s, order.id, it))

            s.flush()
            order_id = order.id

        return order_id

    # ------------------------------------------------------------------
    # UPDATE DRAFT - fully editable, any time, as long as nothing has
    # been dispatched yet. A draft can sit unfinished for days, weeks,
    # or months before it's ready to send - buyer, lines, and notes can
    # all change freely, since nothing about a draft has touched stock
    # or any party balance at all.
    # ------------------------------------------------------------------
    @staticmethod
    def update_draft(sales_order_id: int, buyer_company_id: int,
                     items: List[Dict[str, Any]], notes: str = "",
                     expected_delivery_date=None) -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)
        buyer_company_id = int(buyer_company_id or 0)

        if buyer_company_id <= 0:
            raise ValueError("buyer_company_id is required.")
        if not items:
            raise ValueError("At least one product line is required.")

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Only a draft order can be edited this way - this order "
                    f"is currently '{order.status}'. An in-transit order's "
                    f"lines are already reflected in real stock movements; "
                    f"see revert_to_draft if it genuinely needs to change."
                )

            # Replace the lines entirely - safe and simple, since nothing
            # about a draft's rows has any real-world effect to preserve.
            s.query(SalesOrderItem).filter(
                SalesOrderItem.sales_order_id == sales_order_id
            ).delete(synchronize_session=False)

            order.buyer_company_id = buyer_company_id
            order.notes = (notes or "").strip() or None
            order.expected_delivery_date = expected_delivery_date

            for it in items:
                s.add(_validate_and_build_item(s, sales_order_id, it))

        return {"ok": True, "sales_order_id": sales_order_id, "status": "draft"}

    # ------------------------------------------------------------------
    # DISPATCH - goods physically leave the warehouse
    # ------------------------------------------------------------------
    @staticmethod
    def dispatch(sales_order_id: int, dispatched_by_user_id: int, driver_id: int = None) -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)
        dispatched_by_user_id = int(dispatched_by_user_id or 0)
        if dispatched_by_user_id <= 0:
            raise ValueError("dispatched_by_user_id is required.")

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Only a draft order can be dispatched - this order is "
                    f"currently '{order.status}'."
                )

            items = s.query(SalesOrderItem).filter(
                SalesOrderItem.sales_order_id == sales_order_id
            ).all()
            if not items:
                raise ValueError("This order has no product lines to dispatch.")

            # Validate stock BEFORE creating any movement - all lines must
            # be coverable, or nothing about this dispatch commits at all.
            # Tracked separately per unit, matching each line's own type -
            # a fertilizer line's need is in bags, a crop line's in kg,
            # never mixed together.
            needed_kg_by_pid: Dict[int, float] = {}
            needed_bags_by_pid: Dict[int, float] = {}
            for it in items:
                # Bags are tracked for BOTH types (crops are dual-tracked kg +
                # bags), so every line's bags must be covered and deducted.
                needed_bags_by_pid[it.product_id] = needed_bags_by_pid.get(it.product_id, 0.0) + float(it.dispatched_qty_bags)
                if not _is_fert(it.product_type_snapshot):
                    needed_kg_by_pid[it.product_id] = needed_kg_by_pid.get(it.product_id, 0.0) + float(it.dispatched_weight_kg)

            for pid, needed in needed_kg_by_pid.items():
                avail = s.query(
                    func.coalesce(func.sum(case(
                        (StockMovement.movement_type.in_(STOCK_IN_TYPES),
                         StockMovement.total_weight_kg),
                        (StockMovement.movement_type.in_(STOCK_OUT_TYPES),
                         -StockMovement.total_weight_kg),
                        else_=0,
                    )), 0.0)
                ).filter(StockMovement.product_id == pid).scalar()
                if float(avail or 0.0) < needed:
                    product = s.get(Product, pid)
                    name = product.name if product else f"Product#{pid}"
                    raise ValueError(
                        f"Insufficient stock for {name}: need {needed:,.2f} kg, "
                        f"only {float(avail or 0.0):,.2f} kg available."
                    )

            for pid, needed in needed_bags_by_pid.items():
                avail = s.query(
                    func.coalesce(func.sum(case(
                        (StockMovement.movement_type.in_(STOCK_IN_TYPES),
                         StockMovement.qty_bags),
                        (StockMovement.movement_type.in_(STOCK_OUT_TYPES),
                         -StockMovement.qty_bags),
                        else_=0,
                    )), 0.0)
                ).filter(StockMovement.product_id == pid).scalar()
                if float(avail or 0.0) < needed:
                    product = s.get(Product, pid)
                    name = product.name if product else f"Product#{pid}"
                    raise ValueError(
                        f"Insufficient stock for {name}: need {needed:,.2f} bags, "
                        f"only {float(avail or 0.0):,.2f} bags available."
                    )

            for it in items:
                is_fert = _is_fert(it.product_type_snapshot)
                s.add(StockMovement(
                    product_id=it.product_id,
                    movement_type="sale_out",
                    qty_bags=float(it.dispatched_qty_bags),
                    total_weight_kg=0 if is_fert else float(it.dispatched_weight_kg),
                    unit_rate=float(it.my_price),
                    reference_type="sales_order_item",
                    reference_id=it.id,
                    # No party_id here - the buyer is a Company, not a
                    # Party, and this column has an enforced foreign key
                    # to parties.id. reference_type/reference_id already
                    # give real traceability back to this order.
                    created_by_user_id=dispatched_by_user_id,
                ))

            order.status = "in_transit"
            order.dispatched_by_user_id = dispatched_by_user_id
            order.dispatched_at = datetime.utcnow()
            if driver_id:
                order.driver_id = int(driver_id)

        return {"ok": True, "sales_order_id": sales_order_id, "status": "in_transit"}

    # ------------------------------------------------------------------
    # REVERT TO DRAFT - reverses a dispatch entirely, so the operator can
    # change what was sent and re-dispatch differently. Only from
    # in_transit, never from confirmed - once confirmed, real returns
    # have ALSO already happened (rejected quantities went back to
    # stock), and safely reversing that combination is a genuinely
    # different, more complex problem this does not attempt to solve.
    # ------------------------------------------------------------------
    @staticmethod
    def revert_to_draft(sales_order_id: int, reverted_by_user_id: int) -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Can only revert an in-transit order back to draft - "
                    f"this order is currently '{order.status}'. A confirmed "
                    f"order has real returns already recorded too; reverting "
                    f"that safely needs a different, explicit operation."
                )

            items = s.query(SalesOrderItem).filter(
                SalesOrderItem.sales_order_id == sales_order_id
            ).all()
            item_ids = [it.id for it in items]

            removed_movements = 0
            if item_ids:
                removed_movements = (
                    s.query(StockMovement)
                    .filter(
                        StockMovement.reference_type == "sales_order_item",
                        StockMovement.reference_id.in_(item_ids),
                        StockMovement.movement_type == "sale_out",
                    )
                    .delete(synchronize_session=False)
                )

            # Clear any partial confirmation progress too - reverting to
            # draft means genuinely starting the dispatch process over,
            # so whatever feedback was saved no longer applies to
            # whatever gets dispatched next.
            for it in items:
                it.company_weight_kg = None
                it.company_qty_bags = None
                it.company_price = None
                it.accepted_weight_kg = None
                it.accepted_qty_bags = None
                it.rejected_weight_kg = None
                it.rejected_qty_bags = None
                it.final_line_amount = None

            order.status = "draft"
            order.dispatched_by_user_id = None
            order.dispatched_at = None

        return {
            "ok": True, "sales_order_id": sales_order_id, "status": "draft",
            "stock_movements_reversed": removed_movements,
        }

    # ------------------------------------------------------------------
    # SAVE CONFIRMATION PROGRESS - the company's feedback often arrives
    # gradually, not all at once. This stores whatever's known so far
    # WITHOUT finalizing anything: no requirement that every line be
    # included, no requirement that accepted+rejected sum correctly yet,
    # no StockMovement, no status change. The order stays "in_transit"
    # the whole time. confirm() (below) is the separate, final step that
    # actually commits - it re-validates everything properly regardless
    # of what was saved here.
    # ------------------------------------------------------------------
    @staticmethod
    def save_confirmation_progress(sales_order_id: int,
                                   item_confirmations: List[Dict[str, Any]]) -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Confirmation progress can only be saved for an "
                    f"in-transit order - this order is currently '{order.status}'."
                )

            by_id = {
                it.id: it for it in
                s.query(SalesOrderItem).filter(SalesOrderItem.sales_order_id == sales_order_id).all()
            }

            saved_count = 0
            for conf in (item_confirmations or []):
                item_id = int(conf.get("item_id") or 0)
                item = by_id.get(item_id)
                if item is None:
                    continue

                is_fert = _is_fert(item.product_type_snapshot)
                if is_fert:
                    if conf.get("company_qty_bags") is not None:
                        item.company_qty_bags = _f(conf.get("company_qty_bags"))
                    if conf.get("accepted_qty_bags") is not None:
                        item.accepted_qty_bags = _f(conf.get("accepted_qty_bags"))
                    if conf.get("rejected_qty_bags") is not None:
                        item.rejected_qty_bags = _f(conf.get("rejected_qty_bags"))
                else:
                    if conf.get("company_weight_kg") is not None:
                        item.company_weight_kg = _f(conf.get("company_weight_kg"))
                    if conf.get("accepted_weight_kg") is not None:
                        item.accepted_weight_kg = _f(conf.get("accepted_weight_kg"))
                    if conf.get("rejected_weight_kg") is not None:
                        item.rejected_weight_kg = _f(conf.get("rejected_weight_kg"))

                if conf.get("company_price") is not None:
                    item.company_price = _f(conf.get("company_price"))

                saved_count += 1

        return {"ok": True, "sales_order_id": sales_order_id, "lines_saved": saved_count}

    # ------------------------------------------------------------------
    # CONFIRM - the company confirms what they actually received
    # ------------------------------------------------------------------
    @staticmethod
    def confirm(sales_order_id: int,
               item_confirmations: List[Dict[str, Any]],
               confirmed_by_user_id: int) -> Dict[str, Any]:
        """
        item_confirmations: [{"item_id", "company_weight_kg" OR
                              "company_qty_bags" (whichever matches the
                              line's own product_type_snapshot),
                              "company_price", "accepted_weight_kg" OR
                              "accepted_qty_bags", "rejected_weight_kg" OR
                              "rejected_qty_bags"}, ...]
        """
        sales_order_id = int(sales_order_id or 0)
        confirmed_by_user_id = int(confirmed_by_user_id or 0)
        if confirmed_by_user_id <= 0:
            raise ValueError("confirmed_by_user_id is required.")

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "in_transit":
                raise ValueError(
                    f"Only an in-transit order can be confirmed - this order "
                    f"is currently '{order.status}'."
                )

            by_id = {
                it.id: it for it in
                s.query(SalesOrderItem).filter(SalesOrderItem.sales_order_id == sales_order_id).all()
            }
            if not item_confirmations:
                raise ValueError("At least one line's confirmation is required.")

            confirmed_ids = {int(c.get("item_id") or 0) for c in item_confirmations}
            missing = set(by_id.keys()) - confirmed_ids
            if missing:
                raise ValueError(
                    f"Every line must be confirmed before finalizing this "
                    f"order - {len(missing)} line(s) still missing. Use "
                    f"'Save Progress' if the company's feedback isn't "
                    f"complete yet."
                )

            total = 0.0
            for conf in item_confirmations:
                item_id = int(conf.get("item_id") or 0)
                item = by_id.get(item_id)
                if item is None:
                    raise ValueError(f"Sales order item #{item_id} does not belong to this order.")

                is_fert = _is_fert(item.product_type_snapshot)
                company_price = _f(conf.get("company_price"))

                if is_fert:
                    company_qty = _f(conf.get("company_qty_bags"))
                    accepted = _f(conf.get("accepted_qty_bags"))
                    rejected = _f(conf.get("rejected_qty_bags"))
                    unit_label = "bags"
                else:
                    company_qty = _f(conf.get("company_weight_kg"))
                    accepted = _f(conf.get("accepted_weight_kg"))
                    rejected = _f(conf.get("rejected_weight_kg"))
                    unit_label = "kg"

                if company_qty <= 0:
                    raise ValueError(f"Line #{item_id}: company quantity must be > 0.")
                # A negative accepted/rejected/price would still satisfy the
                # sum check below (e.g. 150 + -50 = 100) and inflate the total.
                if accepted < 0 or rejected < 0 or company_price < 0:
                    raise ValueError(
                        f"Line #{item_id}: accepted, rejected and price cannot be negative.")
                # Never just trusted from the caller - accepted + rejected
                # must genuinely account for the whole confirmed quantity.
                if abs((accepted + rejected) - company_qty) > 0.01:
                    raise ValueError(
                        f"Line #{item_id}: accepted ({accepted:,.2f}) + rejected "
                        f"({rejected:,.2f}) must equal the company's confirmed "
                        f"quantity ({company_qty:,.2f} {unit_label})."
                    )

                final_line_amount = round(accepted * company_price, 2)

                if is_fert:
                    item.company_qty_bags = company_qty
                    item.accepted_qty_bags = accepted
                    item.rejected_qty_bags = rejected
                else:
                    item.company_weight_kg = company_qty
                    item.accepted_weight_kg = accepted
                    item.rejected_weight_kg = rejected

                item.company_price = company_price
                item.final_line_amount = final_line_amount
                total += final_line_amount

                if rejected > 0:
                    if is_fert:
                        bags_back = rejected
                    else:
                        # Crops are dual-tracked: dispatch deducted the bags
                        # too, so the rejected share of them goes back in
                        # proportion to the rejected share of the weight.
                        sent_kg = float(item.dispatched_weight_kg or 0)
                        sent_bags = float(item.dispatched_qty_bags or 0)
                        bags_back = (
                            min(sent_bags, round(sent_bags * rejected / sent_kg))
                            if sent_kg > 0 else 0
                        )
                    s.add(StockMovement(
                        product_id=item.product_id,
                        movement_type="return_in",
                        qty_bags=bags_back,
                        total_weight_kg=0 if is_fert else rejected,
                        unit_rate=company_price,
                        reference_type="sales_order_item",
                        reference_id=item.id,
                        # No party_id here - see the same note in dispatch().
                        created_by_user_id=confirmed_by_user_id,
                    ))

            order.status = "confirmed"
            order.confirmed_by_user_id = confirmed_by_user_id
            order.confirmed_at = datetime.utcnow()
            order.final_total_amount = round(total, 2)

        return {"ok": True, "sales_order_id": sales_order_id, "status": "confirmed",
                "final_total_amount": round(total, 2)}

    # ------------------------------------------------------------------
    # CANCEL - only from draft, since nothing has touched stock yet
    # ------------------------------------------------------------------
    @staticmethod
    def cancel(sales_order_id: int, cancelled_by_user_id: int, reason: str = "") -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)
        cancelled_by_user_id = int(cancelled_by_user_id or 0)

        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")
            if order.status != "draft":
                raise ValueError(
                    f"Cannot cancel - this order is already '{order.status}'. "
                    f"Once dispatched, stock has genuinely moved; cancelling "
                    f"at that point needs an explicit reversal, not a simple cancel."
                )

            order.status = "cancelled"
            order.cancelled_by_user_id = cancelled_by_user_id
            order.cancelled_at = datetime.utcnow()
            if reason:
                order.notes = ((order.notes or "") + f"\nCancelled: {reason}").strip()

        return {"ok": True, "sales_order_id": sales_order_id, "status": "cancelled"}

    # ------------------------------------------------------------------
    # "In Transit" - a derived view, not a stock bucket. Every order
    # that's been dispatched but not yet confirmed.
    # ------------------------------------------------------------------
    @staticmethod
    def in_transit_summary() -> List[Dict[str, Any]]:
        with get_session() as s:
            from database.models.driver import Driver

            orders = s.query(SalesOrder).filter(SalesOrder.status == "in_transit").all()
            driver_ids = {o.driver_id for o in orders if o.driver_id}
            drivers = {
                d.id: {"name": d.name, "phone": d.phone or "", "vehicle_number": d.vehicle_number or ""}
                for d in s.query(Driver).filter(Driver.id.in_(driver_ids)).all()
            } if driver_ids else {}

            out = []
            for order in orders:
                items = s.query(SalesOrderItem).filter(
                    SalesOrderItem.sales_order_id == order.id
                ).all()
                out.append({
                    "sales_order_id": order.id,
                    "order_no": order.order_no,
                    "buyer_company_id": order.buyer_company_id,
                    "dispatched_at": order.dispatched_at,
                    "expected_delivery_date": order.expected_delivery_date,
                    "driver": drivers.get(order.driver_id),
                    "items": [
                        {"item_id": it.id, "product_id": it.product_id,
                        "product_type_snapshot": it.product_type_snapshot,
                        "dispatched_weight_kg": float(it.dispatched_weight_kg),
                        "dispatched_qty_bags": float(it.dispatched_qty_bags),
                        "my_price": float(it.my_price)}
                        for it in items
                    ],
                })
        return out

    # ------------------------------------------------------------------
    # Full detail for one order - used by the list screen's double-click
    # and the confirm dialog, so both read from exactly the same shape.
    # ------------------------------------------------------------------
    @staticmethod
    def get_order_detail(sales_order_id: int) -> Dict[str, Any]:
        sales_order_id = int(sales_order_id or 0)
        with get_session() as s:
            order = s.get(SalesOrder, sales_order_id)
            if order is None:
                raise ValueError("Sales Order not found.")

            items = s.query(SalesOrderItem).filter(
                SalesOrderItem.sales_order_id == sales_order_id
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
                payment_info = OrderPaymentService.get_balance("sales_order", order.id)

            return {
                "sales_order_id": order.id,
                "order_no": order.order_no,
                "buyer_company_id": order.buyer_company_id,
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
                        "dispatched_weight_kg": float(it.dispatched_weight_kg),
                        "dispatched_qty_bags": float(it.dispatched_qty_bags),
                        "my_price": float(it.my_price),
                        "company_weight_kg": (float(it.company_weight_kg) if it.company_weight_kg is not None else None),
                        "company_qty_bags": (float(it.company_qty_bags) if it.company_qty_bags is not None else None),
                        "company_price": (float(it.company_price) if it.company_price is not None else None),
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
    # LIST FOR ONE COMPANY - every Sales Order belonging to this
    # company, newest first, with payment status included for confirmed
    # orders. Used by the Company Profile's Orders tab.
    # ------------------------------------------------------------------
    @staticmethod
    def list_for_company(buyer_company_id: int) -> List[Dict[str, Any]]:
        buyer_company_id = int(buyer_company_id or 0)
        with get_session() as s:
            orders = (
                s.query(SalesOrder)
                .filter(SalesOrder.buyer_company_id == buyer_company_id)
                .order_by(SalesOrder.created_at.desc())
                .all()
            )

        from database.services.order_payment_service import OrderPaymentService
        out = []
        for o in orders:
            payment = None
            if o.status == "confirmed":
                payment = OrderPaymentService.get_balance("sales_order", o.id)
            out.append({
                "sales_order_id": o.id,
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