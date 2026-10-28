#database/models/purchase_price_adjustment.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class PurchasePriceAdjustment(Base):
    """
    A manual, after-the-fact price correction on a specific purchase -
    mirrors SaleDiscount's exact pattern, but for the purchase side, and
    typically the opposite polarity: a supplier coming back and asking
    for MORE than what was originally agreed (e.g. "actually, make it
    370/kg instead of 350/kg"), rather than a discount taken off.

    Deliberately additive, never overwriting: Purchase.total_amount and
    every PurchaseItem's own rate/line_total stay exactly as originally
    recorded, forever. This table is the ONLY place the adjustment
    itself lives, so the original purchase is always reconstructable
    exactly as it happened, with each correction sitting alongside it as
    its own auditable row.

    amount is signed: positive means the supplier is owed MORE (the
    common case - a price increase), negative means owed LESS (a price
    decrease agreed after the fact, kept possible for completeness
    without needing a second, mirrored model). Works identically
    whether the purchase was already fully paid (the new amount becomes
    a fresh, genuine balance, and the purchase correctly reappears as
    needing payment again) or still partial/unpaid (it simply adds to
    the existing balance) - see PurchaseAdjustmentService.apply_
    adjustment, which computes the purchase's real, current balance
    fresh each time, not from whatever the screen showed when first
    opened.
    """
    __tablename__ = "purchase_price_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=False)
    purchase = relationship("Purchase")

    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_purchase_price_adjustments_purchase", "purchase_id"),
    )


class PurchasePriceAdjustmentLine(Base):
    """
    One product's part of a price edit: "Maize 350 -> 370 per kg on 1,000 kg = +20,000". An edit made from the
    purchase's rows writes ONE PurchasePriceAdjustment (the signed total, which is what every balance reads) and
    one of these per product whose price changed - so the edit screen can show each product's current price and
    the history says exactly what was changed. The original PurchaseItem is never touched.
    """
    __tablename__ = "purchase_price_adjustment_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("purchase_price_adjustments.id", ondelete="CASCADE"),
                                               nullable=False)
    purchase_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_items.id"), nullable=False)
    old_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    new_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    basis_qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)      # kg (crops) or bags (fertilizer)
    basis_unit: Mapped[str] = mapped_column(String(10), nullable=False)           # "kg" | "bag"
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)         # (new - old) x basis_qty

    __table_args__ = (
        Index("idx_ppa_lines_adjustment", "adjustment_id"),
        Index("idx_ppa_lines_item", "purchase_item_id"),
    )

