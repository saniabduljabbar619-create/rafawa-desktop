# database/services/driver_service.py
from __future__ import annotations

from typing import Dict, List, Optional

from database.session import get_session
from database.models.driver import Driver


class DriverService:
    """
    CRUD for the reusable Driver entity - see Driver's own docstring for
    why this is its own table rather than free-text fields on an order.
    Deliberately isolated from Party/Payment, matching the same
    isolation the Sales Order subsystem itself has from the rest of the
    app.
    """

    @staticmethod
    def create(name: str, created_by_user_id: int, phone: str = "",
              vehicle_number: str = "", notes: str = "") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Driver name is required.")
        created_by_user_id = int(created_by_user_id or 0)
        if created_by_user_id <= 0:
            raise ValueError("created_by_user_id is required.")

        with get_session() as s:
            d = Driver(
                name=name,
                phone=(phone or "").strip() or None,
                vehicle_number=(vehicle_number or "").strip() or None,
                notes=(notes or "").strip() or None,
                created_by_user_id=created_by_user_id,
            )
            s.add(d)
            s.flush()
            return int(d.id)

    @staticmethod
    def list_active(search: str = "") -> List[Dict]:
        """For a driver picker - active drivers, optionally filtered by
        a name/phone/vehicle search term."""
        search = (search or "").strip().lower()
        with get_session() as s:
            q = s.query(Driver).filter(Driver.is_active == True)  # noqa: E712
            drivers = q.order_by(Driver.name.asc()).all()

        out = []
        for d in drivers:
            if search and search not in (d.name or "").lower() \
                    and search not in (d.phone or "").lower() \
                    and search not in (d.vehicle_number or "").lower():
                continue
            out.append({
                "id": int(d.id), "name": d.name, "phone": d.phone or "",
                "vehicle_number": d.vehicle_number or "", "notes": d.notes or "",
            })
        return out

    @staticmethod
    def get(driver_id: int) -> Optional[Dict]:
        driver_id = int(driver_id or 0)
        if driver_id <= 0:
            return None
        with get_session() as s:
            d = s.get(Driver, driver_id)
            if not d:
                return None
            return {
                "id": int(d.id), "name": d.name, "phone": d.phone or "",
                "vehicle_number": d.vehicle_number or "", "notes": d.notes or "",
            }

    @staticmethod
    def deactivate(driver_id: int) -> None:
        """Soft-remove - never deletes, so past orders keep a real,
        readable link to who actually carried them."""
        driver_id = int(driver_id or 0)
        with get_session() as s:
            d = s.get(Driver, driver_id)
            if d:
                d.is_active = False