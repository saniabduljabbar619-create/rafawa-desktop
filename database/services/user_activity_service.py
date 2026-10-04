# database/services/user_activity_service.py
from __future__ import annotations

from typing import Dict, List, Any, Optional
from datetime import date, datetime, time
from sqlalchemy import select, func, desc

from database.session import get_session
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.models.payment import Payment
from database.models.sale_return import SaleReturn
from database.models.purchase_return import PurchaseReturn
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation 
from database.services.document_balance import offset_totals
from database.services.user_performance_service import UserPerformanceService


class UserActivityService:

    @staticmethod
    def get_summary(user_id: int, start_date: Optional[date] = None, end_date: Optional[date] = None):
        # 🚀 UPDATED: Propagates the range parameters cleanly to the performance analyzer engine
        return UserPerformanceService.compute(user_id, start_date=start_date, end_date=end_date)

    @staticmethod
    def list_purchases(user_id: int, limit: int = 200, start_date: Optional[date] = None, end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        user_id = int(user_id or 0)
        limit = int(limit or 200)

        with get_session() as s:
            stmt = select(Purchase).where(Purchase.created_by_user_id == user_id)
            if start_date:
                stmt = stmt.where(func.date(Purchase.created_at) >= start_date)
            if end_date:
                # 🚀 INJECTED: Binds chronological upper boundary selection limit
                stmt = stmt.where(func.date(Purchase.created_at) <= end_date)

            purchases = s.execute(
                stmt.order_by(desc(Purchase.created_at), desc(Purchase.id)).limit(limit)
            ).scalars().all()

        out: List[Dict[str, Any]] = []
        with get_session() as s: 
            for p in purchases:
                raw_total = float(p.total_amount or 0.0)

                return_total = (
                    s.query(func.coalesce(func.sum(PurchaseReturn.amount), 0.0))
                    .filter(PurchaseReturn.purchase_id == p.id)
                    .scalar()
                ) or 0.0
                return_total = abs(float(return_total))

                paid_total = (
                    s.query(func.coalesce(func.sum(Payment.amount), 0.0))
                    .filter(
                        Payment.entity_type == "purchase",
                        Payment.entity_id == p.id,
                        Payment.direction == "out",
                        Payment.method != "advance",
                        ~Payment.notes.like("%Advance settlement%"),
                        Payment.status != "voided"
                    )
                    .scalar()
                ) or 0.0
                
                alloc_total = (
                    s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
                    .filter(AdvanceAllocation.entity_type == "purchase", AdvanceAllocation.entity_id == p.id)
                    .scalar()
                ) or 0.0

                net_total = max(raw_total - return_total, 0.0)
                settled = (float(paid_total) + float(alloc_total)
                           + offset_totals(s, "purchase", [p.id]).get(p.id, 0.0))
                balance = max(net_total - settled, 0.0)

                if net_total <= 0:
                    status = "PAID"
                elif settled <= 0:
                    status = "UNPAID"
                elif balance > 0:
                    status = "PARTIAL"
                else:
                    status = "PAID"

                completed_at = getattr(p, "completed_at", None)
                created_at = getattr(p, "created_at", None)

                out.append({
                    "id": int(p.id),
                    "purchase_no": str(p.purchase_no or ""),
                    "status": status,
                    "total": net_total,  
                    "completed_at": completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else "",
                    "created_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "",
                })
        return out

    @staticmethod
    def list_sales(user_id: int, limit: int = 200, start_date: Optional[date] = None, end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        user_id = int(user_id or 0)
        limit = int(limit or 200)

        with get_session() as s:
            stmt = select(Sale).where(Sale.created_by_user_id == user_id)
            if start_date:
                stmt = stmt.where(func.date(Sale.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(Sale.created_at) <= end_date)

            sales = s.execute(
                stmt.order_by(desc(Sale.created_at), desc(Sale.id)).limit(limit)
            ).scalars().all()

        out: List[Dict[str, Any]] = []
        with get_session() as s:
            for sale in sales:
                raw_total = float(sale.total_amount or 0.0)

                return_total = (
                    s.query(func.coalesce(func.sum(SaleReturn.amount), 0.0))
                    .filter(SaleReturn.sale_id == sale.id)
                    .scalar()
                ) or 0.0
                return_total = abs(float(return_total))

                paid_total = (
                    s.query(func.coalesce(func.sum(Payment.amount), 0.0))
                    .filter(
                        Payment.entity_type == "sale",
                        Payment.entity_id == sale.id,
                        Payment.direction == "in",
                        Payment.method != "advance",
                        ~Payment.notes.like("%Advance settlement%"),
                        Payment.status != "voided"
                    )
                    .scalar()
                ) or 0.0
                
                alloc_total = (
                    s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0))
                    .filter(AdvanceAllocation.entity_type == "sale", AdvanceAllocation.entity_id == sale.id)
                    .scalar()
                ) or 0.0

                net_total = max(raw_total - return_total, 0.0)
                settled = (float(paid_total) + float(alloc_total)
                           + offset_totals(s, "sale", [sale.id]).get(sale.id, 0.0))
                balance = max(net_total - settled, 0.0)

                if net_total <= 0:
                    status = "PAID"
                elif settled <= 0:
                    status = "UNPAID"
                elif balance > 0:
                    status = "PARTIAL"
                else:
                    status = "PAID"

                completed_at = getattr(sale, "completed_at", None)
                created_at = getattr(sale, "created_at", None)

                out.append({
                    "id": int(sale.id),
                    "sale_no": str(sale.sale_no or ""),
                    "status": status,
                    "total": net_total,  
                    "paid": min(settled, net_total),
                    "completed_at": completed_at.strftime("%Y-%m-%d %H:%M") if completed_at else "",
                    "created_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "",
                })
        return out

    @staticmethod
    def list_payments(user_id: int, limit: int = 300, start_date: Optional[date] = None, end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        user_id = int(user_id or 0)
        limit = int(limit or 300)

        with get_session() as s:
            stmt = select(Payment).where(Payment.created_by_user_id == user_id)
            if start_date:
                stmt = stmt.where(func.date(Payment.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(Payment.created_at) <= end_date)
            
            rows = s.execute(
                stmt.order_by(desc(Payment.created_at), desc(Payment.id)).limit(limit)
            ).scalars().all()

        out: List[Dict[str, Any]] = []
        for p in rows:
            if str(p.method).lower() == "advance" or "advance settlement" in str(p.notes).lower():
                continue
                
            created_at = getattr(p, "created_at", None)
            out.append({
                "id": int(p.id),
                "entity_type": str(getattr(p, "entity_type", "") or ""),
                "entity_id": int(getattr(p, "entity_id", 0) or 0),
                "direction": str(getattr(p, "direction", "") or "").upper(),
                "amount": float(getattr(p, "amount", 0) or 0),
                "method": str(getattr(p, "method", "") or ""),
                "notes": str(getattr(p, "notes", "") or ""),
                "created_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "",
            })
        return out

    @staticmethod
    def list_sale_returns(user_id: int, limit: int = 200, start_date: Optional[date] = None, end_date: Optional[date] = None):
        user_id = int(user_id or 0)
        with get_session() as s:
            stmt = select(SaleReturn).where(SaleReturn.created_by_user_id == user_id)
            if start_date:
                stmt = stmt.where(func.date(SaleReturn.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(SaleReturn.created_at) <= end_date)
                
            rows = s.execute(
                stmt.order_by(desc(SaleReturn.created_at), desc(SaleReturn.id)).limit(limit)
            ).scalars().all()

        out = []
        for r in rows:
            created_at = getattr(r, "created_at", None)
            out.append({
                "id": int(r.id),
                "sale_id": int(r.sale_id),
                "amount": abs(float(r.amount or 0)),  
                "reason": str(r.reason or ""),
                "created_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "",
            })
        return out

    @staticmethod
    def list_purchase_returns(user_id: int, limit: int = 200, start_date: Optional[date] = None, end_date: Optional[date] = None):
        user_id = int(user_id or 0)
        with get_session() as s:
            stmt = select(PurchaseReturn).where(PurchaseReturn.created_by_user_id == user_id)
            if start_date:
                stmt = stmt.where(func.date(PurchaseReturn.created_at) >= start_date)
            if end_date:
                stmt = stmt.where(func.date(PurchaseReturn.created_at) <= end_date)
                
            rows = s.execute(
                stmt.order_by(desc(PurchaseReturn.created_at), desc(PurchaseReturn.id)).limit(limit)
            ).scalars().all()

        out = []
        for r in rows:
            created_at = getattr(r, "created_at", None)
            out.append({
                "id": int(r.id),
                "purchase_id": int(r.purchase_id),
                "amount": abs(float(r.amount or 0)),  
                "reason": str(r.reason or ""),
                "created_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "",
            })
        return out

    @staticmethod
    def list_advances(user_id: int, limit: int = 300, start_date: Optional[date] = None, end_date: Optional[date] = None):
        user_id = int(user_id or 0)
        limit = int(limit or 300)
        
        combined_list = []

        with get_session() as s:
            # 1. Original master Advance setups
            adv_stmt = select(Advance).where(Advance.created_by_user_id == user_id)
            if start_date:
                adv_stmt = adv_stmt.where(func.date(Advance.created_at) >= start_date)
            if end_date:
                adv_stmt = adv_stmt.where(func.date(Advance.created_at) <= end_date)
                
            adv_rows = s.execute(adv_stmt).scalars().all()
            for a in adv_rows:
                sys_dir = str(a.direction or "").lower().strip()
                view_dir = "IN" if sys_dir == "received" else "OUT"
                amt = float(a.amount or 0.0)
                
                lifelong_same_user_allocations = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0)).filter(
                    AdvanceAllocation.advance_id == a.id,
                    AdvanceAllocation.created_by_user_id == user_id
                ).scalar() or 0.0
                
                amt = max(amt - float(lifelong_same_user_allocations), 0.0)

                combined_list.append({
                    "id": int(a.id),
                    "direction": view_dir,
                    "amount": amt,
                    "method": "Cash",
                    "notes": str(a.notes or ""),
                    "created_at": a.created_at
                })

            # 2. Partial advance payout settlements
            pay_stmt = select(Payment).where(
                Payment.created_by_user_id == user_id,
                Payment.entity_type == "advance"
            )
            if start_date:
                pay_stmt = pay_stmt.where(func.date(Payment.created_at) >= start_date)
            if end_date:
                pay_stmt = pay_stmt.where(func.date(Payment.created_at) <= end_date)
                
            pay_rows = s.execute(pay_stmt).scalars().all()
            for p in pay_rows:
                pay_dir = str(p.direction or "").lower().strip()
                view_dir = "IN" if pay_dir == "in" else "OUT"

                combined_list.append({
                    "id": int(p.id),
                    "direction": view_dir,
                    "amount": float(p.amount or 0),
                    "method": str(p.method or "Cash"),
                    "notes": str(p.notes or "Advance Settlement Handled"),
                    "created_at": p.created_at
                })

            # 3. Cross-cashier historical invoice settlements
            cross_pay_stmt = select(Payment).where(
                Payment.created_by_user_id == user_id,
                Payment.entity_type.in_(["sale", "purchase"])
            )
            if start_date:
                cross_pay_stmt = cross_pay_stmt.where(func.date(Payment.created_at) >= start_date)
            if end_date:
                cross_pay_stmt = cross_pay_stmt.where(func.date(Payment.created_at) <= end_date)
                
            cross_pay_rows = s.execute(cross_pay_stmt).scalars().all()
            for p in cross_pay_rows:
                et = str(p.entity_type or "").lower().strip()
                pay_dir = str(p.direction or "").lower().strip()
                method = str(p.method or "").lower().strip()
                notes = str(p.notes or "").lower().strip()
                
                if method == "advance" or "advance settlement" in notes:
                    continue

                is_cross_cashier = False
                if et == "sale":
                    parent = s.get(Sale, p.entity_id)
                    if parent and parent.created_by_user_id != user_id:
                        is_cross_cashier = True
                else:
                    parent = s.get(Purchase, p.entity_id)
                    if parent and parent.created_by_user_id != user_id:
                        is_cross_cashier = True

                if is_cross_cashier:
                    view_dir = "IN" if pay_dir == "in" else "OUT"
                    combined_list.append({
                        "id": int(p.id),
                        "direction": view_dir,
                        "amount": float(p.amount or 0),
                        "method": str(p.method or "Cash"),
                        "notes": str(p.notes or f"Cross-shift {et} balance collection"),
                        "created_at": p.created_at
                    })

        combined_list.sort(key=lambda x: x["created_at"] if x["created_at"] else datetime.min, reverse=True)
        trimmed = combined_list[:limit]

        final_out = []
        for item in trimmed:
            dt = item["created_at"]
            final_out.append({
                "id": item["id"],
                "direction": item["direction"],
                "amount": item["amount"],
                "method": item["method"],
                "notes": item["notes"],
                "created_at": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
            })
        return final_out
    
    
    
    # ---------------------------------------------------------
    # 🚀 POST-ADVANCE SETTLEMENT CLEARANCE HOOKS
    # ---------------------------------------------------------
    @staticmethod
    def list_pending_advance_settlements(user_id: int, start_date: Optional[date] = None, end_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        🔍 Scans for partial ('PARTIAL') invoice lines created by this cashier 
        that used an Advance Allocation and still have an outstanding balance due.
        """
        user_id = int(user_id or 0)
        out_records = []

        with get_session() as s:
            # 1. Inspect Sales Lines
            sale_stmt = select(Sale).where(Sale.created_by_user_id == user_id)
            if start_date:
                sale_stmt = sale_stmt.where(func.date(Sale.created_at) >= start_date)
            if end_date:
                sale_stmt = sale_stmt.where(func.date(Sale.created_at) <= end_date)
            
            sales = s.execute(sale_stmt).scalars().all()
            for sale in sales:
                raw_total = float(sale.total_amount or 0.0)
                
                # Deduct returns
                ret = s.query(func.coalesce(func.sum(SaleReturn.amount), 0.0)).filter(SaleReturn.sale_id == sale.id).scalar() or 0.0
                net_total = max(raw_total - abs(float(ret)), 0.0)

                # Fetch advance allocation values specifically
                alloc_total = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0)).filter(
                    AdvanceAllocation.entity_type == "sale", AdvanceAllocation.entity_id == sale.id
                ).scalar() or 0.0
                
                if alloc_total <= 0:
                    continue  # Bypasses standard cash invoices that didn't touch an advance

                # Fetch standard cash/bank payments
                paid_total = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                    Payment.entity_type == "sale", Payment.entity_id == sale.id,
                    Payment.direction == "in", Payment.method != "advance",
                    ~Payment.notes.like("%Advance settlement%"),
                    Payment.status != "voided"
                ).scalar() or 0.0

                balance_due = max(net_total - (float(paid_total) + float(alloc_total)), 0.0)
                if balance_due > 0.01:  # Filter out items that still have a balance due
                    out_records.append({
                        "id": int(sale.id),
                        "type": "sale",
                        "doc_no": str(sale.sale_no or f"SL-{sale.id}"),
                        "net_total": net_total,
                        "advance_used": float(alloc_total),
                        "balance_due": balance_due
                    })

            # 2. Inspect Purchase Lines
            purch_stmt = select(Purchase).where(Purchase.created_by_user_id == user_id)
            if start_date:
                purch_stmt = purch_stmt.where(func.date(Purchase.created_at) >= start_date)
            if end_date:
                purch_stmt = purch_stmt.where(func.date(Purchase.created_at) <= end_date)

            purchases = s.execute(purch_stmt).scalars().all()
            for p in purchases:
                raw_total = float(p.total_amount or 0.0)
                
                # Deduct returns
                ret = s.query(func.coalesce(func.sum(PurchaseReturn.amount), 0.0)).filter(PurchaseReturn.purchase_id == p.id).scalar() or 0.0
                net_total = max(raw_total - abs(float(ret)), 0.0)

                # Fetch advance allocation values specifically
                alloc_total = s.query(func.coalesce(func.sum(AdvanceAllocation.amount), 0.0)).filter(
                    AdvanceAllocation.entity_type == "purchase", AdvanceAllocation.entity_id == p.id
                ).scalar() or 0.0
                
                if alloc_total <= 0:
                    continue  # Bypasses standard invoices that didn't touch an advance

                # Fetch standard payments out
                paid_total = s.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter(
                    Payment.entity_type == "purchase", Payment.entity_id == p.id,
                    Payment.direction == "out", Payment.method != "advance",
                    ~Payment.notes.like("%Advance settlement%"),
                    Payment.status != "voided"
                ).scalar() or 0.0

                balance_due = max(net_total - (float(paid_total) + float(alloc_total)), 0.0)
                if balance_due > 0.01:
                    out_records.append({
                        "id": int(p.id),
                        "type": "purchase",
                        "doc_no": str(p.purchase_no or f"PR-{p.id}"),
                        "net_total": net_total,
                        "advance_used": float(alloc_total),
                        "balance_due": balance_due
                    })

        return out_records

    @staticmethod
    def clear_pending_advance_balance(doc_type: str, doc_id: int, amount: float, operator_id: int):
        """
        💰 Writes a concluding Payment row log entry to settle an outstanding balance completely.
        This forces the calculated balance down to zero, automatically removing it from the tracking view list.
        """
        from database.models.payment import Payment
        doc_type = str(doc_type).lower().strip()
        
        with get_session() as s:
            direction = "in" if doc_type == "sale" else "out"
            notes = f"Cash balance clearance collection matching historical advance invoice reference ID: #{doc_id}"
            
            clearance_payment = Payment(
                party_id=0,  # Fallback zero assignment; standard relational cascade handles this smoothly
                entity_type=doc_type,
                entity_id=int(doc_id),
                direction=direction,
                amount=float(amount),
                method="Cash",
                notes=notes,
                created_by_user_id=int(operator_id)
            )
            
            # Fetch and assign precise relational party target references safely from parent objects
            if doc_type == "sale":
                parent_sale = s.get(Sale, int(doc_id))
                if parent_sale:
                    clearance_payment.party_id = parent_sale.buyer_party_id
            else:
                parent_purch = s.get(Purchase, int(doc_id))
                if parent_purch:
                    clearance_payment.party_id = parent_purch.supplier_party_id

            s.add(clearance_payment)
            s.commit()