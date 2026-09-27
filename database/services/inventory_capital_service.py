# database/services/inventory_capital_service.py

from sqlalchemy import text
from database.session import get_session


def get_live_inventory_capital_summary_db():
    """
    Live Inventory Capital (movement-driven, schema-safe).

    Net Stock derived from stock_movements.
    Weighted avg cost derived from purchase_items.
    """

    sql = text("""
        SELECT
            pr.id AS product_id,
            pr.name AS product_name,
            pr.product_type,

            -- =========================
            -- NET BAGS (fertilizer)
            -- =========================
            (
                COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type = 'purchase_in'
                        THEN sm.qty_bags
                        ELSE 0
                    END
                ),0)

                - COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type IN ('sale_out','adjustment_out','advance_out')
                        THEN sm.qty_bags
                        ELSE 0
                    END
                ),0)

                + COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type IN ('return_in','adjustment_in')
                        THEN sm.qty_bags
                        ELSE 0
                    END
                ),0)
            ) AS net_bags,

            -- =========================
            -- NET KG (crops)
            -- =========================
            (
                COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type = 'purchase_in'
                        THEN sm.total_weight_kg
                        ELSE 0
                    END
                ),0)

                - COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type IN ('sale_out','adjustment_out','advance_out')
                        THEN sm.total_weight_kg
                        ELSE 0
                    END
                ),0)

                + COALESCE(SUM(
                    CASE
                        WHEN sm.movement_type IN ('return_in','adjustment_in')
                        THEN sm.total_weight_kg
                        ELSE 0
                    END
                ),0)
            ) AS net_kg,

            -- =========================
            -- Weighted Average Purchase Cost
            -- =========================
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

        FROM products pr

        LEFT JOIN stock_movements sm
            ON sm.product_id = pr.id

        LEFT JOIN purchase_items pi
            ON pi.product_id = pr.id

        LEFT JOIN purchases pu
            ON pu.id = pi.purchase_id
            AND pu.status = 'completed'

        GROUP BY pr.id, pr.name, pr.product_type

        ORDER BY pr.product_type, pr.name
    """)

    with get_session() as session:
        rows = session.execute(sql).mappings().all()
        return [dict(r) for r in rows]