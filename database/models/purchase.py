#database/models/purchase.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Enum, Text, Numeric, String, Index, Column
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


PurchaseStatusEnum = Enum("draft", "completed", "voided", name="purchase_status_enum")


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    supplier_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), nullable=False)
    supplier = relationship("Party")
    walkin_name = Column(String(150), nullable=True)   # ← ADD THIS

    purchase_no: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)

    status: Mapped[str] = mapped_column(PurchaseStatusEnum, nullable=False, default="draft")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_quick: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    linked_sale_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sales.id"), nullable=True)

    subtotal: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    discount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User")

    completed_at: Mapped["Optional[DateTime]"] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped["DateTime"] = mapped_column(
        MySQLDateTime(fsp=6), server_default=func.now(6), onupdate=func.now(6), nullable=False
    )

    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_purchases_supplier", "supplier_party_id"),
        Index("idx_purchases_status", "status"),
        Index("idx_purchases_created_by", "created_by_user_id"),
    )