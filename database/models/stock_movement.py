#database/models/stock_movement.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Enum, Numeric, String, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


StockMovementTypeEnum = Enum(
    "purchase_in",
    "sale_out",
    "return_in",
    "adjustment_in",
    "adjustment_out",
    "advance_out",
    name="stock_movement_type_enum",
)

# The single, shared source of truth for which movement types add to or
# subtract from a product's on-hand stock. Previously, this classification
# was independently copy-pasted across eight separate files throughout
# this app, and had genuinely drifted apart: some correctly treated
# ADVANCE_OUT as a real deduction (product physically given out as an
# advance - the goods are just as gone as a sale), others silently
# omitted it entirely - including SaleService._check_stock, the exact
# function that decides whether a sale is even allowed to be recorded,
# meaning it could think more stock was available than truly existed.
#
# Every place in this app that computes on-hand stock should import and
# use these two tuples directly, rather than hand-writing its own copy -
# that's what actually prevents this kind of drift from happening again,
# not just a one-time fix to whichever files happen to be wrong today.
STOCK_IN_TYPES = ("purchase_in", "return_in", "adjustment_in")
STOCK_OUT_TYPES = ("sale_out", "adjustment_out", "advance_out")


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product = relationship("Product")

    movement_type: Mapped[str] = mapped_column(StockMovementTypeEnum, nullable=False)

    qty_bags: Mapped[int] = mapped_column(Integer, nullable=False)
    total_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Optional for audit/profit analysis (store the rate used on the source line)
    unit_rate: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)

    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. 'purchase_item', 'sale_item'
    reference_id: Mapped[int] = mapped_column(Integer, nullable=False)

    party_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parties.id"), nullable=True)
    party = relationship("Party")

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # A movement must genuinely move SOMETHING - but "something" means
        # either a real bag count (fertilizer) OR a real kg weight (crops),
        # not bags specifically. The old version of this constraint required
        # qty_bags >= 1 unconditionally, which silently made every crop-type
        # movement (qty_bags=0, total_weight_kg>0 - correct and used
        # everywhere else in this schema) impossible to insert at all.
        CheckConstraint(
            "qty_bags >= 0 AND (qty_bags >= 1 OR total_weight_kg > 0)",
            name="chk_stock_movements_qty_bags"),
        Index("idx_stock_movements_product", "product_id"),
        Index("idx_stock_movements_ref", "reference_type", "reference_id"),
        Index("idx_stock_movements_created_at", "created_at"),
    )