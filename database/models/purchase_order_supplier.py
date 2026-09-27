#database/models/purchase_order_supplier.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, func, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class PurchaseOrderSupplier(Base):
    """
    A Purchase Order supplier - deliberately its own entity, isolated
    from Party (which serves regular suppliers/customers throughout the
    rest of the app), mirroring Company's exact reasoning for Sales
    Order. Confirmed decision: keeps PO fully isolated from the rest of
    the app, matching SO's own isolation, even though a "supplier"
    already exists as a concept elsewhere - this is a deliberately
    separate one for the PO subsystem specifically.

    The PO screen looks up an existing supplier by name first; only
    creates a new one if it genuinely doesn't exist yet.
    """
    __tablename__ = "purchase_order_suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    contact_person: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)