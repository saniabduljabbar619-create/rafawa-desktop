# database/models/product.py

from typing import Optional

from sqlalchemy import (
    String, Integer, Boolean, DateTime, func, Enum,
    ForeignKey, Numeric, Index
)
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


ProductTypeEnum = Enum("crops", "fertilizer", name="product_type_enum")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    product_type: Mapped[str] = mapped_column(ProductTypeEnum, nullable=False)

    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )

    sku: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    unit_label: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Defaults for UI dialogs
    default_cost_rate: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    default_sale_rate: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_products_type", "product_type"),
    )

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.name!r} type={self.product_type!r}>"
