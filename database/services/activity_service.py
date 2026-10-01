# database/services/activity_service.py
from __future__ import annotations

from typing import List, Dict, Optional
from sqlalchemy import desc
from database.session import get_session

from database.models.purchase import Purchase
from database.models.sale import Sale
from database.models.party import Party
from database.models.payment import Payment


# Optional: if you want ledger/type parsing from notes later
def _guess_ledger_from_notes(notes: str) -> str:
    s = (notes or "").lower()

    if "fertilizer" in s:   # matches fertilizer/fertilizers/[fertilizers]/type=fertilizer
        return "fertilizers"
    if "crops" in s or "crop" in s:
        return "crops"
    return ""


def list_customer_activities_db(
    customer_id: int,
    session_user_id: int,
    role: str,
    limit: int = 500
) -> List[Dict]:
    cid = int(customer_id or 0)
    if cid <= 0:
        return []

    uid = int(session_user_id or 0)
    if uid <= 0:
        raise ValueError("session_user_id is required (login session).")
    
    def apply_isolation(query, model):
        if role == "cashier":
            query = query.filter(model.created_by_user_id == uid)
        return query

    limit = max(1, min(int(limit or 500), 2000))
    out: List[Dict] = []

    with get_session() as s:
        # 🛡️ SYSTEM SECURITY GATE: Compile a direct look-up cache map of all system operators
        user_rows = s.connection().exec_driver_sql("SELECT id, username FROM users").fetchall()
        user_map = {int(r[0]): str(r[1] or "").strip() for r in user_rows}

        # -------------------
        # Purchases (Intakes)
        # -------------------
        purchases_query = (
                s.query(Purchase)
                .filter(Purchase.supplier_party_id == cid)
            )

        purchases_query = apply_isolation(purchases_query, Purchase)

        purchases = (
            purchases_query
            .order_by(desc(Purchase.id))
            .limit(limit)
            .all()
        )
        for p in purchases:
            ledger = _guess_ledger_from_notes(p.notes or "")
            created_by_id = int(getattr(p, "created_by_user_id", 0) or 0)
            
            out.append({
                "dt": (p.created_at.isoformat(sep="T", timespec="seconds") if p.created_at else ""),
                "reference": f"PUR-{int(p.id)}",
                "ledger": ledger,
                "type": "intake",
                "amount": float(p.total_amount or 0.0),
                "summary": "Intake (Purchase)",
                "status": str(p.status or ""),
                "processed_by": user_map.get(created_by_id, "System"),  # 🚀 Live Cashier Mapping
                "entity_type": "purchase",
                "entity_id": int(p.id),
                "meta": {"source": "purchase"},
            })

        # ------------
        # Sales
        # ------------
        sales_query = (
            s.query(Sale)
            .filter(Sale.buyer_party_id == cid)
        )

        sales_query = apply_isolation(sales_query, Sale)

        sales = (
            sales_query
            .order_by(desc(Sale.id))
            .limit(limit)
            .all()
        )
        for x in sales:
            created_by_id = int(getattr(x, "created_by_user_id", 0) or 0)
            
            out.append({
                "dt": (x.created_at.isoformat(sep="T", timespec="seconds") if x.created_at else ""),
                "reference": f"SAL-{int(x.id)}",
                "ledger": _guess_ledger_from_notes((getattr(x, "notes", "") or "")),
                "type": "sale",
                "amount": float(x.total_amount or 0.0),
                "summary": "Sale checkout",
                "status": str(x.status or ""),
                "processed_by": user_map.get(created_by_id, "System"),  # 🚀 Live Cashier Mapping
                "entity_type": "sale",
                "entity_id": int(x.id),
                "meta": {"source": "sale"},
            })

        # ------------
        # Payments (DB truth)
        # ------------
        payments_query = (
            s.query(Payment)
            .filter(Payment.party_id == cid)
        )

        payments_query = apply_isolation(payments_query, Payment)

        pays = (
            payments_query
            .order_by(desc(Payment.id))
            .limit(limit)
            .all()
        )
        for p in pays:
            etype = (str(getattr(p, "entity_type", "") or "")).strip().lower()
            created_by_id = int(getattr(p, "created_by_user_id", 0) or 0)
            operator_name = user_map.get(created_by_id, "System")

            # money-advance mirror
            if etype == "advance":
                out.append({
                    "dt": (p.created_at.isoformat(sep="T", timespec="seconds") if p.created_at else ""),
                    "reference": f"ADV-PAY-{int(p.id)}",
                    "ledger": "money",
                    "type": "advance",
                    "amount": float(p.amount or 0.0),
                    "summary": f"Money advance ({p.direction})",
                    "status": "Recorded",
                    "processed_by": operator_name,  # 🚀 Live Cashier Mapping
                    "entity_type": "advance",
                    "entity_id": int(p.entity_id or 0),
                    "meta": {
                        "method": getattr(p, "method", None),
                        "note": getattr(p, "notes", None),
                        "direction": str(p.direction),
                        "source": "payments_mirror",
                    },
                })
                continue

            out.append({
                "dt": (p.created_at.isoformat(sep="T", timespec="seconds") if p.created_at else ""),
                "reference": f"PAY-{int(p.id)}",
                "ledger": _guess_ledger_from_notes((getattr(p, "notes", "") or "")) or "crops",
                "type": "payment",
                "amount": float(p.amount or 0.0),
                "summary": f"Payment ({p.entity_type} • {p.direction})",
                "status": "Recorded",
                "processed_by": operator_name,  # 🚀 Live Cashier Mapping
                "entity_type": str(p.entity_type),
                "entity_id": int(p.entity_id or 0),
                "meta": {
                    "method": getattr(p, "method", None),
                    "note": getattr(p, "notes", None),
                    "direction": str(p.direction),
                },
            })

    out.sort(key=lambda r: (r.get("dt") or ""), reverse=True)
    return out