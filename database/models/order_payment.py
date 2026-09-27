#database/models/order_payment.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, func, ForeignKey, Numeric, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class OrderPayment(Base):
    """
    One shared payment table for both Sales Orders and (later) Purchase
    Orders - not two separate, nearly-identical tables. Matches the same
    proven discriminator pattern the main Payment table already uses for
    entity_type/entity_id: order_type says which kind of order this is
    ("sales_order" today, "purchase_order" once that module exists),
    order_id is that order's own id. No real foreign key on order_id -
    same reasoning as Payment.entity_id: it's polymorphic, pointing to a
    different table depending on order_type, exactly like the proven
    pattern elsewhere in this schema.

    Deliberately NOT the main Payment table - Sales/Purchase Orders are
    their own, isolated subsystem (see SalesOrder's own docstring). This
    table never touches PartyBalanceService or the Activity tab.

    Same proven append-only pattern as the main Payment table: a voided
    payment is never deleted or edited, just marked, so history stays
    complete and auditable.
    """
    __tablename__ = "order_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    order_type: Mapped[str] = mapped_column(
        Enum("sales_order", "purchase_order", name="order_payment_type_enum"),
        nullable=False,
    )
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)

    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    method: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        Enum("completed", "voided", name="order_payment_status_enum"),
        nullable=False, default="completed",
    )
    voided_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)