# database/services/company_service.py
from __future__ import annotations

from typing import Dict, List, Optional

from database.session import get_session
from database.models.company import Company


class CompanyService:
    """
    CRUD for the Company entity - a Sales Order buyer, never a customer.
    See Company's own docstring for the full isolation reasoning.
    Matches the described screen flow: search for an existing company by
    name first; only create a new one if it genuinely doesn't exist yet.
    """

    @staticmethod
    def search(term: str, limit: int = 20) -> List[Dict]:
        """For the "does this company already exist?" lookup step -
        active companies whose name contains the search term."""
        term = (term or "").strip()
        with get_session() as s:
            q = s.query(Company).filter(Company.is_active == True)  # noqa: E712
            if term:
                q = q.filter(Company.name.ilike(f"%{term}%"))
            rows = q.order_by(Company.name.asc()).limit(limit).all()
        return [
            {
                "id": int(c.id), "name": c.name, "address": c.address or "",
                "phone": c.phone or "", "contact_person": c.contact_person or "",
            }
            for c in rows
        ]

    @staticmethod
    def find_by_exact_name(name: str) -> Optional[Dict]:
        """A precise, case-insensitive exact match - used right before
        creating a new company, to catch the case where the operator
        typed a name that already exists but didn't pick it from search."""
        name = (name or "").strip()
        if not name:
            return None
        with get_session() as s:
            c = (
                s.query(Company)
                .filter(Company.is_active == True)  # noqa: E712
                .filter(Company.name.ilike(name))
                .first()
            )
            if not c:
                return None
            return {
                "id": int(c.id), "name": c.name, "address": c.address or "",
                "phone": c.phone or "", "contact_person": c.contact_person or "",
            }

    @staticmethod
    def create(name: str, created_by_user_id: int, address: str = "",
              phone: str = "", contact_person: str = "", notes: str = "") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Company name is required.")
        created_by_user_id = int(created_by_user_id or 0)
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        existing = CompanyService.find_by_exact_name(name)
        if existing:
            raise ValueError(
                f"A company named '{existing['name']}' already exists - "
                f"select it from search instead of creating a duplicate."
            )

        with get_session() as s:
            c = Company(
                name=name,
                address=(address or "").strip() or None,
                phone=(phone or "").strip() or None,
                contact_person=(contact_person or "").strip() or None,
                notes=(notes or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(c)
            s.flush()
            return int(c.id)

    @staticmethod
    def get(company_id: int) -> Optional[Dict]:
        company_id = int(company_id or 0)
        if company_id <= 0:
            return None
        with get_session() as s:
            c = s.get(Company, company_id)
            if not c:
                return None
            return {
                "id": int(c.id), "name": c.name, "address": c.address or "",
                "phone": c.phone or "", "contact_person": c.contact_person or "",
                "notes": c.notes or "",
            }

    @staticmethod
    def deactivate(company_id: int) -> None:
        """Soft-remove - never deletes, so past orders keep a real,
        readable link to who they were actually sold to."""
        company_id = int(company_id or 0)
        with get_session() as s:
            c = s.get(Company, company_id)
            if c:
                c.is_active = False