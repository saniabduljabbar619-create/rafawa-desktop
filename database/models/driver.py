#database/models/driver.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, func, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class Driver(Base):
    """
    A reusable driver record - name, phone, vehicle number - linked to
    Sales Orders (and, later, Purchase Orders) at dispatch time. Kept as
    its own entity deliberately, not free-text fields on the order
    itself: the same driver genuinely gets reused across many orders
    over time, and their own delivery history is worth reviewing on its
    own (which orders they've carried, how many, how recently).

    Deliberately isolated from Party, Payment, and the rest of the app's
    ledger/balance system, matching the same isolation SalesOrder itself
    already has - created_by_user_id is the one exception, since
    recording which staff member did something is pure audit trail, not
    the kind of cross-system linkage that needs isolating (every other
    table in this schema, including SalesOrder itself, does the same).
    """
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    vehicle_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)