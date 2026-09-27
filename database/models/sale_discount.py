#database/models/sale_discount.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class SaleDiscount(Base):
    """
    A manual, after-the-fact discount applied to a specific sale - the
    "lazy calculator" in the Payments section. Deliberately additive, never
    overwriting: Sale.total_amount, Sale.discount, and every SaleItem's own
    rate/line_total stay exactly as originally recorded, forever. This
    table is the ONLY place the adjustment itself lives, so the original
    sale is always reconstructable exactly as it happened, with each
    adjustment sitting alongside it as its own auditable row.

    Scoped to sales that are genuinely partial or unpaid at the moment
    this gets applied - see SaleAdjustmentService.apply_discount, which
    re-checks the sale's real, current balance right before inserting this
    row, not just whatever the screen showed when it was first opened.
    """
    __tablename__ = "sale_discounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False)
    sale = relationship("Sale")

    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sale_discounts_sale", "sale_id"),
    )