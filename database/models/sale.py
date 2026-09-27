#database/models/sale.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Enum, Text, Numeric, String, Index, Column
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


SaleStatusEnum = Enum("draft", "completed", "voided", "returned", name="sale_status_enum")


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    buyer_party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), nullable=False)
    buyer = relationship("Party")
    walkin_name = Column(String(150), nullable=True)   # ← ADD THIS

    sale_no: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)

    status: Mapped[str] = mapped_column(SaleStatusEnum, nullable=False, default="draft")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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

    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_sales_buyer", "buyer_party_id"),
        Index("idx_sales_status", "status"),
        Index("idx_sales_created_by", "created_by_user_id"),
    )