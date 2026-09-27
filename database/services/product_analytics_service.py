# database/services/product_analytics_service.py

from sqlalchemy import text
from database.session import get_session


def get_product_purchase_capital_summary_db():
    """
    Returns historical purchase capital per product.

    - Uses purchase_items as source of truth
    - Uses product_type_snapshot for unit basis
    - Filters ONLY completed purchases
    - Computes:
        total_bags
        total_qty (kg for crops, bags for fertilizer)
        total_purchase_value
        avg_unit_cost
    """

    sql = text("""
        SELECT
            pr.id AS product_id,
            pr.name AS product_name,
            pr.product_type AS product_type,

            -- Always aggregate bags (important for crops visibility)
            SUM(pi.qty_bags) AS total_bags,

            -- Quantity basis:
            -- crops => kg
            -- fertilizer => bags
            SUM(
                CASE
                    WHEN pi.product_type_snapshot = 'fertilizer'
                        THEN pi.qty_bags
                    ELSE pi.total_weight_kg
                END
            ) AS total_qty,

            -- Total capital injected
            SUM(pi.line_total) AS total_purchase_value,

            -- Weighted average cost
            CASE
                WHEN SUM(
                    CASE
                        WHEN pi.product_type_snapshot = 'fertilizer'
                            THEN pi.qty_bags
                        ELSE pi.total_weight_kg
                    END
                ) > 0
                THEN
                    SUM(pi.line_total) /
                    SUM(
                        CASE
                            WHEN pi.product_type_snapshot = 'fertilizer'
                                THEN pi.qty_bags
                            ELSE pi.total_weight_kg
                        END
                    )
                ELSE 0
            END AS avg_unit_cost

        FROM purchase_items pi
        JOIN purchases pu ON pu.id = pi.purchase_id
        JOIN products pr ON pr.id = pi.product_id

        WHERE pu.status = 'completed'

        GROUP BY pr.id, pr.name, pr.product_type

        ORDER BY pr.product_type ASC, total_purchase_value DESC
    """)

    with get_session() as session:
        rows = session.execute(sql).mappings().all()
        return [dict(r) for r in rows]