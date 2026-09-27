# database/services/stock_service.py
from typing import Dict, List

from sqlalchemy import func, case
from database.session import get_session
from database.models.stock_movement import StockMovement, STOCK_IN_TYPES, STOCK_OUT_TYPES
from database.models.product import Product

# Local aliases for the shared, canonical classification - kept so nothing
# else in this file needs to change, just removes the independent copy
# that used to live here.
IN_MOVES = STOCK_IN_TYPES
OUT_MOVES = STOCK_OUT_TYPES


def sum_stock_totals() -> Dict[str, float]:
    with get_session() as s:
        signed_bags = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.qty_bags, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.qty_bags, 0)),
            else_=0,
        )

        signed_kg = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.total_weight_kg, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.total_weight_kg, 0)),
            else_=0,
        )

        fert_bags = (
            s.query(func.coalesce(func.sum(signed_bags), 0))
            .join(Product)
            .filter(Product.product_type == "fertilizer")
            .scalar()
        )

        crops_bags = (
            s.query(func.coalesce(func.sum(signed_bags), 0))
            .join(Product)
            .filter(Product.product_type == "crops")
            .scalar()
        )

        crops_kg = (
            s.query(func.coalesce(func.sum(signed_kg), 0))
            .join(Product)
            .filter(Product.product_type == "crops")
            .scalar()
        )

    return {
        "fertilizer_bags": float(fert_bags or 0),
        "crops_bags": float(crops_bags or 0),
        "crops_kg": float(crops_kg or 0),
    }


def top_crops_by_kg(limit: int = 5) -> List[dict]:
    with get_session() as s:
        signed_kg = case(
            (StockMovement.movement_type.in_(IN_MOVES), func.coalesce(StockMovement.total_weight_kg, 0)),
            (StockMovement.movement_type.in_(OUT_MOVES), -func.coalesce(StockMovement.total_weight_kg, 0)),
            else_=0,
        )

        rows = (
            s.query(
                Product.id,
                Product.name,
                Product.sku,
                func.coalesce(func.sum(signed_kg), 0).label("net_kg"),
            )
            .outerjoin(StockMovement)
            .filter(Product.product_type == "crops")
            .filter(Product.is_active == True)
            .group_by(Product.id, Product.name, Product.sku)
            .order_by(func.coalesce(func.sum(signed_kg), 0).desc())
            .limit(limit)
            .all()
        )

    return [
        {
            "product_id": int(r.id),
            "name": r.name or "",
            "sku": r.sku or "",
            "net_kg": float(r.net_kg or 0),
        }
        for r in rows
    ]