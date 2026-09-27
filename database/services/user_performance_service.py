# database/services/user_performance_service.py
from __future__ import annotations

from typing import Dict, Any, List, Optional
from datetime import date, datetime, time
from sqlalchemy import select, func
from database.session import get_session
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.models.payment import Payment
from database.models.sale_return import SaleReturn
from database.models.purchase_return import PurchaseReturn
from database.models.advance import Advance  
from database.models.advance_allocations import AdvanceAllocation  


class UserPerformanceService:

    @staticmethod
    def compute(user_id: int, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, Any]:
        """
        📊 Cross-Shift Audited Performance Compute Engine
        Locks down actual physical cash accountability by verifying the source 
        creator of applied advances to prevent cross-cashier drawer deficits.
        """
        user_id = int(user_id or 0)
        if user_id <= 0:
            raise ValueError("user_id is required.")

        with get_session() as s:
            
            # --- Helper to apply date filter consistently ---
            def apply_filter(stmt, model):
                stmt = stmt.where(model.created_by_user_id == user_id)
                if start_date:
                    stmt = stmt.where(func.date(model.created_at) >= start_date)
                if end_date:
                    stmt = stmt.where(func.date(model.created_at) <= end_date)
                return stmt

            # ---------------------------------------------------------
            # 1. AUDITED SALES REVENUE RECONCILIATION
            # ---------------------------------------------------------
            sales_stmt = apply_filter(select(Sale), Sale)
            sales_records = s.execute(sales_stmt).scalars().all()

            smart_sales_total = 0.0
            smart_sales_net = 0.0
            smart_sale_count = 0
            smart_sale_returns = 0.0
            daily_map = {}

            for sale in sales_records:
                raw_total = float(sale.total_amount or 0.0)

                ret_val = s.query(func.coalesce(func.sum(SaleReturn.amount), 0.0)).filter(
                    SaleReturn.sale_id == sale.id
                ).scalar() or 0.0
                clean_ret = abs(float(ret_val))
                net_invoice_total = max(raw_total - clean_ret, 0.0)

                # Fetch physical cash collected during this shift for this sale
                paid_val = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                    Payment.entity_type == "sale",
                    Payment.entity_id == sale.id,
                    Payment.direction == "in",
                    Payment.created_by_user_id == user_id,
                    Payment.method != "advance",
                    ~Payment.notes.like("%Advance settlement%"),
                    Payment.status != "voided"
                ).scalar() or 0.0

                # 🚀 DEEP CROSS-SHIFT AUDIT: Calculate advance allocations applied to this sale
                allocations = s.query(AdvanceAllocation).filter(
                    AdvanceAllocation.entity_type == "sale",
                    AdvanceAllocation.entity_id == sale.id,
                    AdvanceAllocation.created_by_user_id == user_id
                ).all()

                same_shift_alloc_val = 0.0
                for alloc in allocations:
                    # Look up the original advance row to see who collected the cash asset
                    orig_advance = s.get(Advance, alloc.advance_id)
                    if orig_advance and orig_advance.created_by_user_id == user_id:
                        # Cash was collected on THIS shift. Safe to credit performance metrics!
                        same_shift_alloc_val += float(alloc.amount or 0.0)
                    else:
                        # 🛡️ CROSS-SHIFT DETECTION: Cash is sitting in another cashier's drawer!
                        # We skip adding this to Cashier B's performance net so their cash audit remains clean.
                        continue

                # Total verified contribution belonging strictly to this cashier's drawer layer
                settled_amount = float(paid_val) + same_shift_alloc_val

                if settled_amount > 0 or net_invoice_total == 0:
                    contribution = min(settled_amount, net_invoice_total)
                    smart_sales_total += raw_total
                    smart_sales_net += contribution
                    smart_sale_returns += clean_ret
                    smart_sale_count += 1

                    s_date = sale.created_at.date() if hasattr(sale.created_at, "date") else sale.created_at
                    d_str = str(s_date) if s_date else ""
                    if d_str:
                        daily_map[d_str] = daily_map.get(d_str, 0.0) + contribution

            daily = [{"date": k, "sales": v} for k, v in sorted(daily_map.items())]

            # ---------------------------------------------------------
            # 2. AUDITED PURCHASES INTAKE RECONCILIATION
            # ---------------------------------------------------------
            purchases_stmt = apply_filter(select(Purchase), Purchase)
            purchases_records = s.execute(purchases_stmt).scalars().all()

            smart_purchases_total = 0.0
            smart_purchases_net = 0.0
            smart_purchase_count = 0
            smart_purchase_returns = 0.0

            for p in purchases_records:
                raw_total = float(p.total_amount or 0.0)

                ret_val = s.query(func.coalesce(func.sum(PurchaseReturn.amount), 0.0)).filter(
                    PurchaseReturn.purchase_id == p.id
                ).scalar() or 0.0
                clean_ret = abs(float(ret_val))
                net_invoice_total = max(raw_total - clean_ret, 0.0)

                # Physical cash disbursed out during this specific shift
                paid_val = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                    Payment.entity_type == "purchase",
                    Payment.entity_id == p.id,
                    Payment.direction == "out",
                    Payment.created_by_user_id == user_id,
                    Payment.method != "advance",
                    ~Payment.notes.like("%Advance settlement%"),
                    Payment.status != "voided"
                ).scalar() or 0.0

                # 🚀 DEEP CROSS-SHIFT AUDIT: Calculate advance pre-fundings consumed
                allocations = s.query(AdvanceAllocation).filter(
                    AdvanceAllocation.entity_type == "purchase",
                    AdvanceAllocation.entity_id == p.id,
                    AdvanceAllocation.created_by_user_id == user_id
                ).all()

                same_shift_alloc_val = 0.0
                for alloc in allocations:
                    orig_advance = s.get(Advance, alloc.advance_id)
                    if orig_advance and orig_advance.created_by_user_id == user_id:
                        # This cashier disbursed the pre-payment earlier on this shift
                        same_shift_alloc_val += float(alloc.amount or 0.0)
                    else:
                        # Cross-shift consumption: Skip to prevent false drawer shortfalls
                        continue

                settled_amount = float(paid_val) + same_shift_alloc_val

                if settled_amount > 0 or net_invoice_total == 0:
                    contribution = min(settled_amount, net_invoice_total)
                    smart_purchases_total += raw_total
                    smart_purchases_net += contribution
                    smart_purchase_returns += clean_ret
                    smart_purchase_count += 1

            # ---------------------------------------------------------
            # 3. PAYMENTS & ADVANCES SHIFT CASH SUMMARY
            # ---------------------------------------------------------
            payments = {"sale_in": 0.0, "purchase_out": 0.0, "advance_in": 0.0, "advance_out": 0.0}

            payment_records = s.execute(
                apply_filter(select(Payment), Payment)
            ).scalars().all()

            for p in payment_records:
                amt = float(p.amount or 0.0)
                et = str(p.entity_type or "").lower().strip()
                direction = str(p.direction or "").lower().strip()
                method = str(getattr(p, "method", "") or "").lower().strip()
                notes = str(getattr(p, "notes", "") or "").lower().strip()

                if method == "advance" or "advance settlement" in notes:
                    continue

                if et == "advance":
                    key = f"advance_{direction}"
                    if key in payments:
                        payments[key] += amt
                
                elif et in ("sale", "purchase"):
                    is_cross_cashier_settlement = False
                    if et == "sale":
                        parent_invoice = s.get(Sale, p.entity_id)
                        if parent_invoice and parent_invoice.created_by_user_id != user_id:
                            is_cross_cashier_settlement = True
                    else:
                        parent_invoice = s.get(Purchase, p.entity_id)
                        if parent_invoice and parent_invoice.created_by_user_id != user_id:
                            is_cross_cashier_settlement = True

                    if is_cross_cashier_settlement:
                        if et == "sale" and direction == "in":
                            payments["advance_in"] += amt
                        elif et == "purchase" and direction == "out":
                            payments["advance_out"] += amt
                    else:
                        key = f"{et}_{direction}"
                        if key in payments:
                            payments[key] += amt

            # Process Master Advance creation lines recorded on this shift
            advance_records = s.execute(
                apply_filter(select(Advance), Advance)
            ).scalars().all()

            for adv in advance_records:
                amt = float(adv.amount or 0.0)
                sys_dir = str(adv.direction or "").lower().strip()
                
                lifelong_same_user_allocations = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0)).filter(
                    AdvanceAllocation.advance_id == adv.id,
                    AdvanceAllocation.created_by_user_id == user_id
                ).scalar() or 0.0

                # Settlement payments recorded against this advance reduce the
                # outstanding exposure, so a fully-settled advance nets to 0
                # (mirrors the customer-profile balance netting).
                settled = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                    Payment.entity_type == "advance",
                    Payment.entity_id == adv.id,
                    Payment.status != "voided"
                ).scalar() or 0.0

                amt = max(amt - float(lifelong_same_user_allocations) - float(settled), 0.0)

                if sys_dir == "received":
                    payments["advance_in"] += amt
                elif sys_dir == "given":
                    payments["advance_out"] += amt

        return {
            "purchases": {
                "count": int(smart_purchase_count),
                "total": float(smart_purchases_total),
                "returns_total": smart_purchase_returns,
                "net": smart_purchases_net,
            },
            "sales": {
                "count": int(smart_sale_count),
                "total": float(smart_sales_total),
                "returns_total": smart_sale_returns,
                "net": smart_sales_net,
            },
            "payments": payments,
            "daily": daily,
        }