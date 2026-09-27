# database/services/profit_analytics_service.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import date
from sqlalchemy import select, func
from database.session import get_session

from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.product import Product
from database.models.sale_return import SaleReturn


class ProfitAnalyticsService:

    @staticmethod
    def get_profit_loss_summary_db(start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        📊 Itemized Profit & Loss Analytics Engine
        Computes accurate margins by subtracting product returns from both Revenue and COGS,
        while separating true price-drop losses from standard profitable gains.
        """
        with get_session() as s:
            # 1. Query lines using standard ORM structures
            stmt = (
                select(
                    Sale.id,
                    Sale.discount,
                    Product.id,
                    Product.name,
                    Product.product_type,
                    SaleItem.qty_bags,
                    SaleItem.total_weight_kg,
                    SaleItem.unit_sale_rate,
                    func.coalesce(SaleItem.unit_cost_snapshot, Product.default_cost_rate)
                )
                .join(SaleItem, SaleItem.product_id == Product.id)
                .join(Sale, SaleItem.sale_id == Sale.id)
                .where(Sale.status == "completed")
            )

            if start_date:
                stmt = stmt.where(func.date(Sale.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(Sale.created_at) <= end_date)

            raw_sales_items = s.execute(stmt).all()

            # 2. Query return entries within the time range window
            ret_stmt = select(SaleReturn.product_id, SaleReturn.amount)
            if start_date:
                ret_stmt = ret_stmt.where(func.date(SaleReturn.created_at) >= start_date)
            if end_date:
                ret_stmt = ret_stmt.where(func.date(SaleReturn.created_at) <= end_date)

            return_rows = s.execute(ret_stmt).all()

            returns_map = {}
            for r_pid, r_amt in return_rows:
                pid = int(r_pid)
                returns_map[pid] = returns_map.get(pid, 0.0) + abs(float(r_amt or 0.0))

            # 3a. First pass: each sale's total gross revenue (summed across
            # all its own lines) - needed to prorate that sale's discount
            # proportionally per line. The checkout screen already applies
            # discount per-product, but SaleItem has no discount column to
            # store that granularity - Sale.discount is the only place it's
            # actually recorded, as one total for the whole sale. Prorating
            # it back down by each line's share of the sale is the closest
            # available approximation without a schema change.
            sale_gross_revenue: Dict[int, float] = {}
            for sid, sale_discount, pid, pname, ptype, qty_bags, total_weight_kg, sale_rate, cost_rate in raw_sales_items:
                ptype_str = str(ptype or "").lower().strip()
                if ptype_str == "fertilizer":
                    line_rev = float(qty_bags or 0.0) * float(sale_rate or 0.0)
                else:
                    line_rev = float(total_weight_kg or 0.0) * float(sale_rate or 0.0)
                sale_gross_revenue[sid] = sale_gross_revenue.get(sid, 0.0) + line_rev

            # 3b. Second pass: aggregate into product_metrics, each line's
            # revenue reduced by its proportional share of its sale's discount.
            product_metrics = {}
            for sid, sale_discount, pid, pname, ptype, qty_bags, total_weight_kg, sale_rate, cost_rate in raw_sales_items:
                ptype_str = str(ptype or "").lower().strip()
                qty_bags_val = float(qty_bags or 0.0)
                weight_kg_val = float(total_weight_kg or 0.0)
                sale_rate_val = float(sale_rate or 0.0)
                cost_rate_val = float(cost_rate or 0.0)
                sale_discount_val = float(sale_discount or 0.0)

                if ptype_str == "fertilizer":
                    raw_line_revenue = qty_bags_val * sale_rate_val
                    item_cogs = qty_bags_val * cost_rate_val
                    item_volume = qty_bags_val
                    unit_str = "Bags"
                else:
                    raw_line_revenue = weight_kg_val * sale_rate_val
                    item_cogs = weight_kg_val * cost_rate_val
                    item_volume = weight_kg_val
                    unit_str = "KG"

                gross_for_sale = sale_gross_revenue.get(sid, 0.0)
                line_discount = (
                    (raw_line_revenue / gross_for_sale) * sale_discount_val
                    if gross_for_sale > 0 else 0.0
                )
                item_revenue = max(raw_line_revenue - line_discount, 0.0)

                if pid not in product_metrics:
                    product_metrics[pid] = {
                        "name": pname,
                        "category": ptype_str.capitalize(),
                        "volume": 0.0,
                        "unit": unit_str,
                        "revenue": 0.0,
                        "cogs": 0.0,
                        "returns": returns_map.get(pid, 0.0)
                    }

                product_metrics[pid]["volume"] += item_volume
                product_metrics[pid]["revenue"] += item_revenue
                product_metrics[pid]["cogs"] += item_cogs

            # 4. Process itemized adjustments to apply returns and isolate losses
            table_rows = []
            gross_revenue = 0.0
            total_cogs = 0.0
            total_gains = 0.0
            total_losses = 0.0

            for pid, m in product_metrics.items():
                raw_rev = m["revenue"]
                raw_cogs = m["cogs"]
                ret_val = m["returns"]

                # Calculate what percentage of total sales was returned
                return_ratio = (ret_val / raw_rev) if raw_rev > 0 else 0.0
                if return_ratio > 1.0:
                    return_ratio = 1.0

                # Proportions clear down revenue, volume, and cost bases (COGS) concurrently
                net_rev = max(raw_rev - ret_val, 0.0)
                net_cogs = max(raw_cogs - (raw_cogs * return_ratio), 0.0)
                net_volume = max(m["volume"] - (m["volume"] * return_ratio), 0.0)

                net_profit = net_rev - net_cogs

                # Accumulate global totals maps
                gross_revenue += net_rev
                total_cogs += net_cogs

                if net_profit >= 0:
                    total_gains += net_profit
                else:
                    total_losses += net_profit  # Accumulated as a negative value for the summary block

                table_rows.append({
                    "product_id": pid,
                    "product_name": m["name"],
                    "category": m["category"],
                    "volume_sold": f"{net_volume:,.2f} {m['unit']}",
                    "revenue": net_rev,
                    "cogs": net_cogs,
                    "profit": net_profit
                })

            margin_pct = ((gross_revenue - total_cogs) / gross_revenue * 100.0) if gross_revenue > 0 else 0.0

            return {
                "summary": {
                    "gross_revenue": gross_revenue,
                    "total_cogs": total_cogs,
                    "total_gains": total_gains,
                    "total_losses": total_losses,
                    "margin_pct": margin_pct,
                },
                "items": table_rows
            }

    # -------------------------------------------------------------------
    # Per-product drill-down: the individual completed sale lines behind a
    # product's aggregated profit row (customer, operator, prices, profit).
    # -------------------------------------------------------------------
    @staticmethod
    def get_product_sale_lines(product_id: int,
                               start_date: Optional[date] = None,
                               end_date: Optional[date] = None):
        from database.models.party import Party
        from database.models.user import User

        with get_session() as s:
            stmt = (
                select(
                    Sale.id,
                    Sale.created_at,
                    Sale.discount,
                    SaleItem.qty_bags,
                    SaleItem.total_weight_kg,
                    SaleItem.unit_sale_rate,
                    SaleItem.product_type_snapshot,
                    func.coalesce(SaleItem.unit_cost_snapshot, Product.default_cost_rate),
                    Party.display_name,
                    Sale.walkin_name,
                    User.username,
                )
                .select_from(Product)
                .join(SaleItem, SaleItem.product_id == Product.id)
                .join(Sale, SaleItem.sale_id == Sale.id)
                .outerjoin(Party, Party.id == Sale.buyer_party_id)
                .outerjoin(User, User.id == Sale.created_by_user_id)
                .where(Sale.status == "completed", Product.id == int(product_id))
            )
            if start_date:
                stmt = stmt.where(func.date(Sale.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(Sale.created_at) <= end_date)
            stmt = stmt.order_by(Sale.created_at.desc())

            main_rows = s.execute(stmt).all()

            # A sale's discount is only ever recorded as one total for the
            # whole sale (SaleItem has no discount column of its own) - to
            # prorate it correctly per line, each line needs its OWN sale's
            # full gross revenue across every product in that sale, not just
            # this one. Fetched separately here, one query for every sale
            # involved, rather than N+1 queries per row.
            sale_ids = {int(r[0]) for r in main_rows}
            sale_gross_revenue: Dict[int, float] = {}
            if sale_ids:
                all_items_stmt = (
                    select(
                        SaleItem.sale_id,
                        SaleItem.qty_bags,
                        SaleItem.total_weight_kg,
                        SaleItem.unit_sale_rate,
                        SaleItem.product_type_snapshot,
                    )
                    .where(SaleItem.sale_id.in_(sale_ids))
                )
                for a_sid, a_bags, a_kg, a_rate, a_ptype in s.execute(all_items_stmt).all():
                    a_is_fert = str(a_ptype or "").strip().lower() in ("fertilizer", "fertilizers")
                    a_qty = float(a_bags or 0) if a_is_fert else float(a_kg or 0)
                    a_line_rev = a_qty * float(a_rate or 0.0)
                    sale_gross_revenue[int(a_sid)] = sale_gross_revenue.get(int(a_sid), 0.0) + a_line_rev

            out = []
            for (sid, created, sale_discount, bags, kg, rate, ptype, cost_rate, cust, walkin, oper) in main_rows:
                is_fert = str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")
                qty = float(bags or 0) if is_fert else float(kg or 0)
                unit = "bags" if is_fert else "kg"
                rate = float(rate or 0.0)
                cost_rate = float(cost_rate or 0.0)
                raw_revenue = qty * rate

                gross_for_sale = sale_gross_revenue.get(int(sid), 0.0)
                sale_discount_val = float(sale_discount or 0.0)
                line_discount = (
                    (raw_revenue / gross_for_sale) * sale_discount_val
                    if gross_for_sale > 0 else 0.0
                )
                revenue = max(raw_revenue - line_discount, 0.0)
                cogs = qty * cost_rate
                out.append({
                    "sale_no": f"SAL-{sid}",
                    "date": created,
                    "customer": (cust or walkin or "Walk-in"),
                    "operator": (oper or ""),
                    "qty": qty,
                    "unit": unit,
                    "rate": rate,
                    "cost_rate": cost_rate,
                    "revenue": revenue,
                    "cogs": cogs,
                    "profit": revenue - cogs,
                })
            return out