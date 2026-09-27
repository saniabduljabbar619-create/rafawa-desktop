# database/services/user_activity_details_service.py
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from database.session import get_session

from database.models.party import Party
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.payment import Payment


class UserActivityDetailsService:

    @staticmethod
    def _parse_notes(notes: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not notes:
            return out
        parts = [p.strip() for p in str(notes).split(";") if p.strip()]
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    @staticmethod
    def get_details(*, actor_user_id: int, actor_role: str, kind: str, entity_id: int) -> Dict:
        kind = (kind or "").strip().lower()
        entity_id = int(entity_id or 0)
        actor_user_id = int(actor_user_id or 0)
        actor_role = (actor_role or "").strip().lower()

        if entity_id <= 0:
            raise ValueError("Invalid entity_id.")

        if kind == "purchase":
            return UserActivityDetailsService._purchase_details(actor_user_id, actor_role, entity_id)

        if kind == "sale":
            return UserActivityDetailsService._sale_details(actor_user_id, actor_role, entity_id)

        if kind == "payment":
            return UserActivityDetailsService._payment_details(actor_user_id, actor_role, entity_id)

        raise ValueError(f"Unsupported kind: {kind}")

    # -----------------------------
    # PURCHASE DETAILS
    # -----------------------------
    @staticmethod
    def _purchase_details(actor_user_id: int, actor_role: str, purchase_id: int) -> Dict:
        with get_session() as s:
            p = s.get(Purchase, int(purchase_id))
            if not p:
                raise ValueError("Purchase not found.")

            # 🔐 Isolation enforcement
            if actor_role != "admin" and p.created_by_user_id != actor_user_id:
                raise PermissionError("Access denied.")

            party = s.get(Party, int(p.supplier_party_id or 0)) if getattr(p, "supplier_party_id", None) else None

            rows = (
                s.execute(
                    select(PurchaseItem, Product)
                    .join(Product, PurchaseItem.product_id == Product.id)
                    .where(PurchaseItem.purchase_id == int(purchase_id))
                )
                .all()
            )

        items: List[Dict] = []
        for pi, prod in rows:
            items.append({
                "product": str(getattr(prod, "name", "") or ""),
                "product_type": str(getattr(pi, "product_type_snapshot", "") or ""),
                "bags": int(getattr(pi, "qty_bags", 0) or 0),
                "kg": float(getattr(pi, "total_weight_kg", 0) or 0),
                "rate": float(getattr(pi, "unit_cost_rate", 0) or 0),
                "line_total": float(getattr(pi, "line_total", 0) or 0),
            })

        ledger = "fertilizers" if any(str(i["product_type"]).lower() == "fertilizer" for i in items) else "crops"

        dt = getattr(p, "created_at", None)
        dt_txt = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

        return {
            "title": f"Purchase Details (PUR-{int(p.id)})",
            "subtitle": f"Created: {dt_txt}",
            "party": {
                "name": str(getattr(party, "display_name", "") or "-") if party else "-",
                "phone": str(getattr(party, "phone", "") or "-") if party else "-",
            },
            "summary": {
                "type": "purchase",
                "status": str(getattr(p, "status", "") or ""),
                "ledger": ledger,
                "amount": f'{float(getattr(p, "total_amount", 0) or 0):,.2f}',
            },
            "items": items,
        }

    # -----------------------------
    # SALE DETAILS
    # -----------------------------
    @staticmethod
    def _sale_details(actor_user_id: int, actor_role: str, sale_id: int) -> Dict:
        with get_session() as s:
            x = s.get(Sale, int(sale_id))
            if not x:
                raise ValueError("Sale not found.")

            # 🔐 Isolation enforcement
            if actor_role != "admin" and x.created_by_user_id != actor_user_id:
                raise PermissionError("Access denied.")

            party = s.get(Party, int(x.buyer_party_id or 0)) if getattr(x, "buyer_party_id", None) else None

            rows = (
                s.execute(
                    select(SaleItem, Product)
                    .join(Product, SaleItem.product_id == Product.id)
                    .where(SaleItem.sale_id == int(sale_id))
                )
                .all()
            )

        items: List[Dict] = []
        for si, prod in rows:
            items.append({
                "product": str(getattr(prod, "name", "") or ""),
                "product_type": str(getattr(si, "product_type_snapshot", "") or ""),
                "bags": int(getattr(si, "qty_bags", 0) or 0),
                "kg": float(getattr(si, "total_weight_kg", 0) or 0),
                "rate": float(getattr(si, "unit_sale_rate", 0) or 0),
                "line_total": float(getattr(si, "line_total", 0) or 0),
            })

        ledger = "fertilizers" if any(str(i["product_type"]).lower() == "fertilizer" for i in items) else "crops"

        dt = getattr(x, "created_at", None)
        dt_txt = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

        return {
            "title": f"Sale Details (SAL-{int(x.id)})",
            "subtitle": f"Created: {dt_txt}",
            "party": {
                "name": str(getattr(party, "display_name", "") or "-") if party else "-",
                "phone": str(getattr(party, "phone", "") or "-") if party else "-",
            },
            "summary": {
                "type": "sale",
                "status": str(getattr(x, "status", "") or ""),
                "ledger": ledger,
                "amount": f'{float(getattr(x, "total_amount", 0) or 0):,.2f}',
            },
            "items": items,
        }

    # -----------------------------
    # PAYMENT DETAILS
    # -----------------------------
    @staticmethod
    def _payment_details(actor_user_id: int, actor_role: str, payment_id: int) -> Dict:
        with get_session() as s:
            p = s.get(Payment, int(payment_id))
            if not p:
                raise ValueError("Payment not found.")

            # 🔐 Isolation enforcement
            if actor_role != "admin" and p.created_by_user_id != actor_user_id:
                raise PermissionError("Access denied.")

            party = s.get(Party, int(p.party_id or 0)) if getattr(p, "party_id", None) else None

        meta = UserActivityDetailsService._parse_notes(getattr(p, "notes", "") or "")
        ledger = (meta.get("ledger") or "").strip().lower() or "crops"

        dt = getattr(p, "created_at", None)
        dt_txt = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""

        et = str(getattr(p, "entity_type", "") or "")
        direction = str(getattr(p, "direction", "") or "")
        method = str(getattr(p, "method", "") or "")

        return {
            "title": f"Payment Details (PAY-{int(p.id)})",
            "subtitle": f"Created: {dt_txt}   •   {et}   •   dir={direction}",
            "party": {
                "name": str(getattr(party, "display_name", "") or "-") if party else "-",
                "phone": str(getattr(party, "phone", "") or "-") if party else "-",
            },
            "summary": {
                "type": "advance" if et == "advance" else "payment",
                "status": "posted",
                "ledger": ledger,
                "amount": f'{float(getattr(p, "amount", 0) or 0):,.2f}',
                "method": method,
                "notes": str(getattr(p, "notes", "") or ""),
            },
            "items": [],
        }