# database/services/dashboard_service.py
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from datetime import date

from sqlalchemy import func, case
from sqlalchemy.orm import joinedload
from database.session import get_session
from database.services.document_balance import document_balances
from database.services.audit_service import AuditService
from shared.auth.permissions import require_admin

from database.models.stock_movement import StockMovement, STOCK_IN_TYPES, STOCK_OUT_TYPES
from database.models.product import Product
from database.models.payment import Payment
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.sale_return import SaleReturn
from database.models.sale_discount import SaleDiscount
from database.models.purchase_return import PurchaseReturn 
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.user import User
from database.models.party import Party
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.models.deletion_log import DeletionLog


class DeletionNotAllowed(Exception):
    """Raised when a delete is attempted without a valid acting admin user id."""
    pass

# Signed movement groups - local aliases for the shared, canonical
# classification in database/models/stock_movement.py, not an
# independent copy.
IN_MOVES = STOCK_IN_TYPES
OUT_MOVES = STOCK_OUT_TYPES


def _sum_stock_totals() -> Dict[str, float]:
    """
    Stock totals from stock_movements joined to products.product_type.
    - fertilizer: bags total
    - crops: bags + kg total
    """
    with get_session() as s:
        # Bags signed sum
        signed_bags = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.qty_bags, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.qty_bags, 0)),
            else_=0,
        )

        # Kg signed sum
        signed_kg = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.total_weight_kg, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.total_weight_kg, 0)),
            else_=0,
        )

        fert_bags = (
            s.query(func.coalesce(func.sum(signed_bags), 0))
            .join(Product, Product.id == StockMovement.product_id)
            .filter(Product.product_type == "fertilizer")
            .scalar()
        )

        crops_bags = (
            s.query(func.coalesce(func.sum(signed_bags), 0))
            .join(Product, Product.id == StockMovement.product_id)
            .filter(Product.product_type == "crops")
            .scalar()
        )

        crops_kg = (
            s.query(func.coalesce(func.sum(signed_kg), 0))
            .join(Product, Product.id == StockMovement.product_id)
            .filter(Product.product_type == "crops")
            .scalar()
        )

    return {
        "fertilizer_bags": float(fert_bags or 0),
        "crops_bags": float(crops_bags or 0),
        "crops_kg": float(crops_kg or 0),
    }


def _sum_advances_global(scope: str = "all") -> Dict[str, Dict[str, float]]:
    start, end = _resolve_scope(scope)

    advances = {
        "crops": {"in": 0.0, "out": 0.0},
        "fertilizers": {"in": 0.0, "out": 0.0},
    }

    with get_session() as s:
        query = s.query(
            Advance.direction,
            Advance.advance_type,
            Advance.ledger,
            func.coalesce(func.sum(func.coalesce(Advance.amount, 0)), 0)
        ).filter(Advance.status != "voided")

        if start:
            query = query.filter(Advance.created_at >= start)
        if end:
            query = query.filter(Advance.created_at <= end)

        rows = query.group_by(
            Advance.direction,
            Advance.advance_type,
            Advance.ledger
        ).all()

        for direction, adv_type, ledger, total in rows:
            direction = (direction or "").lower()
            adv_type = (adv_type or "").lower()
            ledger = (ledger or "").lower()
            total = float(total or 0)

            # Route ledger
            if adv_type == "crops":
                target = "crops"
            elif adv_type == "fertilizer":
                target = "fertilizers"
            elif adv_type == "money":
                target = "fertilizers" if ledger == "fertilizers" else "crops"
            else:
                continue

            if direction == "received":
                advances[target]["in"] += total
            elif direction == "given":
                advances[target]["out"] += total

    return advances


def _sum_paid_cashflow(scope: str = "all") -> Dict[str, float]:
    """
    Cashflow totals from payments table.
    Exclude entity_type='advance' and ledger transfer roles to protect metrics.
    """
    start, end = _resolve_scope(scope)
    with get_session() as s:
        query_in = s.query(func.coalesce(func.sum(func.coalesce(Payment.amount, 0)), 0)) \
            .filter(Payment.direction == "in") \
            .filter(Payment.entity_type != "advance") \
            .filter(Payment.method != "advance") \
            .filter(~Payment.notes.like("%Advance settlement%")) \
            .filter(Payment.status != "voided")

        if start:
            query_in = query_in.filter(Payment.created_at >= start)
        if end:
            query_in = query_in.filter(Payment.created_at <= end)
        paid_in = query_in.scalar()

        query_out = s.query(func.coalesce(func.sum(func.coalesce(Payment.amount, 0)), 0)) \
            .filter(Payment.direction == "out") \
            .filter(Payment.entity_type != "advance") \
            .filter(Payment.method != "advance") \
            .filter(~Payment.notes.like("%Advance settlement%")) \
            .filter(Payment.status != "voided")

        if start:
            query_out = query_out.filter(Payment.created_at >= start)
        if end:
            query_out = query_out.filter(Payment.created_at <= end)

        paid_out = query_out.scalar()

    paid_in = float(paid_in or 0)
    paid_out = float(paid_out or 0)
    return {"paid_in": paid_in, "paid_out": paid_out, "net": paid_in - paid_out}


def _top_crops_by_kg(limit: int = 5) -> List[dict]:
    with get_session() as s:
        signed_kg = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.total_weight_kg, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.total_weight_kg, 0)),
            else_=0,
        )

        rows = (
            s.query(
                Product.id.label("id"),
                Product.name.label("name"),
                Product.sku.label("sku"),
                func.coalesce(func.sum(signed_kg), 0).label("net_kg"),
            )
            .outerjoin(StockMovement, StockMovement.product_id == Product.id)
            .filter(Product.product_type == "crops")
            .filter(Product.is_active == True)  # noqa: E712
            .group_by(Product.id, Product.name, Product.sku)
            .order_by(func.coalesce(func.sum(signed_kg), 0).desc())
            .limit(int(limit))
            .all()
        )

    out = []
    for r in rows:
        out.append({
            "product_id": int(r.id),
            "name": str(r.name or ""),
            "sku": str(r.sku or ""),
            "net_kg": float(r.net_kg or 0),
        })
    return out


def get_admin_dashboard_snapshot(scope: str = "all"):
    stock = _sum_stock_totals()
    advances = get_advance_remaining_summary(scope)

    return {
        "stock": stock,
        "advances": advances,
        "top_crops": _top_crops_by_kg(limit=5),
    }


def _party_label(party, walkin_name) -> str:
    """Who a document belongs to. Every casual walk-in shares one account, so tell them apart by the name typed."""
    from database.services.walkin_account import is_shared_walkin
    if party is not None and is_shared_walkin(party.id):
        n = (walkin_name or "").strip()
        return f"{n} (Walk-in)" if n else "Walk-in"
    return party.display_name if party is not None else "Walk-in Customer"


def get_purchase_payment_snapshot(
    role: str, 
    user_id: int | None, 
    start_date: date | None = None, 
    end_date: date | None = None,
    scope: str | None = None
):
    if scope and not start_date and not end_date:
        start_dt, end_dt = _resolve_scope(scope)
        start_date = start_dt.date() if start_dt else None
        end_date = end_dt.date() if end_dt else None

    with get_session() as s:
        query = s.query(Purchase).options(joinedload(Purchase.supplier))
        # A voided purchase owes nothing - keep it out of the paid/unpaid tallies.
        query = query.filter(Purchase.status != "voided")

        if role != "admin":
            query = query.filter(Purchase.created_by_user_id == user_id)

        if start_date:
            query = query.filter(func.date(Purchase.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Purchase.created_at) <= end_date)

        purchases = query.all()

        summary = {
            "paid": {"count": 0, "total": 0.0},
            "partial": {"count": 0, "total_balance": 0.0},
            "unpaid": {"count": 0, "total_balance": 0.0},
        }

        rows = []

        # Paid/balance/status come from the one shared definition (grouped
        # queries for the whole page, not 3 per purchase).
        balances = document_balances(s, "purchase", [p.id for p in purchases])

        for purchase in purchases:
            bal = balances[purchase.id]
            status = bal.status.upper()                 # PAID | PARTIAL | UNPAID

            if bal.status == "paid":
                summary["paid"]["count"] += 1
                summary["paid"]["total"] += bal.net_total
            elif bal.status == "partial":
                summary["partial"]["count"] += 1
                summary["partial"]["total_balance"] += bal.balance
            else:
                summary["unpaid"]["count"] += 1
                summary["unpaid"]["total_balance"] += bal.balance

            rows.append({
                "purchase_id": purchase.id,
                "supplier": _party_label(purchase.supplier, purchase.walkin_name),
                "total": bal.net_total,
                "paid": min(bal.settled, bal.net_total),
                "balance": bal.balance,
                "status": status,
            })

    return {
        "summary": summary,
        "rows": rows,
    }

def get_sale_payment_snapshot(
    role: str, 
    user_id: int | None, 
    start_date: date | None = None, 
    end_date: date | None = None,
    scope: str | None = None
):
    if scope and not start_date and not end_date:
        start_dt, end_dt = _resolve_scope(scope)
        start_date = start_dt.date() if start_dt else None
        end_date = end_dt.date() if end_dt else None

    def apply_isolation(query, model):
        if role == "cashier" and user_id:
            query = query.filter(model.created_by_user_id == user_id)
        return query

    with get_session() as s:
        sales_query = s.query(Sale).options(joinedload(Sale.buyer))
        # A voided sale is owed nothing - keep it out of the paid/unpaid tallies.
        sales_query = sales_query.filter(Sale.status != "voided")
        sales_query = apply_isolation(sales_query, Sale)

        if start_date:
            sales_query = sales_query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            sales_query = sales_query.filter(func.date(Sale.created_at) <= end_date)

        sales = sales_query.all()
        rows = []

        paid_count = 0
        paid_total = 0.0
        partial_count = 0
        partial_balance_total = 0.0
        unpaid_count = 0
        unpaid_balance_total = 0.0

        # Paid/balance/status come from the one shared definition. Cashiers
        # only ever see the payments THEY recorded (apply_isolation's rule).
        balances = document_balances(
            s, "sale", [sale.id for sale in sales],
            payments_by_user_id=(user_id if role == "cashier" and user_id else None))

        for sale in sales:
            bal = balances[sale.id]
            if bal.status == "paid":
                paid_count += 1
                paid_total += bal.net_total
            elif bal.status == "partial":
                partial_count += 1
                partial_balance_total += bal.balance
            else:
                unpaid_count += 1
                unpaid_balance_total += bal.balance

            rows.append({
                "sale_id": sale.id,
                "customer": _party_label(sale.buyer, sale.walkin_name),
                "total": bal.net_total,
                "paid": min(bal.settled, bal.net_total),
                "balance": bal.balance,
                "status": bal.status.upper(),
            })

        return {
            "summary": {
                "paid": {"count": paid_count, "total": paid_total},
                "partial": {"count": partial_count, "total_balance": partial_balance_total},
                "unpaid": {"count": unpaid_count, "total_balance": unpaid_balance_total},
            },
            "rows": rows,
        }

def get_advance_settlement_snapshot(
    direction: str,
    role: str,
    user_id,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> dict:

    direction = direction.upper()
    direction_map = {
        "IN": "received",
        "OUT": "given",
    }

    if direction not in direction_map:
        raise ValueError("Invalid direction")

    db_direction = direction_map[direction]

    with get_session() as session:
        # 1. Subquery to calculate invoice allocations applied to advances
        allocated = (
            session.query(
                AdvanceAllocation.advance_id,
                func.coalesce(func.sum(AdvanceAllocation.amount), 0).label("allocated")
            )
            .group_by(AdvanceAllocation.advance_id)
            .subquery()
        )

        # 2. Subquery to calculate direct external cash/bank payments made against advances
        paid_settlements = (
            session.query(
                Payment.entity_id.label("advance_id"),
                func.coalesce(func.sum(Payment.amount), 0).label("total_paid")
            )
            .filter(Payment.entity_type == "advance")
            .filter(Payment.status != "voided")
            .group_by(Payment.entity_id)
            .subquery()
        )

        # 3. Main Query incorporating BOTH subqueries to compute the true remaining balance
        query = (
            session.query(
                Advance,
                Party.display_name,
                User.username,
                (
                    Advance.amount 
                    - func.coalesce(allocated.c.allocated, 0) 
                    - func.coalesce(paid_settlements.c.total_paid, 0)
                ).label("remaining")
            )
            .join(Party, Party.id == Advance.party_id)
            .outerjoin(User, User.id == Advance.created_by_user_id)
            .outerjoin(allocated, Advance.id == allocated.c.advance_id)
            .outerjoin(paid_settlements, Advance.id == paid_settlements.c.advance_id)
        )

        query = query.filter(Advance.direction == db_direction)
        query = query.filter(Advance.advance_type == "money")
        query = query.filter(Advance.status == "completed")

        if start_date:
            query = query.filter(func.date(Advance.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Advance.created_at) <= end_date)

        if role == "cashier" and user_id:
            query = query.filter(Advance.created_by_user_id == user_id)

        # Only fetch items that still have a balance greater than 0
        query = query.filter(
            (
                Advance.amount 
                - func.coalesce(allocated.c.allocated, 0) 
                - func.coalesce(paid_settlements.c.total_paid, 0)
            ) > 0
        )

        db_rows = query.order_by(Advance.created_at.asc()).all()

        rows = []
        total_amount = 0.0

        for adv, party_name, username, remaining in db_rows:
            remaining = float(remaining or 0)
            total_amount += remaining

            rows.append({
                "advance_id": adv.id,
                "date": adv.created_at.strftime("%Y-%m-%d"),
                "party_name": party_name or "",
                "reference": f"ADV-{adv.id}",
                "amount": remaining, 
                "qty_bags": float(adv.qty_bags or 0),
                "total_weight_kg": float(adv.total_weight_kg or 0),
                "created_by": username or "",
                "note": adv.notes or "",
            })

        return {
            "summary": {
                "count": len(rows),
                "total_amount": total_amount,
            },
            "rows": rows,
        }


def get_advance_grand_summary(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    scope: Optional[str] = None,
) -> dict:
    """
    Total OUTSTANDING (remaining) advances GIVEN and RECEIVED, in money
    advances only.

    This previously summed the raw `Advance.amount` across every
    advance_type (crops/fertilizer/money) and every non-voided status,
    ignored the caller's date range entirely, and was hardcoded to
    scope="today" - three different ways it could never agree with the
    money/completed/remaining-balance numbers shown in the table above it
    or in the dashboard cards. It now uses the exact same filters and the
    same (amount - allocated - paid) remaining-balance calculation as
    get_advance_settlement_snapshot / get_advance_remaining_summary, so
    all three numbers on screen are computed the same way.

    Accepts an explicit start_date/end_date (matching whatever the caller
    is already filtering by) or, for backward compatibility, a `scope`
    keyword ("today"/"week"/"month") which is resolved the same way the
    rest of this module resolves scopes.
    """
    if scope and not start_date and not end_date:
        start_dt, end_dt = _resolve_scope(scope)
        start_date = start_dt.date() if start_dt else None
        end_date = end_dt.date() if end_dt else None

    with get_session() as s:
        allocated = (
            s.query(
                AdvanceAllocation.advance_id,
                func.coalesce(func.sum(AdvanceAllocation.amount), 0).label("allocated")
            )
            .group_by(AdvanceAllocation.advance_id)
            .subquery()
        )

        paid_settlements = (
            s.query(
                Payment.entity_id.label("advance_id"),
                func.coalesce(func.sum(Payment.amount), 0).label("total_paid")
            )
            .filter(Payment.entity_type == "advance")
            .filter(Payment.status != "voided")
            .group_by(Payment.entity_id)
            .subquery()
        )

        remaining_expr = (
            Advance.amount
            - func.coalesce(allocated.c.allocated, 0)
            - func.coalesce(paid_settlements.c.total_paid, 0)
        )

        query = (
            s.query(Advance.direction, func.coalesce(func.sum(remaining_expr), 0))
            .outerjoin(allocated, Advance.id == allocated.c.advance_id)
            .outerjoin(paid_settlements, Advance.id == paid_settlements.c.advance_id)
            .filter(Advance.advance_type == "money")
            .filter(Advance.status == "completed")
            .filter(remaining_expr > 0)
        )

        if start_date:
            query = query.filter(func.date(Advance.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Advance.created_at) <= end_date)

        totals = dict(query.group_by(Advance.direction).all())

    received_total = float(totals.get("received", 0) or 0)
    given_total = float(totals.get("given", 0) or 0)

    return {
        "received": received_total,
        "given": given_total,
        "net": received_total - given_total,
    }


def _resolve_scope(scope: str):
    now = datetime.now()

    if scope == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    if scope == "week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    if scope == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    return None, None


def get_advance_remaining_summary(scope: str = "all"):
    # `scope` used to be accepted here and silently dropped - the query
    # below had no date filter at all, so every caller got the same
    # all-time totals no matter what scope they asked for. Wire it up for
    # real so this stays correct if/when a caller other than "all" is
    # used again (see the scope="today"->"all" comment in
    # get_admin_dashboard_snapshot's caller).
    start, end = _resolve_scope(scope)

    with get_session() as s:
        query = (
            s.query(
                Advance.id,
                Advance.direction,
                Advance.amount
            )
            .filter(Advance.advance_type == "money")
            .filter(Advance.status == "completed")
        )

        if start:
            query = query.filter(Advance.created_at >= start)
        if end:
            query = query.filter(Advance.created_at <= end)

        advances = query.all()

        advance_in = 0.0
        advance_out = 0.0

        advance_ids = [adv_id for adv_id, _direction, _amount in advances]

        # Batch-aggregate allocations/payments for every advance in one
        # round trip each, instead of 2 queries per advance - see the
        # matching note in get_purchase_payment_snapshot.
        alloc_map: dict = {}
        payment_map: dict = {}

        if advance_ids:
            alloc_map = dict(
                s.query(AdvanceAllocation.advance_id, func.coalesce(func.sum(AdvanceAllocation.amount), 0))
                .filter(AdvanceAllocation.advance_id.in_(advance_ids))
                .group_by(AdvanceAllocation.advance_id)
                .all()
            )

            payment_map = dict(
                s.query(Payment.entity_id, func.coalesce(func.sum(Payment.amount), 0))
                .filter(Payment.entity_type == "advance")
                .filter(Payment.entity_id.in_(advance_ids))
                .filter(Payment.status != "voided")
                .group_by(Payment.entity_id)
                .all()
            )

        for adv_id, direction, amount in advances:
            remaining = (
                float(amount or 0)
                - float(alloc_map.get(adv_id, 0) or 0)
                - float(payment_map.get(adv_id, 0) or 0)
            )

            if remaining <= 0:
                continue

            if direction == "received":
                advance_in += remaining
            elif direction == "given":
                advance_out += remaining

        return {
            "advance_in_remaining": advance_in,
            "advance_out_remaining": advance_out,
        }


# =====================================================================
# HARD DELETE + AUDIT TRAIL
#
# These permanently remove a row (and everything financially tied to
# it — payments, advance allocations, returns, discounts, price
# adjustments) so the dashboard's totals stay correct after the row is
# gone. Before anything is deleted, a DeletionLog row is written
# capturing WHO deleted it and a JSON snapshot of what it looked like,
# since a hard delete otherwise leaves no trace at all.
#
# `deleted_by_user_id` must be the id of the currently logged-in admin
# performing the action — NOT the `user_id` used elsewhere in this file
# for cashier row-scoping (that one is frequently None for admins).
# Callers must not omit it.
# =====================================================================

def _require_actor(deleted_by_user_id: Optional[int]) -> int:
    if not deleted_by_user_id:
        raise DeletionNotAllowed(
            "A deleted_by_user_id (the acting admin's user id) is required to delete this record."
        )
    return int(deleted_by_user_id)


def _purge_stock_and_settlements(s, *, item_ref: str, item_ids, return_ref: str,
                                 return_ids, payment_ids) -> None:
    """
    Hard-deleting a document must also remove what hangs off it indirectly,
    or the books silently drift:
      - the StockMovement rows its lines (and returns) created - otherwise the
        stock those lines added/removed stays counted forever, pointing at a
        reference_id that no longer exists;
      - any cross-cashier CashierSettlement mirrored from its payments -
        otherwise an open debt for a deleted payment lingers on the drawer.
    """
    from database.models.cashier_settlement import CashierSettlement

    if item_ids:
        s.query(StockMovement).filter(
            StockMovement.reference_type == item_ref,
            StockMovement.reference_id.in_(item_ids),
        ).delete(synchronize_session=False)
    if return_ids:
        s.query(StockMovement).filter(
            StockMovement.reference_type == return_ref,
            StockMovement.reference_id.in_(return_ids),
        ).delete(synchronize_session=False)
    if payment_ids:
        s.query(CashierSettlement).filter(
            CashierSettlement.source_payment_id.in_(payment_ids)
        ).delete(synchronize_session=False)


def _ids(rows):
    return [int(r[0]) for r in rows]


@require_admin
def delete_purchase(purchase_id: int, deleted_by_user_id: int, reason: str | None = None) -> None:
    deleted_by_user_id = _require_actor(deleted_by_user_id)

    with get_session() as s:
        purchase = s.query(Purchase).filter(Purchase.id == purchase_id).first()
        if not purchase:
            raise ValueError(f"Purchase {purchase_id} not found")

        snapshot = {
            "id": purchase.id,
            "supplier_party_id": getattr(purchase, "supplier_party_id", None),
            "supplier": purchase.supplier.display_name if getattr(purchase, "supplier", None) else None,
            "total_amount": float(purchase.total_amount or 0),
            "created_by_user_id": getattr(purchase, "created_by_user_id", None),
            "created_at": purchase.created_at.isoformat() if purchase.created_at else None,
        }

        try:
            from database.models.purchase_item import PurchaseItem
            _purge_stock_and_settlements(
                s,
                item_ref="purchase_item",
                item_ids=_ids(s.query(PurchaseItem.id).filter(PurchaseItem.purchase_id == purchase_id).all()),
                return_ref="purchase_return",
                return_ids=_ids(s.query(PurchaseReturn.id).filter(PurchaseReturn.purchase_id == purchase_id).all()),
                payment_ids=_ids(s.query(Payment.id).filter(
                    Payment.entity_type == "purchase", Payment.entity_id == purchase_id).all()),
            )

            # Dependents first, in case FKs aren't set up to cascade.
            s.query(Payment).filter(
                Payment.entity_type == "purchase", Payment.entity_id == purchase_id
            ).delete(synchronize_session=False)

            s.query(AdvanceAllocation).filter(
                AdvanceAllocation.entity_type == "purchase", AdvanceAllocation.entity_id == purchase_id
            ).delete(synchronize_session=False)

            s.query(PurchaseReturn).filter(
                PurchaseReturn.purchase_id == purchase_id
            ).delete(synchronize_session=False)

            s.query(PurchasePriceAdjustment).filter(
                PurchasePriceAdjustment.purchase_id == purchase_id
            ).delete(synchronize_session=False)

            AuditService.record(
                "purchase.delete", "purchase", purchase_id,
                f"HARD DELETE of purchase #{purchase_id}", {"reason": reason, "snapshot": snapshot}, session=s)
            s.add(DeletionLog(
                entity_type="purchase",
                entity_id=purchase_id,
                entity_snapshot=json.dumps(snapshot, default=str),
                deleted_by_user_id=deleted_by_user_id,
                reason=reason,
            ))

            s.delete(purchase)
            s.commit()
        except Exception:
            s.rollback()
            raise


@require_admin
def delete_sale(sale_id: int, deleted_by_user_id: int, reason: str | None = None) -> None:
    deleted_by_user_id = _require_actor(deleted_by_user_id)

    with get_session() as s:
        sale = s.query(Sale).filter(Sale.id == sale_id).first()
        if not sale:
            raise ValueError(f"Sale {sale_id} not found")

        snapshot = {
            "id": sale.id,
            "buyer_party_id": getattr(sale, "buyer_party_id", None),
            "customer": sale.buyer.display_name if getattr(sale, "buyer", None) else None,
            "total_amount": float(sale.total_amount or 0),
            "created_by_user_id": getattr(sale, "created_by_user_id", None),
            "created_at": sale.created_at.isoformat() if sale.created_at else None,
        }

        try:
            from database.models.sale_item import SaleItem
            _purge_stock_and_settlements(
                s,
                item_ref="sale_item",
                item_ids=_ids(s.query(SaleItem.id).filter(SaleItem.sale_id == sale_id).all()),
                return_ref="sale_return",
                return_ids=_ids(s.query(SaleReturn.id).filter(SaleReturn.sale_id == sale_id).all()),
                payment_ids=_ids(s.query(Payment.id).filter(
                    Payment.entity_type == "sale", Payment.entity_id == sale_id).all()),
            )

            s.query(Payment).filter(
                Payment.entity_type == "sale", Payment.entity_id == sale_id
            ).delete(synchronize_session=False)

            s.query(AdvanceAllocation).filter(
                AdvanceAllocation.entity_type == "sale", AdvanceAllocation.entity_id == sale_id
            ).delete(synchronize_session=False)

            s.query(SaleReturn).filter(
                SaleReturn.sale_id == sale_id
            ).delete(synchronize_session=False)

            s.query(SaleDiscount).filter(
                SaleDiscount.sale_id == sale_id
            ).delete(synchronize_session=False)

            AuditService.record(
                "sale.delete", "sale", sale_id,
                f"HARD DELETE of sale #{sale_id}", {"reason": reason, "snapshot": snapshot}, session=s)
            s.add(DeletionLog(
                entity_type="sale",
                entity_id=sale_id,
                entity_snapshot=json.dumps(snapshot, default=str),
                deleted_by_user_id=deleted_by_user_id,
                reason=reason,
            ))

            s.delete(sale)
            s.commit()
        except Exception:
            s.rollback()
            raise


@require_admin
def delete_advance(advance_id: int, deleted_by_user_id: int, reason: str | None = None) -> None:
    deleted_by_user_id = _require_actor(deleted_by_user_id)

    with get_session() as s:
        advance = s.query(Advance).filter(Advance.id == advance_id).first()
        if not advance:
            raise ValueError(f"Advance {advance_id} not found")

        snapshot = {
            "id": advance.id,
            "party_id": getattr(advance, "party_id", None),
            "direction": advance.direction,
            "advance_type": advance.advance_type,
            "amount": float(advance.amount or 0),
            "created_by_user_id": getattr(advance, "created_by_user_id", None),
            "created_at": advance.created_at.isoformat() if advance.created_at else None,
        }

        try:
            _purge_stock_and_settlements(
                s, item_ref="", item_ids=[], return_ref="", return_ids=[],
                payment_ids=_ids(s.query(Payment.id).filter(
                    Payment.entity_type == "advance", Payment.entity_id == advance_id).all()),
            )

            s.query(Payment).filter(
                Payment.entity_type == "advance", Payment.entity_id == advance_id
            ).delete(synchronize_session=False)

            s.query(AdvanceAllocation).filter(
                AdvanceAllocation.advance_id == advance_id
            ).delete(synchronize_session=False)

            AuditService.record(
                "advance.delete", "advance", advance_id,
                f"HARD DELETE of advance #{advance_id}", {"reason": reason, "snapshot": snapshot}, session=s)
            s.add(DeletionLog(
                entity_type="advance",
                entity_id=advance_id,
                entity_snapshot=json.dumps(snapshot, default=str),
                deleted_by_user_id=deleted_by_user_id,
                reason=reason,
            ))

            s.delete(advance)
            s.commit()
        except Exception:
            s.rollback()
            raise