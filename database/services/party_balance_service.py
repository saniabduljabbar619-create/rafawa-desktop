# database\services\party_balance_service.py

from __future__ import annotations
from sqlalchemy import func, case
from database.session import get_session
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.purchase_return import PurchaseReturn
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.sale_return import SaleReturn
from database.models.sale_discount import SaleDiscount
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment


class PartyBalanceService:

    @staticmethod
    def compute(party_id: int, session_user_id: int, role: str) -> dict:

        party_id = int(party_id or 0)

        # 🚧 THE BRIDGE: Initializing distinct keys for IN and OUT balances
        result = {
            "crops": {
                "receivable": 0.0,
                "payable": 0.0,
                "advance_in": 0.0,   # Customer deposits (We owe them product/cash)
                "advance_out": 0.0,  # Inputs given out (They owe us product/cash)
                "net_position": 0.0,
                "paid": 0.0,
                "received": 0.0
            },
            "fertilizers": {
                "receivable": 0.0,
                "payable": 0.0,
                "advance_in": 0.0,
                "advance_out": 0.0,
                "net_position": 0.0,
                "paid": 0.0,
                "received": 0.0
            },
        }

        if party_id <= 0:
            return result

        with get_session() as s:

            # ======================================================
            # PURCHASE TOTALS (single aggregated query)
            # ======================================================
            # Returns are pre-summed in their OWN subquery (one row per
            # purchase_id) before being joined in - joining PurchaseItem
            # (one-to-many) and PurchaseReturn (also one-to-many) directly
            # off the same Purchase in one query fans them out into a
            # cartesian product per purchase (N items x M returns = N*M
            # rows), which silently multiplied func.sum(PurchaseReturn.
            # amount) by the purchase's own line-item count - e.g. a
            # 3-item purchase with 3 returns summed the true return value
            # 3x over. Pre-aggregating first means this join can only ever
            # attach at most one return-total row per purchase, so the
            # later join to PurchaseItem can no longer multiply it.
            purchase_returns_subq = (
                s.query(
                    PurchaseReturn.purchase_id.label("purchase_id"),
                    func.sum(PurchaseReturn.amount).label("returns"),
                )
                .group_by(PurchaseReturn.purchase_id)
                .subquery()
            )

            # Same pre-aggregation reasoning as purchase_returns_subq above -
            # without grouping first, joining PurchasePriceAdjustment
            # directly would get multiplied by the PurchaseItem join's own
            # line count for any purchase with more than one product line.
            purchase_adjustments_subq = (
                s.query(
                    PurchasePriceAdjustment.purchase_id.label("purchase_id"),
                    func.sum(PurchasePriceAdjustment.amount).label("adjustments"),
                )
                .group_by(PurchasePriceAdjustment.purchase_id)
                .subquery()
            )

            purchase_rows = (
                s.query(
                    Purchase.id,
                    Purchase.total_amount,
                    func.max(
                        case(
                            (PurchaseItem.product_type_snapshot.in_(("fertilizer", "fertilizers")), 1),
                            else_=0
                        )
                    ).label("has_fert"),
                    func.coalesce(func.max(purchase_returns_subq.c.returns), 0).label("returns"),
                    func.coalesce(func.max(purchase_adjustments_subq.c.adjustments), 0).label("adjustments")
                )
                .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
                .outerjoin(purchase_returns_subq, purchase_returns_subq.c.purchase_id == Purchase.id)
                .outerjoin(purchase_adjustments_subq, purchase_adjustments_subq.c.purchase_id == Purchase.id)
                .filter(Purchase.supplier_party_id == party_id)
                .filter(Purchase.status == "completed")
                .group_by(Purchase.id)
                .all()
            )

            purchase_map = {}
            for pid, total, has_fert, returns, adjustments in purchase_rows:
                # A price adjustment is a REAL, additional amount now
                # genuinely owed - e.g. a supplier renegotiating after
                # delivery - so it adds to what the purchase total was
                # already, the same way a return subtracts from it. Not
                # an assumption or a projection: this is what the
                # purchase actually, currently costs.
                amt = max(0.0, float(total or 0) - abs(float(returns or 0)) + float(adjustments or 0))
                ledger = "fertilizers" if int(has_fert or 0) else "crops"
                result[ledger]["payable"] += amt
                purchase_map[pid] = {
                    "ledger": ledger,
                    "total": amt,
                    "paid": 0.0
                }

            # ======================================================
            # SALES TOTALS
            # ======================================================
            # Same pre-aggregation fix as purchase_returns_subq above -
            # SaleItem and SaleReturn are both one-to-many off Sale, so
            # summing SaleReturn.amount directly in a query already joined
            # to SaleItem multiplies it by the sale's own item count.
            sale_returns_subq = (
                s.query(
                    SaleReturn.sale_id.label("sale_id"),
                    func.sum(SaleReturn.amount).label("returns"),
                )
                .group_by(SaleReturn.sale_id)
                .subquery()
            )

            sale_rows = (
                s.query(
                    Sale.id,
                    Sale.total_amount,
                    func.max(
                        case(
                            (SaleItem.product_type_snapshot.in_(("fertilizer", "fertilizers")), 1),
                            else_=0
                        )
                    ).label("has_fert"),
                    func.coalesce(func.max(sale_returns_subq.c.returns), 0).label("returns")
                )
                .join(SaleItem, SaleItem.sale_id == Sale.id)
                .outerjoin(sale_returns_subq, sale_returns_subq.c.sale_id == Sale.id)
                .filter(Sale.buyer_party_id == party_id)
                .filter(Sale.status == "completed")
                .group_by(Sale.id)
                .all()
            )

            sale_discount_rows = (
                s.query(SaleDiscount.sale_id, func.coalesce(func.sum(SaleDiscount.amount), 0))
                .group_by(SaleDiscount.sale_id)
                .all()
            )
            sale_discounts_by_id = {int(sid): float(amt or 0) for sid, amt in sale_discount_rows}

            sale_map = {}
            for sid, total, has_fert, returns in sale_rows:
                discount = sale_discounts_by_id.get(int(sid), 0.0)
                amt = max(0.0, float(total or 0) - abs(float(returns or 0)) - discount)
                ledger = "fertilizers" if int(has_fert or 0) else "crops"
                result[ledger]["receivable"] += amt
                sale_map[sid] = {
                    "ledger": ledger,
                    "total": amt,
                    "received": 0.0
                }

            # ======================================================
            # ADVANCE ALLOCATIONS - applying an advance to a specific
            # purchase/sale never creates a Payment row at all (it's
            # recorded ONLY in AdvanceAllocation), so without this query
            # that settlement is completely invisible to the calculation
            # below - a sale genuinely, fully paid via a mix of advance
            # and cash would still show most of its total as outstanding.
            # ======================================================
            alloc_rows = (
                s.query(
                    AdvanceAllocation.entity_type,
                    AdvanceAllocation.entity_id,
                    func.sum(AdvanceAllocation.amount)
                )
                .join(Advance, Advance.id == AdvanceAllocation.advance_id)
                .filter(Advance.party_id == party_id)
                .group_by(AdvanceAllocation.entity_type, AdvanceAllocation.entity_id)
                .all()
            )
            for et, eid, amt in alloc_rows:
                et = (et or "").lower()
                amt = float(amt or 0)
                if et == "purchase" and eid in purchase_map:
                    purchase_map[eid]["paid"] += amt
                elif et == "sale" and eid in sale_map:
                    sale_map[eid]["received"] += amt

            # ======================================================
            # PAYMENTS (single query)
            # ======================================================
            # Payment.method == "advance" rows are created ALONGSIDE the
            # AdvanceAllocation row for the same settlement (see
            # PaymentCenter.handle_sale_creation/handle_purchase_creation) -
            # kept for visibility elsewhere (party_ledger_service.py
            # already excludes these too, for the exact same reason), but
            # counting both here double-counts a single settlement event,
            # capping "applied" at the full transaction total and hiding
            # the real remaining balance entirely.
            payments = (
                s.query(
                    Payment.entity_type,
                    Payment.entity_id,
                    Payment.direction,
                    func.sum(Payment.amount)
                )
                .filter(Payment.party_id == party_id)
                .filter(Payment.status != "voided")
                .filter(Payment.method != "advance")
                .group_by(
                    Payment.entity_type,
                    Payment.entity_id,
                    Payment.direction
                )
                .all()
            )

            for et, eid, direction, amt in payments:
                et = (et or "").lower()
                direction = (direction or "").lower()
                amt = float(amt or 0)

                if et == "purchase" and direction == "out":
                    if eid in purchase_map:
                        purchase_map[eid]["paid"] += amt

                elif et == "sale" and direction == "in":
                    if eid in sale_map:
                        sale_map[eid]["received"] += amt

                # 🚧 THE BRIDGE: Payments modifying independent gross buckets
                elif et == "advance":
                    adv = s.query(Advance).filter(Advance.id == eid).first()
                    if not adv:
                        continue

                    ledger = (adv.ledger or "crops").lower()
                    adv_direction = (adv.direction or "").lower()

                    if adv_direction == "received":
                        # Settlement directly drops our unredeemed Advance IN commitment
                        result[ledger]["advance_in"] -= amt
                    elif adv_direction == "given":
                        # Settlement directly drops their outstanding Advance OUT debt
                        result[ledger]["advance_out"] -= amt

            # ======================================================
            # APPLY PURCHASE PAYMENTS
            # ======================================================
            for p in purchase_map.values():
                ledger = p["ledger"]
                total = float(p["total"] or 0)
                paid = float(p["paid"] or 0)

                applied = min(paid, total)
                balance = total - applied
                result[ledger]["payable"] -= applied

                if balance > 0:
                    result[ledger]["paid"] += applied

            # ======================================================
            # APPLY SALE PAYMENTS 
            # ======================================================
            for srow in sale_map.values():
                ledger = srow["ledger"]
                total = float(srow["total"] or 0)
                received = float(srow["received"] or 0)

                applied = min(received, total)
                balance = total - applied
                result[ledger]["receivable"] -= applied

                if balance > 0:
                    result[ledger]["received"] += applied
                    
            # ======================================================
            # ADVANCES (Remaining exposure - real allocations only)
            # ======================================================
            # This used to also net an advance's raw remaining straight
            # out of payable/receivable for display, even when nothing
            # had actually been applied anywhere (no AdvanceAllocation,
            # no Payment). That produced a summary "Payable" figure that
            # no other view agreed with - the Purchase Payment dialog and
            # the Advance Settlement Hub both read only real records, so
            # they kept showing the full, un-netted amounts. An advance
            # now only reduces payable/receivable here once it has
            # genuinely been allocated (AdvanceAllocation rows, already
            # folded into purchase_map/sale_map "paid"/"received" above)
            # or paid down directly (handled in the PAYMENTS loop above).
            # Anything still unapplied just stands as its own exposure.
            advances = (
                s.query(
                    Advance.id,
                    Advance.direction,
                    Advance.ledger,
                    Advance.amount
                )
                .filter(
                    Advance.party_id == party_id,
                    Advance.status == "completed"
                )
                .all()
            )

            for adv_id, direction, ledger, amount in advances:
                ledger = (ledger or "crops").lower()
                amount = float(amount or 0)

                allocated = (
                    s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                    .filter(AdvanceAllocation.advance_id == adv_id)
                    .scalar()
                )

                remaining = amount - float(allocated or 0)
                if remaining <= 0:
                    continue

                if direction == "given":
                    result[ledger]["advance_out"] += remaining
                elif direction == "received":
                    result[ledger]["advance_in"] += remaining

        # ======================================================
        # FINAL NET POSITION & AGGREGATION
        # ======================================================
        overall = {
            "receivable": 0.0,
            "payable": 0.0,
            "advance_in": 0.0,   # Total Gross Advance IN
            "advance_out": 0.0,  # Total Gross Advance OUT
            "net_position": 0.0,
            "paid": 0.0,
            "received": 0.0
        }

        for ledger in ("crops", "fertilizers"):
            pay = float(result[ledger]["payable"] or 0)
            rec = float(result[ledger]["receivable"] or 0)

            if pay <= 0:
                result[ledger]["paid"] = 0.0
            if rec <= 0:
                result[ledger]["received"] = 0.0

            net = rec - pay
            result[ledger]["net_position"] = net

            # 🚧 THE BRIDGE: Aggregating individual channels cleanly
            overall["receivable"] += rec
            overall["received"] += float(result[ledger]["received"] or 0)
            overall["payable"] += pay
            overall["paid"] += float(result[ledger]["paid"] or 0)
            
            overall["advance_in"] += float(result[ledger]["advance_in"] or 0)
            overall["advance_out"] += float(result[ledger]["advance_out"] or 0)
            overall["net_position"] += net

        result["overall"] = overall
        return result