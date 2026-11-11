from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Numeric, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class CustomerClosing(Base):
    __tablename__ = "customer_closings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    closing_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    party_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("parties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    party_name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    party_phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    opening_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)

    # Activity components within this period
    total_purchases: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_sales: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_advance_out: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_advance_in: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_payments_in: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_payments_out: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    total_returns: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)

    # Net change & closing balance:
    # Debits (customer owes us): Sales + Advance OUT + Payments OUT
    # Credits (we owe customer): Purchases + Advance IN + Payments IN + Returns
    # net_period_change = Debits - Credits
    # closing_balance = opening_balance + net_period_change
    net_period_change: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)
    closing_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0.00, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="closed", nullable=False)  # closed | voided
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CustomerClosing id={self.id} no={self.closing_no!r} party={self.party_name!r} balance={self.closing_balance}>"
