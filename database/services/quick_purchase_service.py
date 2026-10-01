# database/services/quick_purchase_service.py
# -*- coding: utf-8 -*-
"""
Quick Purchase orchestrator.

The problem it solves: a customer orders 5 bags, only 3 are in stock. Instead
of recording a wrong Advance Out, the cashier buys the missing 2 from a supplier
right now, then the sale of all 5 goes through.

This service owns NO money logic and writes NO rows of its own. It composes two
services that already work - PurchaseService (buy the shortfall) and SaleService
(sell everything) - in the one order that satisfies SaleService._check_stock:

        buy the shortfall  ->  commit  ->  sell all

WHY THE ORDER IS FORCED
    SaleService.record() calls _check_stock() and raises "Insufficient stock"
    if the bags are not physically present. So the purchase MUST be committed
    before the sale is attempted. Two services, two sessions, two commits.

THE GAP THIS LEAVES (stated honestly, not hidden)
    If the purchase commits and the sale then fails, we own goods the customer
    never bought. That is a real, correct dangling purchase - stock is real,
    the supplier payable is real - not corruption. execute() reports it plainly
    in the result so the caller can tell the operator exactly what is now in
    stock and unsold, rather than letting it vanish.

SCOPE (single-supplier, per the roll-out decision)
    One QP covers the shortfall from ONE supplier. If a dealer is short, the
    caller handles the remainder (another supplier / backorder / drop-line) -
    the multi-supplier loop is a later iteration once this is proven in use.
"""
from __future__ import annotations

from typing import Dict, List, Any, Optional

from sqlalchemy import func, case

from database.session import get_session
from database.models.product import Product
from database.models.stock_movement import StockMovement, STOCK_IN_TYPES, STOCK_OUT_TYPES
from database.services.purchase_service import PurchaseService
from database.services.sales_service import SaleService

from shared.config.constants import WALKIN_PARTY_ID

WALKIN_SUPPLIER_ID = WALKIN_PARTY_ID  # mirrors PurchaseService's walk-in convention


def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


class QuickPurchaseService:

    # ------------------------------------------------------------------
    # STEP 1 - what is short?
    # ------------------------------------------------------------------
    @staticmethod
    def check_availability(items: List[Dict[str, Any]],
                          pending_extra_by_pid: Dict[int, Dict[str, float]] = None) -> Dict[str, Any]:
        """
        Split a proposed sale's lines into what is in stock and what is short.

        This is the SAME arithmetic SaleService._check_stock uses, so the two
        can never disagree about availability. Read-only: touches nothing.

        Each item: {product_id, product_type, qty_bags, total_weight_kg?}
        Fertilizer is measured in bags; crops in kg (bags are still counted).

        pending_extra_by_pid: {product_id: {"qty_bags":.., "total_weight_kg":..}}
        - virtual stock not yet actually committed to StockMovement, but
        that WILL be there by the time this sale itself commits (e.g. a
        standalone Quick Purchase already planned earlier in this same
        checkout, deliberately deferred - see cashier_sales.py's own
        comments on why). Counted as available here so this interlude
        doesn't redundantly re-prompt for something already handled;
        never touches the real "wanted" amounts themselves, which stay
        accurate for whatever downstream (e.g. build_sale_lines) needs
        the true, full quantity.

        Returns:
            {
              "all_available": bool,
              "available":   [ ...lines fully coverable... ],
              "short":       [ {..line.., short_qty_bags, short_weight_kg} ],
              "human": "3 of 5 bags of Golden are in stock; 2 short",
            }
        """
        pending_extra_by_pid = pending_extra_by_pid or {}
        available: List[Dict[str, Any]] = []
        short: List[Dict[str, Any]] = []
        notes: List[str] = []

        with get_session() as s:
            for it in items:
                pid = int(it["product_id"])
                ptype = str(it.get("product_type") or "").lower()
                want_bags = int(it.get("qty_bags") or 0)
                want_kg = _f(it.get("total_weight_kg"))

                have_bags, have_kg = QuickPurchaseService._on_hand(s, pid)
                extra = pending_extra_by_pid.get(pid) or {}
                have_bags += int(extra.get("qty_bags") or 0)
                have_kg += _f(extra.get("total_weight_kg"))
                name = QuickPurchaseService._name(s, pid)
                # Freeze the book cost NOW, before any QP can overwrite it via
                # _upsert_product. The from-stock portion of the sale must be
                # costed at THIS value, not at whatever the product's cost
                # becomes after we buy the shortfall at the dealer price.
                prod = s.get(Product, pid)
                book_cost = _f(prod.default_cost_rate) if prod is not None else 0.0

                if ptype == "fertilizer":
                    covered = min(want_bags, have_bags)
                    short_bags = max(0, want_bags - have_bags)
                    if short_bags == 0:
                        available.append({**it, "name": name, "book_cost": book_cost,
                                          "have_bags": have_bags})
                        notes.append(f"{name}: {want_bags} bag(s) in stock")
                    else:
                        short.append({**it, "name": name,
                                      "short_qty_bags": short_bags,
                                      "short_weight_kg": 0.0,
                                      "have_bags": have_bags,
                                      "book_cost": book_cost})
                        notes.append(
                            f"{name}: {covered} of {want_bags} bag(s) in stock, "
                            f"{short_bags} short")
                else:
                    # crops: shortfall is measured in kg, bags come along for the ride
                    short_kg = max(0.0, round(want_kg - have_kg, 2))
                    if short_kg <= 0.005:
                        available.append({**it, "name": name, "book_cost": book_cost,
                                          "have_kg": have_kg})
                        notes.append(f"{name}: {want_kg:g}kg in stock")
                    else:
                        # bags proportional to the kg shortfall, at least 1
                        if want_kg > 0:
                            sb = max(1, round(want_bags * (short_kg / want_kg)))
                        else:
                            sb = max(0, want_bags - have_bags)
                        short.append({**it, "name": name,
                                      "short_qty_bags": sb,
                                      "short_weight_kg": short_kg,
                                      "have_kg": max(0.0, have_kg),
                                      "book_cost": book_cost})
                        notes.append(
                            f"{name}: {max(0.0, have_kg):g} of {want_kg:g}kg in "
                            f"stock, {short_kg:g}kg short")

        return {
            "all_available": not short,
            "available": available,
            "short": short,
            "human": "; ".join(notes),
        }

    # ------------------------------------------------------------------
    # STEP 1b - turn availability into correctly-COSTED sale lines
    # ------------------------------------------------------------------
    @staticmethod
    def build_sale_lines(availability: Dict[str, Any],
                         dealer_cost_by_pid: Dict[int, float]) -> List[Dict[str, Any]]:
        """
        Produce the sale's item lines, SPLIT so profit is right.

        A quick-purchased product sells as TWO cost lots:
          - the from-stock portion, costed at the book cost frozen in
            check_availability (BEFORE the QP could move it);
          - the quick-purchased portion, costed at the dealer price.

        Each line carries an explicit unit_cost, so NONE of them fall back to
        Product.default_cost_rate - which the QP is about to overwrite. This is
        the single rule the dialog must not get wrong, so the service does it.

        dealer_cost_by_pid: {product_id: price the operator will pay the dealer}
        """
        lines: List[Dict[str, Any]] = []

        # fully-available lines: one lot, at book cost
        for it in availability.get("available", []):
            pid = int(it["product_id"])
            lines.append({
                "product_id": pid,
                "product_type": str(it.get("product_type") or "").lower(),
                "qty_bags": int(it.get("qty_bags") or 0),
                "total_weight_kg": _f(it.get("total_weight_kg")),
                "unit_price": _f(it.get("unit_price")),
                "unit_cost": _f(it.get("book_cost")),   # frozen book cost
            })

        # short lines: split into from-stock lot + quick lot
        for it in availability.get("short", []):
            pid = int(it["product_id"])
            ptype = str(it.get("product_type") or "").lower()
            price = _f(it.get("unit_price"))
            dealer = _f(dealer_cost_by_pid.get(pid))
            want_bags = int(it.get("qty_bags") or 0)
            short_bags = int(it.get("short_qty_bags") or 0)
            stock_bags = max(0, want_bags - short_bags)

            if ptype == "fertilizer":
                if stock_bags > 0:
                    lines.append({
                        "product_id": pid, "product_type": ptype,
                        "qty_bags": stock_bags, "total_weight_kg": 0.0,
                        "unit_price": price, "unit_cost": _f(it.get("book_cost")),
                    })
                if short_bags > 0:
                    lines.append({
                        "product_id": pid, "product_type": ptype,
                        "qty_bags": short_bags, "total_weight_kg": 0.0,
                        "unit_price": price, "unit_cost": dealer,
                    })
            else:
                want_kg = _f(it.get("total_weight_kg"))
                short_kg = _f(it.get("short_weight_kg"))
                stock_kg = max(0.0, round(want_kg - short_kg, 2))
                if stock_kg > 0.005:
                    lines.append({
                        "product_id": pid, "product_type": ptype,
                        "qty_bags": stock_bags, "total_weight_kg": stock_kg,
                        "unit_price": price, "unit_cost": _f(it.get("book_cost")),
                    })
                if short_kg > 0.005:
                    lines.append({
                        "product_id": pid, "product_type": ptype,
                        "qty_bags": short_bags, "total_weight_kg": short_kg,
                        "unit_price": price, "unit_cost": dealer,
                    })
        return lines

    # ------------------------------------------------------------------
    # STEP 2a - buy ONLY the shortfall (its own commit). Public so the
    # cashier flow can buy first, then record the sale itself.
    # ------------------------------------------------------------------
    @staticmethod
    def buy_shortfall(supplier_party_id: int,
                      short_lines: List[Dict[str, Any]],
                      created_by_user_id: int,
                      supplier_note: str = "") -> Dict[str, Any]:
        """
        Buy just the missing quantity from one supplier and commit it.

        Returns {"ok": True, "purchase_id": .., "total_amount": ..}
             or {"ok": False, "error": ..}. On failure nothing is committed,
        so there is never stranded stock from this call alone.

        NOTE (unchanged policy flag): PurchaseService._upsert_product overwrites
        the product's default_cost_rate with this dealer price. Correct for
        normal intake; for a one-off QP it moves the product's book cost for
        FUTURE normal sales. THIS sale is protected by unit_cost_snapshot; the
        product-default drift is a separate decision, flagged not hidden.
        """
        if not short_lines:
            return {"ok": False, "error": "no shortfall lines to purchase"}

        purchase_items = []
        with get_session() as s:
            for ln in short_lines:
                prod = s.get(Product, int(ln["product_id"]))
                if prod is None:
                    return {"ok": False,
                            "error": f"product {ln['product_id']} not found"}
                purchase_items.append({
                    "product_id": int(ln["product_id"]),
                    "sku": prod.sku,
                    "product_name": prod.name,
                    "product_type": str(ln.get("product_type")
                                        or prod.product_type or "").lower(),
                    "qty_bags": int(ln.get("short_qty_bags") or ln.get("qty_bags") or 0),
                    "total_weight_kg": _f(ln.get("short_weight_kg")
                                          or ln.get("total_weight_kg")),
                    "unit_cost": _f(ln.get("unit_cost")),
                    "note": supplier_note,
                })

        try:
            qp = PurchaseService.record({
                "supplier_party_id": int(supplier_party_id),
                "created_by_user_id": int(created_by_user_id),
                "items": purchase_items,
                "note": supplier_note or "Quick Purchase",
                "is_quick": True,
            })
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {"ok": True, "purchase_id": qp["purchase_id"],
                "total_amount": qp.get("total_amount", 0),
                "purchase_items": purchase_items}

    @staticmethod
    def link_sale(purchase_id: int, sale_id: Optional[int]) -> None:
        """Stamp linked_sale_id after the sale exists (audit link QP <-> sale)."""
        QuickPurchaseService._link(purchase_id, sale_id)

    # ------------------------------------------------------------------
    # STEP 2 - buy the shortfall from ONE supplier, then sell everything
    # ------------------------------------------------------------------
    @staticmethod
    def execute(sale_payload: Dict[str, Any],
                supplier_party_id: int,
                short_lines: List[Dict[str, Any]],
                created_by_user_id: int,
                supplier_note: str = "") -> Dict[str, Any]:
        """
        Run the interlude and finalise.

        short_lines carry the dealer cost the operator entered, under
        "unit_cost" (per bag for fertilizer, per kg for crops) - the SAME key
        SaleService and PurchaseService already understand. Every sale line in
        sale_payload that was quick-purchased MUST also carry that unit_cost so
        the frozen unit_cost_snapshot records the true dealer price and profit
        comes out right (48,700 - 45,000 = 3,700, not a default-cost guess).

        Returns on success:
            {"ok": True, "quick_purchase_id": .., "sale_id": .., "total_purchased": ..}
        On the dangling-purchase case (buy succeeded, sale failed):
            {"ok": False, "stage": "sale", "quick_purchase_id": .., "error": ..,
             "dangling": True, "stranded_stock": [..]}   # goods now owned, unsold
        """
        if not short_lines:
            return {"ok": False, "stage": "input",
                    "error": "no shortfall lines to purchase", "dangling": False}

        # ---- buy the shortfall (its OWN commit) ----
        pur = QuickPurchaseService.buy_shortfall(
            supplier_party_id, short_lines, created_by_user_id, supplier_note)
        if not pur.get("ok"):
            # nothing committed yet - clean failure, no stranded goods
            return {"ok": False, "stage": "purchase",
                    "error": pur.get("error"), "dangling": False}
        qp_id = pur["purchase_id"]
        purchase_items = pur["purchase_items"]

        # ---- now the shortfall is in stock: sell everything (its OWN commit) ----
        try:
            sale = SaleService.record(sale_payload)
        except Exception as e:
            # THE HONEST GAP: we own the goods, the sale did not happen.
            return {
                "ok": False, "stage": "sale", "error": str(e),
                "quick_purchase_id": qp_id, "dangling": True,
                "stranded_stock": purchase_items,
            }

        sale_id = sale.get("sale_id") or sale.get("id")

        # ---- backfill the audit link QP <-> sale ----
        QuickPurchaseService._link(qp_id, sale_id)

        return {
            "ok": True,
            "quick_purchase_id": qp_id,
            "sale_id": sale_id,
            "total_purchased": pur.get("total_amount", 0),
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _on_hand(s, product_id: int):
        """Current stock (bags, kg) - uses the same shared STOCK_IN_TYPES/
        STOCK_OUT_TYPES as every other stock calculation in this app."""
        bags = s.query(func.coalesce(func.sum(case(
            (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.qty_bags),
            (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.qty_bags),
            else_=0)), 0)).filter(StockMovement.product_id == product_id).scalar()
        kg = s.query(func.coalesce(func.sum(case(
            (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.total_weight_kg),
            (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.total_weight_kg),
            else_=0)), 0)).filter(StockMovement.product_id == product_id).scalar()
        return int(bags or 0), _f(kg)

    @staticmethod
    def _name(s, product_id: int) -> str:
        r = s.get(Product, int(product_id))
        return r.name if r is not None else f"Product#{product_id}"

    @staticmethod
    def _link(purchase_id: int, sale_id: Optional[int]) -> None:
        if not sale_id:
            return
        from database.models.purchase import Purchase
        with get_session() as s:
            p = s.get(Purchase, int(purchase_id))
            if p is not None:
                p.linked_sale_id = int(sale_id)

    @staticmethod
    def cancel_pending_quick_purchase(purchase_id: int) -> Dict[str, Any]:
        """
        Fully removes a just-committed Quick Purchase that was never
        actually confirmed - specifically, the operator explicitly
        cancelling the checkout dialog before the sale itself was ever
        attempted, rather than a genuine SaleService.record() failure.

        This is deliberately narrower than the "dangling purchase" case
        execute() already handles: a genuine failure AFTER the operator
        confirmed checkout means real goods were genuinely bought with
        real intent, and stay reported honestly, not silently erased (see
        this module's own docstring). But an explicit cancellation, before
        the sale was ever attempted at all, means the entire reason for
        buying those goods - fulfilling THIS specific customer's order -
        was withdrawn before anything else happened. Nothing should have
        changed at all in that case, so this fully removes it: the stock
        movement, the purchase item, and the purchase itself.

        Refuses (raises) rather than silently deleting if a payment
        somehow already exists against this purchase - that would mean
        real money changed hands, which this function must never erase.
        """
        from database.models.purchase import Purchase
        from database.models.purchase_item import PurchaseItem
        from database.models.payment import Payment

        purchase_id = int(purchase_id or 0)
        if purchase_id <= 0:
            return {"ok": False, "error": "purchase_id required"}

        with get_session() as s:
            purchase = s.get(Purchase, purchase_id)
            if purchase is None:
                return {"ok": True, "already_gone": True}

            payment_count = (
                s.query(Payment)
                .filter(Payment.entity_type == "purchase", Payment.entity_id == purchase_id)
                .count()
            )
            if payment_count > 0:
                raise ValueError(
                    f"Refusing to cancel Quick Purchase #{purchase_id} - a "
                    f"payment already exists against it, meaning real money "
                    f"has already changed hands."
                )

            item_ids = [
                int(pid) for (pid,) in
                s.query(PurchaseItem.id).filter(PurchaseItem.purchase_id == purchase_id).all()
            ]

            removed_movements = 0
            if item_ids:
                # The real convention this module already documents:
                # reference_type="purchase_item" with reference_id pointing
                # at the PurchaseItem's own id, not the Purchase's.
                removed_movements = (
                    s.query(StockMovement)
                    .filter(
                        StockMovement.reference_type == "purchase_item",
                        StockMovement.reference_id.in_(item_ids),
                    )
                    .delete(synchronize_session=False)
                )

            removed_items = (
                s.query(PurchaseItem)
                .filter(PurchaseItem.purchase_id == purchase_id)
                .delete(synchronize_session=False)
            )

            s.delete(purchase)

        return {
            "ok": True,
            "purchase_id": purchase_id,
            "stock_movements_removed": removed_movements,
            "purchase_items_removed": removed_items,
        }