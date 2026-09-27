from typing import Optional
from sqlalchemy import Integer, ForeignKey, Enum, Numeric, String, CheckConstraint, Index, DateTime, func
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


ProductTypeSnapshotEnum = Enum("crops", "fertilizer", name="sale_product_type_snapshot_enum")


class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    sale = relationship("Sale", back_populates="items")

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product = relationship("Product")

    product_type_snapshot: Mapped[str] = mapped_column(ProductTypeSnapshotEnum, nullable=False)

    qty_bags: Mapped[int] = mapped_column(Integer, nullable=False)
    total_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Sale rate = price per kg (crops) OR price per bag (fertilizer)
    unit_sale_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    # What these goods COST US, frozen at the moment of sale, so that a later
    # edit to Product.default_cost_rate can never rewrite historical profit.
    # NULL on rows written before this column existed -> every reader MUST
    # COALESCE back to Product.default_cost_rate. Quick Purchase will stamp the
    # real dealer price here instead of the default.
    unit_cost_snapshot: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)

    line_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    # A genuine, per-line discount entered at checkout time (e.g. "Fertilizer
    # A gets ₦2,000/bag off, Fertilizer B gets ₦0") - deliberately kept
    # SEPARATE from line_total rather than baked into it, so the original,
    # undiscounted subtotal is never lost. The net amount owed for this line
    # is always line_total - line_discount, computed wherever it's needed,
    # not stored pre-subtracted.
    line_discount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default="0")

    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Line items previously had no timestamp at all - added specifically
    # as the basis for cloud sync's change-detection cursor.
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped["DateTime"] = mapped_column(
        MySQLDateTime(fsp=6), server_default=func.now(6), onupdate=func.now(6), nullable=False
    )

    bags = relationship("SaleItemBag", back_populates="sale_item", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("qty_bags >= 1", name="chk_sale_items_qty_bags"),
        Index("idx_sale_items_sale", "sale_id"),
        Index("idx_sale_items_product", "product_id"),
    )