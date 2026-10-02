# database/services/purchase_order_supplier_service.py
from __future__ import annotations

from typing import Dict, List, Optional

from database.session import get_session
from database.models.purchase_order_supplier import PurchaseOrderSupplier


class PurchaseOrderSupplierService:
    """
    CRUD for the PurchaseOrderSupplier entity - mirrors CompanyService
    exactly. See PurchaseOrderSupplier's own docstring for the full
    isolation reasoning. Matches the same search-first, create-if-not-
    found flow: search for an existing supplier by name first; only
    create a new one if it genuinely doesn't exist yet.
    """

    @staticmethod
    def search(term: str, limit: int = 20) -> List[Dict]:
        term = (term or "").strip()
        with get_session() as s:
            q = s.query(PurchaseOrderSupplier).filter(PurchaseOrderSupplier.is_active == True)  # noqa: E712
            if term:
                q = q.filter(PurchaseOrderSupplier.name.ilike(f"%{term}%"))
            rows = q.order_by(PurchaseOrderSupplier.name.asc()).limit(limit).all()
        return [
            {
                "id": int(sup.id), "name": sup.name, "address": sup.address or "",
                "phone": sup.phone or "", "contact_person": sup.contact_person or "",
            }
            for sup in rows
        ]

    @staticmethod
    def find_by_exact_name(name: str) -> Optional[Dict]:
        name = (name or "").strip()
        if not name:
            return None
        with get_session() as s:
            sup = (
                s.query(PurchaseOrderSupplier)
                .filter(PurchaseOrderSupplier.is_active == True)  # noqa: E712
                .filter(PurchaseOrderSupplier.name.ilike(name))
                .first()
            )
            if not sup:
                return None
            return {
                "id": int(sup.id), "name": sup.name, "address": sup.address or "",
                "phone": sup.phone or "", "contact_person": sup.contact_person or "",
            }

    @staticmethod
    def create(name: str, created_by_user_id: int, address: str = "",
              phone: str = "", contact_person: str = "", notes: str = "") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Supplier name is required.")
        created_by_user_id = int(created_by_user_id or 0)
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        existing = PurchaseOrderSupplierService.find_by_exact_name(name)
        if existing:
            raise ValueError(
                f"A supplier named '{existing['name']}' already exists - "
                f"select it from search instead of creating a duplicate."
            )

        with get_session() as s:
            sup = PurchaseOrderSupplier(
                name=name,
                address=(address or "").strip() or None,
                phone=(phone or "").strip() or None,
                contact_person=(contact_person or "").strip() or None,
                notes=(notes or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(sup)
            s.flush()
            new_id = int(sup.id)

        # the same counterparty must be pickable on the other side too (Sales <-> Purchase Orders)
        from database.services.order_hub_service import OrderHubService
        OrderHubService.sync_partners()
        return new_id

    @staticmethod
    def get(supplier_id: int) -> Optional[Dict]:
        supplier_id = int(supplier_id or 0)
        if supplier_id <= 0:
            return None
        with get_session() as s:
            sup = s.get(PurchaseOrderSupplier, supplier_id)
            if not sup:
                return None
            return {
                "id": int(sup.id), "name": sup.name, "address": sup.address or "",
                "phone": sup.phone or "", "contact_person": sup.contact_person or "",
                "notes": sup.notes or "",
            }

    @staticmethod
    def deactivate(supplier_id: int) -> None:
        supplier_id = int(supplier_id or 0)
        with get_session() as s:
            sup = s.get(PurchaseOrderSupplier, supplier_id)
            if sup:
                sup.is_active = False