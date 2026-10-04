# database/services/sale_service.py

from __future__ import annotations

from typing import List, Dict, Optional
from datetime import datetime

from sqlalchemy import func, case

from database.session import get_session
from database.services.audit_service import AuditService
from shared.auth.permissions import require_admin
from shared.config.constants import WALKIN_PARTY_ID
from database.services.walkin_account import is_shared_walkin
from database.models.product import Product
from database.models.stock_movement import StockMovement, STOCK_IN_TYPES, STOCK_OUT_TYPES
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.party import Party
from database.models.payment import Payment
from database.models.sale_return import SaleReturn


class SaleService:

    # A real crop bag is never a handful of kg - it's always in this
    # ballpark (client's own figures: "not below 90kg... up to 120kg").
    # This is a SANITY range, not a hard business rule - it exists purely
    # to catch the case where accumulated kg/bag mismatches in
    # StockMovement history have dragged the average to something
    # physically impossible (see get_bag_average_weight's "plausible"
    # flag). Tune these two numbers to your real-world range if it's
    # different, or per-crop later if different crops genuinely differ.
    BAG_WEIGHT_SANITY_MIN_KG = 60.0
    BAG_WEIGHT_SANITY_MAX_KG = 150.0

    # -------------------------------------------------
    # PUBLIC: PRODUCT LOOKUP
    # -------------------------------------------------

    @staticmethod
    def get_product_for_sale(product_id: int) -> Optional[Dict]:

        with get_session() as s:

            row = (
                s.query(
                    Product.id,
                    Product.name,
                    Product.sku,
                    Product.product_type,
                    Product.default_cost_rate,
                    Product.default_sale_rate
                )
                .filter(Product.id == int(product_id))
                .first()
            )

            if not row:
                return None

            return {
                "id": row.id,
                "name": row.name,
                "sku": row.sku,
                "product_type": str(row.product_type or "").lower(),
                "cost_rate": float(row.default_cost_rate or 0),
                "sale_rate": float(row.default_sale_rate or 0),
            }

    @staticmethod
    def list_products_for_sale(q: str = "", limit: int = 200,
                               include_zero_stock: bool = False) -> List[Dict]:

        q = (q or "").strip().lower()
        limit = max(1, min(int(limit or 200), 2000))

        stock_kg, stock_bags = SaleService._stock_expr()

        with get_session() as s:

            rows = (
                s.query(
                    Product.id,
                    Product.name,
                    Product.sku,
                    Product.product_type,
                    Product.default_cost_rate,
                    Product.default_sale_rate,
                    Product.is_active,
                    stock_kg,
                    stock_bags,
                )
                .outerjoin(StockMovement, StockMovement.product_id == Product.id)
                .filter(Product.is_active.is_(True))
                .group_by(Product.id)
                .order_by(Product.id.desc())
            )

            # Search / zero-stock filtering happens below in Python (it
            # needs the per-type stock figure), so the LIMIT must come AFTER
            # it - applying it in SQL first silently dropped matches that
            # fell outside the newest `limit` products.
            rows = rows.all()

        out = []

        for r in rows:

            if not r.is_active:
                continue

            ptype = str(r.product_type or "").strip().lower()

            if ptype in ("fertilizer", "fertilizers"):
                stock = float(r.stock_bags or 0)
            else:
                stock = float(r.stock_kg or 0)

            # Hidden from the normal Add-Item picker by default (the client's
            # own request: don't let an operator select something that isn't
            # there). The standalone "Quick Purchase" button needs the OPPOSITE
            # - it exists specifically to find what's missing - so it passes
            # include_zero_stock=True to see everything, including 0-stock
            # items, while the everyday sale flow stays clean.
            if stock <= 0 and not include_zero_stock:
                continue

            hay = f"{r.name} {r.sku} {ptype}".lower()
            if q and q not in hay:
                continue

            sale_rate = float(r.default_sale_rate or 0)
            cost_rate = float(r.default_cost_rate or 0)

            if sale_rate <= 0:
                sale_rate = cost_rate

            out.append({
                "id": r.id,
                "name": r.name,
                "sku": r.sku,
                "product_type": ptype,
                "stock": stock,
                "cost_rate": cost_rate,
                "sale_rate": sale_rate,
            })

            if len(out) >= limit:
                break

        return out

    # -------------------------------------------------
    # PUBLIC: PER-BAG AVERAGE WEIGHT (crops sold "by the bag")
    # -------------------------------------------------

    @staticmethod
    def get_bag_average_weight(product_id: int) -> dict:
        """
        For crops that a cashier wants to sell "by the bag" instead of by
        the kg (e.g. "5 bags of masara at 42,000 each") - the bag itself
        has never had a fixed weight in this system (SaleItemBag/PurchaseItem
        track qty_bags and total_weight_kg completely separately, on purpose,
        because real bag weights vary intake to intake). So "average weight
        per bag" is only ever a snapshot of CURRENT stock, computed fresh
        at sale time - never stored, never assumed:

            avg_kg_per_bag = current_stock_kg / current_stock_bags

        This is the SAME stock arithmetic _stock_expr()/list_products_for_sale
        already use, just scoped to one product and returned directly
        instead of via a table.

        IMPORTANT: this is an ALL-TIME cumulative average across every
        StockMovement ever recorded for the product, not a per-batch or
        per-intake figure. Because kg and bags are tracked as two
        genuinely independent counters (never enforced to move together),
        a single historical entry where they went out of sync - a typo,
        a per-kg sale with the wrong bag count typed in, a mis-recorded
        adjustment - permanently skews this average until enough correct
        entries dilute it back out, which can be a very long time or
        never. A real crop bag is never a handful of kg, so a wildly low
        (or high) result here is a signal of a data problem upstream, not
        a real bag weight - see "plausible" below.

        Returns:
            {
                "product_id": int,
                "stock_kg": float,      # current kg on hand
                "stock_bags": int,      # current bags on hand
                "avg_kg_per_bag": float,# 0.0 if bags on hand is 0 (can't average)
                "plausible": bool,      # False if outside BAG_WEIGHT_SANITY_* range
            }

        Callers MUST check avg_kg_per_bag > 0 before using it - a product
        with kg on hand but zero bags on hand (or vice versa - a data/entry
        inconsistency) genuinely cannot be sold "by the bag" right now, and
        should fall back to the normal per-kg flow instead of dividing by
        zero or silently guessing.

        Callers should also check "plausible" and, if False, NOT act on
        avg_kg_per_bag automatically - surface it to a human (manual
        override / investigate the data) rather than trusting a number
        that almost certainly doesn't reflect a real bag.
        """
        product_id = int(product_id or 0)

        stock_kg, stock_bags = SaleService._stock_expr()

        with get_session() as s:
            row = (
                s.query(stock_kg, stock_bags)
                .select_from(StockMovement)
                .filter(StockMovement.product_id == product_id)
                .first()
            )

        kg = float(row.stock_kg or 0) if row else 0.0
        bags = float(row.stock_bags or 0) if row else 0.0

        avg = (kg / bags) if bags > 0 else 0.0

        plausible = (
            avg > 0
            and SaleService.BAG_WEIGHT_SANITY_MIN_KG <= avg <= SaleService.BAG_WEIGHT_SANITY_MAX_KG
        )

        return {
            "product_id": product_id,
            "stock_kg": kg,
            "stock_bags": bags,
            "avg_kg_per_bag": avg,
            "plausible": plausible,
        }

    @staticmethod
    def price_by_bag(product_id: int, qty_bags: int, price_per_bag: float,
                      override_avg_kg_per_bag: float | None = None) -> dict:
        """
        Turns a cashier's "N bags at X per bag" into the kg-anchored shape
        SaleService.record()/SaleItem already require, WITHOUT changing
        either of those - record() only ever consumes qty_bags,
        total_weight_kg and a per-kg unit_price, and stays completely
        unaware of how those numbers were arrived at.

        The amount the customer actually agreed to pay (qty_bags *
        price_per_bag) is preserved EXACTLY as line_total; unit_price
        (per kg) is only ever back-derived from it so that
        total_weight_kg * unit_price reproduces that exact total - never
        the other way around, which would let rounding drift the amount
        the customer was quoted.

        override_avg_kg_per_bag: if provided, this value is used directly
        INSTEAD of the computed stock average, and the DB lookup is
        skipped entirely. This is the escape hatch for when
        get_bag_average_weight() reports plausible=False - a human
        (the cashier, or whoever knows this product's real bag weight)
        supplies the true figure instead of trusting a number that's
        almost certainly corrupted by historical kg/bag drift. Still
        validated against the same sanity range - an override doesn't
        bypass sanity, it just replaces the SOURCE of the number.

        Raises ValueError if:
        - no override was given, AND this product currently has no bags
          on hand to average from (see get_bag_average_weight), OR
        - no override was given, AND the computed average is outside the
          sanity range (caller must either pass an override or handle
          this case explicitly - it is never silently used).
        """
        qty_bags = int(qty_bags or 0)
        price_per_bag = float(price_per_bag or 0)

        if qty_bags <= 0:
            raise ValueError("qty_bags must be > 0")
        if price_per_bag <= 0:
            raise ValueError("price_per_bag must be > 0")

        if override_avg_kg_per_bag is not None:
            avg = float(override_avg_kg_per_bag)
            if avg <= 0:
                raise ValueError("override_avg_kg_per_bag must be > 0")
        else:
            avg_info = SaleService.get_bag_average_weight(product_id)
            avg = avg_info["avg_kg_per_bag"]

            if avg <= 0:
                raise ValueError(
                    "Cannot sell by the bag - no bags currently on hand for "
                    "this product to average a weight from. Use per-kg pricing "
                    "instead, or record stock for it first."
                )

            if not avg_info["plausible"]:
                raise ValueError(
                    f"Computed average weight per bag ({avg:,.2f} kg) looks "
                    f"wrong - outside the expected "
                    f"{SaleService.BAG_WEIGHT_SANITY_MIN_KG:.0f}-"
                    f"{SaleService.BAG_WEIGHT_SANITY_MAX_KG:.0f} kg range. This "
                    "usually means past stock entries for this product have "
                    "mismatched bag counts vs kg somewhere in their history. "
                    "Enter the real known bag weight manually to proceed, or "
                    "sell by kg instead."
                )

        total_weight_kg = qty_bags * avg
        line_total = qty_bags * price_per_bag
        unit_price = line_total / total_weight_kg  # per-kg equivalent, for SaleItem

        return {
            "qty_bags": qty_bags,
            "total_weight_kg": total_weight_kg,
            "unit_price": unit_price,          # per kg - what SaleItem/record() expect
            "price_per_bag": price_per_bag,     # for display/receipt purposes
            "avg_kg_per_bag": avg,
            "line_total": line_total,
        }

    # -------------------------------------------------
    # RECORD SALE
    # -------------------------------------------------

    @staticmethod
    def record(payload: dict) -> dict:

        items = payload.get("items") or []
        if not items:
            raise ValueError("Sale items required")

        created_by_user_id = int(payload.get("created_by_user_id") or 0)
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id required")

        discount = float(payload.get("discount") or 0)
        note = str(payload.get("note") or "")

        with get_session() as s:

            buyer_party_id = int(payload.get("buyer_party_id") or 0)
            walkin_name = str(payload.get("walkin_name") or "").strip()

            # Global walk-in party
            if buyer_party_id <= 0:
                buyer_party_id = WALKIN_PARTY_ID
                if not is_shared_walkin(buyer_party_id, s):
                    # The reserved walk-in id now belongs to a real customer: refuse rather than
                    # quietly post an anonymous sale to that customer's account.
                    raise ValueError(
                        f"The shared walk-in account (party id {WALKIN_PARTY_ID}) is not set up - that id "
                        "belongs to a registered customer. Choose a customer for this sale.")

            # A real sale to this party is proof they ARE a customer, whoever
            # they were before - a supplier bought from for months can be sold
            # to today, and this is the moment that becomes true. id 11 is the
            # shared anonymous bucket, not a real counterparty, so it's exempt.
            is_walkin = is_shared_walkin(buyer_party_id, s)
            if not is_walkin:
                SaleService._ensure_role(buyer_party_id, "is_customer")

            product_ids = [int(it["product_id"]) for it in items]

            rows = (
                s.query(Product.id, Product.product_type, Product.default_cost_rate)
                .filter(Product.id.in_(product_ids))
                .all()
            )

            ptype_by_id = {
                int(r.id): str(r.product_type or "").lower()
                for r in rows
            }

            # best cost known RIGHT NOW - frozen onto each line below
            cost_by_id = {
                int(r.id): float(r.default_cost_rate or 0.0)
                for r in rows
            }

            SaleService._check_stock(s, items, ptype_by_id)

            subtotal = SaleService._compute_subtotal(items, ptype_by_id)

            total_amount = float(payload.get("total") or (subtotal - discount))

            sale = Sale(
                buyer_party_id=buyer_party_id,
                walkin_name=walkin_name if is_walkin else None,
                status="completed",
                notes=note,
                subtotal=subtotal,
                discount=discount,
                total_amount=total_amount,
                created_by_user_id=created_by_user_id,
                completed_at=datetime.now(),
            )

            s.add(sale)
            s.flush()

            sale_item_ids = []

            for it in items:

                pid = int(it["product_id"])
                ptype = ptype_by_id[pid]

                qty_bags = int(it["qty_bags"])
                unit_price = float(it.get("unit_price") or 0)

                if ptype == "fertilizer":
                    tw = 0
                    line_total = qty_bags * unit_price
                else:
                    tw = float(it["total_weight_kg"])
                    line_total = tw * unit_price

                # A caller that knows the TRUE cost of these goods (Quick
                # Purchase, buying from a dealer at market price) passes
                # unit_cost; everyone else freezes today's default.
                if it.get("unit_cost") is not None:
                    unit_cost = float(it.get("unit_cost") or 0)
                else:
                    unit_cost = cost_by_id.get(pid, 0.0)

                # A genuine, per-line discount from the checkout screen's
                # itemized grid - kept separate from line_total, never baked
                # into it, so the original subtotal stays intact.
                line_discount = float(it.get("discount") or 0.0)
                # Can never exceed the line's own subtotal - the checkout
                # dialog already enforces this live, but this is the
                # authoritative, server-side guarantee.
                line_discount = max(0.0, min(line_discount, line_total))

                si = SaleItem(
                    sale_id=sale.id,
                    product_id=pid,
                    product_type_snapshot=ptype,
                    qty_bags=qty_bags,
                    total_weight_kg=tw,
                    unit_sale_rate=unit_price,
                    unit_cost_snapshot=unit_cost,
                    line_total=line_total,
                    line_discount=line_discount,
                    notes=note,
                )

                s.add(si)
                s.flush()

                sale_item_ids.append(si.id)

                mv = StockMovement(
                    product_id=pid,
                    movement_type="sale_out",
                    qty_bags=qty_bags,
                    total_weight_kg=tw,
                    unit_rate=unit_price,
                    reference_type="sale_item",
                    reference_id=si.id,
                    party_id=buyer_party_id,
                    created_by_user_id=created_by_user_id,
                )

                s.add(mv)

            s.commit()

            return {
                "sale_id": sale.id,
                "sale_item_ids": sale_item_ids,
                "reference": f"SAL-{sale.id}",
                "subtotal": subtotal,
                "discount": discount,
                "total_amount": total_amount,
            }

    # -------------------------------------------------
    # INTERNAL HELPERS
    # -------------------------------------------------

    

    @staticmethod
    def _compute_subtotal(items, ptype_by_id):

        subtotal = 0

        for it in items:

            pid = int(it["product_id"])
            ptype = ptype_by_id[pid]

            unit_price = float(it.get("unit_price") or 0)

            if ptype == "fertilizer":
                subtotal += int(it["qty_bags"]) * unit_price
            else:
                subtotal += float(it["total_weight_kg"]) * unit_price

        return subtotal

    @staticmethod
    def _check_stock(session, items, ptype_by_id):

        need_kg = {}
        need_bags = {}

        for it in items:

            pid = int(it["product_id"])
            ptype = ptype_by_id[pid]

            qty_bags = int(it["qty_bags"])

            if qty_bags <= 0:
                raise ValueError("qty_bags must be > 0")

            need_bags[pid] = need_bags.get(pid, 0) + qty_bags

            if ptype != "fertilizer":

                tw = float(it["total_weight_kg"])

                if tw <= 0:
                    raise ValueError("total_weight_kg must be > 0")

                need_kg[pid] = need_kg.get(pid, 0) + tw

        stock_kg, stock_bags = SaleService._stock_expr()

        for pid, req_bags in need_bags.items():

            avail_kg, avail_bags = (
                session.query(stock_kg, stock_bags)
                .select_from(StockMovement)
                .filter(StockMovement.product_id == pid)
                .one()
            )
            avail_kg = float(avail_kg or 0)
            avail_bags = float(avail_bags or 0)

            if avail_bags < req_bags:
                name = session.query(Product.name).filter(Product.id == pid).scalar() or f"Product#{pid}"
                raise ValueError(
                    f"Insufficient stock for {name}: need {req_bags} bag(s), "
                    f"only {avail_bags:,.0f} on hand."
                )

            # Crops are dual-tracked: the weight sold must also be covered by
            # the kg actually on hand. (need_kg was computed above but never
            # checked, so a sale could take more kg than the shop held as
            # long as enough bags were left.) Small tolerance for float noise.
            req_kg = need_kg.get(pid, 0.0)
            if req_kg > 0 and avail_kg + 1e-6 < req_kg:
                name = session.query(Product.name).filter(Product.id == pid).scalar() or f"Product#{pid}"
                raise ValueError(
                    f"Insufficient stock for {name}: need {req_kg:,.2f} kg, "
                    f"only {avail_kg:,.2f} kg on hand."
                )

    @staticmethod
    def _stock_expr():

        stock_kg = func.coalesce(
            func.sum(
                case(
                    (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.total_weight_kg),
                    (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.total_weight_kg),
                    else_=0,
                )
            ),
            0
        ).label("stock_kg")

        stock_bags = func.coalesce(
            func.sum(
                case(
                    (StockMovement.movement_type.in_(STOCK_IN_TYPES), StockMovement.qty_bags),
                    (StockMovement.movement_type.in_(STOCK_OUT_TYPES), -StockMovement.qty_bags),
                    else_=0,
                )
            ),
            0
        ).label("stock_bags")

        return stock_kg, stock_bags
    @staticmethod
    def _ensure_role(party_id: int, flag_name: str) -> None:
        """
        Idempotently flips one capability flag true on a party, in its own
        short transaction (same pattern QuickPurchaseService.link_sale uses).
        Never overwrites party_type, never touches the OTHER flag - a party
        can be both is_customer and is_supplier at once, on purpose.

        Skips walk-in-type parties entirely - a walk-in is deliberately NOT
        a registered customer/supplier, and must never appear in those lists.
        The caller's own `party_id != 11` check only ever protected the old
        shared anonymous bucket; get_or_lazy_create_walkin creates a REAL,
        separate Party per walk-in name (never id 11), so that check alone
        let every one of those leak into the Customers/Suppliers list the
        moment a sale or purchase touched them. Checking party_type here
        catches both mechanisms, regardless of which one created the row.
        """
        with get_session() as s:
            p = s.get(Party, int(party_id))
            if p is None or str(p.party_type or "") == "walkin":
                return
            if not getattr(p, flag_name, True):
                setattr(p, flag_name, True)

    # -------------------------------------------------
    # VOID SALE (Case B: the transaction itself, before any payment
    # exists against it - mistake correction, keeps history)
    # -------------------------------------------------
    @staticmethod
    @require_admin
    def void_sale(sale_id: int, voided_by_user_id: int, reason: str = "") -> Dict:
        """
        Voids a sale transaction that was entered wrong from the start
        (wrong product, wrong quantity, wrong buyer entirely) - not a
        payment problem, the transaction itself. Refuses the moment ANY
        payment exists against it, even a voided one's original existence
        - once real money has been recorded here, correcting it is
        PaymentService.void_payment's job on that payment first, not this.

        Never deletes or edits the original SaleItem/StockMovement rows in
        place: reverses the stock this sale removed by appending an
        offsetting movement for each line, the same append-only convention
        already used for void_payment, void_advance, and PurchaseService.
        void_purchase. The sale itself then gets status="voided" - stays
        visible in history forever, but drops out of every balance and
        stock calculation from this point on.
        """
        sale_id = int(sale_id or 0)
        voided_by_user_id = int(voided_by_user_id or 0)
        if sale_id <= 0:
            raise ValueError("sale_id is required.")
        if voided_by_user_id <= 0:
            raise ValueError("voided_by_user_id is required.")

        with get_session() as s:
            sale = s.get(Sale, sale_id)
            if sale is None:
                raise ValueError("Sale not found.")
            if sale.status == "voided":
                return {"ok": True, "sale_id": sale_id, "already_voided": True}

            has_payment = (
                s.query(Payment)
                .filter(Payment.entity_type == "sale", Payment.entity_id == sale_id)
                .first()
            )
            if has_payment is not None:
                raise ValueError(
                    "Cannot void this sale - a payment already exists "
                    "against it (even if that payment has itself been "
                    "voided). Void the specific payment(s) first if they're "
                    "wrong; this action is only for a transaction that was "
                    "never paid against at all."
                )

            has_return = (
                s.query(SaleReturn.id)
                .filter(SaleReturn.sale_id == sale_id)
                .first()
            )
            if has_return is not None:
                raise ValueError(
                    "Cannot void this sale - goods have already been returned "
                    "against it. Voiding would put the returned stock back a "
                    "second time."
                )

            items = s.query(SaleItem).filter(SaleItem.sale_id == sale_id).all()
            reversed_movements = 0
            for item in items:
                movements = (
                    s.query(StockMovement)
                    .filter(StockMovement.reference_type == "sale_item",
                           StockMovement.reference_id == item.id)
                    .all()
                )
                for mv in movements:
                    # sale_out -> adjustment_in (stock coming back in,
                    # correcting the mistake); anything else already
                    # represents an inflow, so its reversal is an outflow.
                    reversal_type = "adjustment_in" if mv.movement_type == "sale_out" else "adjustment_out"
                    s.add(StockMovement(
                        product_id=mv.product_id,
                        movement_type=reversal_type,
                        qty_bags=mv.qty_bags,
                        total_weight_kg=mv.total_weight_kg,
                        unit_rate=mv.unit_rate,
                        reference_type="sale_item",
                        reference_id=item.id,
                        party_id=mv.party_id,
                        created_by_user_id=voided_by_user_id,
                    ))
                    reversed_movements += 1

            sale.status = "voided"
            if reason:
                sale.notes = ((sale.notes or "") + f" | [Voided: {reason}]").strip()
            AuditService.record(
                "sale.void", "sale", sale_id,
                f"Voided sale of {float(sale.total_amount or 0):,.2f}",
                {"reason": reason, "total": float(sale.total_amount or 0),
                 "buyer_party_id": sale.buyer_party_id, "stock_movements_reversed": reversed_movements},
                session=s)

        return {"ok": True, "sale_id": sale_id, "stock_movements_reversed": reversed_movements}

    @staticmethod
    def list_activity_with_shortages(party_id: int, limit: int = 300) -> List[Dict]:
        """
        Every sale for this party, regardless of payment status - unlike
        list_voidable_sales, this deliberately INCLUDES fully-paid ones.
        What the Counter Shortage flow's row picker shows: the exact
        scenario the feature exists for (a cash-handling mistake
        discovered only after a full payment already made the row vanish
        from the normal "needs payment" table) needs a fully-paid row to
        still be selectable here. Each row carries its own open shortage
        amount, if any, so the picker can color-code which ones already
        have an unresolved correction against them.
        """
        party_id = int(party_id or 0)
        with get_session() as s:
            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == party_id)
                .filter(Sale.status != "voided")
                .order_by(Sale.created_at.desc())
                .limit(limit)
                .all()
            )
            sale_ids = [sl.id for sl in sales]
            paid_by_id: Dict[int, float] = {}
            if sale_ids:
                for eid, amt in (
                    s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                    .filter(Payment.entity_type == "sale", Payment.entity_id.in_(sale_ids))
                    .filter(Payment.status != "voided")
                    .group_by(Payment.entity_id).all()
                ):
                    paid_by_id[int(eid)] = float(amt or 0)

            # ---- product summary, same pattern as DrawerService.party_transactions ----
            products = {int(p.id): (p.name or f"Product#{p.id}", p.product_type or "")
                       for p in s.query(Product).all()}
            sale_lines: Dict[int, List[int]] = {}
            if sale_ids:
                for si in s.query(SaleItem).filter(SaleItem.sale_id.in_(sale_ids)).all():
                    sale_lines.setdefault(int(si.sale_id), []).append(int(si.product_id))

            def _product_summary(product_ids: List[int]) -> str:
                if not product_ids:
                    return ""
                names = [products.get(pid, (f"Product#{pid}", ""))[0] for pid in product_ids]
                types = [products.get(pid, ("", ""))[1] for pid in product_ids]
                if len(names) == 1:
                    t = types[0].capitalize() if types[0] else ""
                    return f"{t} ({names[0]})" if t else names[0]
                extra = len(names) - 1
                return f"{names[0]} +{extra} more" if extra > 0 else names[0]

        from database.services.cash_shortage_service import CashShortageService
        open_shortages = CashShortageService.list_open_shortages(party_id, reference_type="sale")
        shortage_by_ref: Dict[int, Dict] = {int(sh["reference_id"]): sh for sh in open_shortages}

        return [
            {
                "id": int(sl.id),
                "reference": sl.sale_no or f"SAL-{sl.id}",
                "amount": float(sl.total_amount or 0),
                "paid": paid_by_id.get(int(sl.id), 0.0),
                "created_at": sl.created_at,
                "open_shortage": shortage_by_ref.get(int(sl.id)),
                "product_summary": _product_summary(sale_lines.get(int(sl.id), [])),
            }
            for sl in sales
        ]

    @staticmethod
    def list_voidable_sales(party_id: int, limit: int = 200) -> List[Dict]:
        """
        Sales for this party that void_sale would actually accept - no
        payment against them at all, and not already voided. What the
        Revert Mistakes tab's "Sale Transaction" option lists.
        """
        party_id = int(party_id or 0)
        with get_session() as s:
            sales = (
                s.query(Sale)
                .filter(Sale.buyer_party_id == party_id)
                .filter(Sale.status != "voided")
                .order_by(Sale.created_at.desc())
                .limit(limit)
                .all()
            )
            paid_ids = {
                int(eid) for (eid,) in
                s.query(Payment.entity_id)
                .filter(Payment.entity_type == "sale",
                       Payment.entity_id.in_([sl.id for sl in sales]))
                .distinct()
                .all()
            } if sales else set()

        return [
            {
                "id": int(sl.id),
                "reference": sl.sale_no or f"SAL-{sl.id}",
                "amount": float(sl.total_amount or 0),
                "notes": sl.notes or "",
                "created_at": sl.created_at,
            }
            for sl in sales if int(sl.id) not in paid_ids
        ]