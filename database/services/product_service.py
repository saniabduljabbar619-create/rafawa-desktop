# database/services/product_service.py
from __future__ import annotations

from typing import List, Dict, Optional
from sqlalchemy import text
from database.session import get_session
from database.models.stock_movement import STOCK_IN_TYPES, STOCK_OUT_TYPES


IN_TYPES = STOCK_IN_TYPES
OUT_TYPES = STOCK_OUT_TYPES


def list_products_with_stock_db(
    q: str = "",
    category: str = "All",
    product_type: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = 2000,
) -> List[Dict]:
    q = (q or "").strip().lower()
    category = (category or "All").strip()
    pt = (product_type or "").strip().lower() or None

    where = []
    params = {"limit": int(max(1, min(int(limit or 2000), 5000)))}

    if not include_inactive:
        where.append("COALESCE(p.is_active, 1) = 1")

    if pt in ("crops", "fertilizer"):
        where.append("p.product_type = :ptype")
        params["ptype"] = pt

    # NOTE: your "category" is currently just UI text; DB has category_id.
    # If you later add categories table + join, we’ll wire proper filtering.
    # For now we keep category filtering client-side in the UI OR store category in notes.
    # So we do NOT filter by category_id here.

    if q:
        where.append("(LOWER(p.name) LIKE :q OR LOWER(COALESCE(p.sku,'')) LIKE :q)")
        params["q"] = f"%{q}%"

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = text(f"""
        SELECT
            p.id,
            p.name,
            p.product_type,
            COALESCE(p.sku, '') AS sku,
            COALESCE(p.unit_label, '') AS unit_label,
            COALESCE(p.is_active, 1) AS is_active,
            COALESCE(p.default_cost_rate, 0) AS default_cost_rate,
            COALESCE(p.default_sale_rate, 0) AS default_sale_rate,

            COALESCE(SUM(
                CASE
                    WHEN sm.movement_type IN {IN_TYPES} THEN COALESCE(sm.qty_bags, 0)
                    WHEN sm.movement_type IN {OUT_TYPES} THEN -COALESCE(sm.qty_bags, 0)
                    ELSE 0
                END
            ), 0) AS net_bags,

            COALESCE(SUM(
                CASE
                    WHEN sm.movement_type IN {IN_TYPES} THEN COALESCE(sm.total_weight_kg, 0)
                    WHEN sm.movement_type IN {OUT_TYPES} THEN -COALESCE(sm.total_weight_kg, 0)
                    ELSE 0
                END
            ), 0) AS net_kg

        FROM products p
        LEFT JOIN stock_movements sm ON sm.product_id = p.id
        {where_sql}
        GROUP BY p.id
        ORDER BY p.name ASC
        LIMIT :limit
    """)

    with get_session() as s:
        rows = s.execute(sql, params).fetchall()

    cols = [
        "id", "name", "product_type", "sku", "unit_label", "is_active",
        "default_cost_rate", "default_sale_rate", "net_bags", "net_kg"
    ]
    return [dict(zip(cols, r)) for r in rows]


def create_product_db(payload: Dict) -> int:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")

    product_type = (payload.get("product_type") or "").strip().lower()
    if product_type not in ("crops", "fertilizer"):
        raise ValueError("product_type must be crops|fertilizer")

    sku = (payload.get("sku") or "").strip()
    unit_label = (payload.get("unit_label") or "").strip() or "kg"

    is_active = 1 if str(payload.get("status") or "active").strip().lower() != "inactive" else 0

    dcr = payload.get("default_cost_rate")
    dsr = payload.get("default_sale_rate")

    dcr = float(dcr) if dcr not in (None, "") else None
    dsr = float(dsr) if dsr not in (None, "") else None

    with get_session() as s:
        res = s.connection().exec_driver_sql(
            """
            INSERT INTO products
                (name, product_type, sku, unit_label, is_active, default_cost_rate, default_sale_rate)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s)
            """,
            (name, product_type, sku or None, unit_label, is_active, dcr, dsr)
        )
        pid = int(res.lastrowid or 0)
        s.commit()
    return pid


def update_product_db(product_id: int, payload: Dict) -> None:
    pid = int(product_id or 0)
    if pid <= 0:
        raise ValueError("product_id is required")

    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")

    sku = (payload.get("sku") or "").strip()
    unit_label = (payload.get("unit_label") or "").strip() or "kg"
    is_active = 1 if str(payload.get("status") or "active").strip().lower() != "inactive" else 0

    dcr = payload.get("default_cost_rate")
    dsr = payload.get("default_sale_rate")
    dcr = float(dcr) if dcr not in (None, "") else None
    dsr = float(dsr) if dsr not in (None, "") else None

    with get_session() as s:
        s.connection().exec_driver_sql(
            """
            UPDATE products
            SET name=%s, sku=%s, unit_label=%s, is_active=%s,
                default_cost_rate=%s, default_sale_rate=%s
            WHERE id=%s
            """,
            (name, sku or None, unit_label, is_active, dcr, dsr, pid)
        )
        s.commit()


def deactivate_product_db(product_id: int) -> None:
    pid = int(product_id or 0)
    if pid <= 0:
        raise ValueError("product_id is required")

    with get_session() as s:
        s.connection().exec_driver_sql(
            "UPDATE products SET is_active = 0 WHERE id = %s",
            (pid,)
        )
        s.commit()


def sku_exists_db(sku: str, exclude_id: Optional[int] = None) -> bool:
    sku = (sku or "").strip()
    if not sku:
        return False

    with get_session() as s:
        if exclude_id:
            row = s.connection().exec_driver_sql(
                "SELECT 1 FROM products WHERE sku=%s AND id<>%s LIMIT 1",
                (sku, int(exclude_id)),
            ).fetchone()
        else:
            row = s.connection().exec_driver_sql(
                "SELECT 1 FROM products WHERE sku=%s LIMIT 1",
                (sku,),
            ).fetchone()
    return bool(row)


def adjust_product_inventory_db(
    product_id: int,
    bags_diff: float,
    weight_diff: float,
    user_id: int | None = None
):

    if bags_diff == 0 and weight_diff == 0:
        return

    movement_type = "adjustment_in"

    if bags_diff < 0 or weight_diff < 0:
        movement_type = "adjustment_out"

    bags = abs(float(bags_diff or 0))
    weight = abs(float(weight_diff or 0))

    with get_session() as s:
        s.connection().exec_driver_sql(
            """
            INSERT INTO stock_movements
            (
                product_id,
                movement_type,
                qty_bags,
                total_weight_kg,
                reference_type,
                reference_id,
                created_by_user_id
            )
            VALUES
            (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                int(product_id),
                movement_type,
                bags,
                weight,
                "catalog_adjustment",
                0,
                1
            )
        )
        s.commit()