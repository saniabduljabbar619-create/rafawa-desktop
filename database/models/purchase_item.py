#database/models/purchase_item.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, Enum, Numeric, String, CheckConstraint, Index, DateTime, func
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


ProductTypeSnapshotEnum = Enum("crops", "fertilizer", name="product_type_snapshot_enum")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    purchase = relationship("Purchase", back_populates="items")

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product = relationship("Product")

    product_type_snapshot: Mapped[str] = mapped_column(ProductTypeSnapshotEnum, nullable=False)

    qty_bags: Mapped[int] = mapped_column(Integer, nullable=False)
    total_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Purchase rate = cost per kg (crops) OR cost per bag (fertilizer)
    unit_cost_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    line_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Line items previously had no timestamp at all - added specifically
    # as the basis for cloud sync's change-detection cursor.
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped["DateTime"] = mapped_column(
        MySQLDateTime(fsp=6), server_default=func.now(6), onupdate=func.now(6), nullable=False
    )

    bags = relationship("PurchaseItemBag", back_populates="purchase_item", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("qty_bags >= 1", name="chk_purchase_items_qty_bags"),
        Index("idx_purchase_items_purchase", "purchase_id"),
        Index("idx_purchase_items_product", "product_id"),
    )