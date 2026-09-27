#database/models/company.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, func, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class Company(Base):
    """
    A Sales Order buyer - a company, never a customer, and never will
    be. Deliberately its own entity, isolated from Party (which serves
    customers and suppliers throughout the rest of the app), matching
    the same reasoning and isolation Driver already has: mixing a
    company into Party would pollute the regular customer dropdowns
    used everywhere else, and risk it accidentally picking up a real
    sale/purchase balance through PartyBalanceService - exactly what the
    Sales Order subsystem's whole design is meant to avoid.

    The Sales Order screen looks up an existing company by name first;
    only creates a new one if it genuinely doesn't exist yet.
    """
    __tablename__ = "companies"

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