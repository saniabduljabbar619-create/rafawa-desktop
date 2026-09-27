#database/models/sales_order.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Enum, Text, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


# draft         - created, nothing has happened to stock yet, freely editable/cancellable
# in_transit    - dispatched: goods deducted from Available Stock, awaiting the
#                 buying company's confirmation of what they actually received
# confirmed     - the company has confirmed weight/price/accepted/rejected -
#                 the order is finalized, accepted goods are sold, rejected
#                 goods have been returned to Available Stock
# cancelled     - withdrawn before dispatch; nothing to reverse, since stock
#                 was never touched at the draft stage in the first place
SalesOrderStatusEnum = Enum(
    "draft", "in_transit", "confirmed", "cancelled",
    name="sales_order_status_enum",
)


class SalesOrder(Base):
    """
    A Sales Order - selling crops to a company, entirely separate from the
    existing, immediate Sale/checkout flow. Only connects to the rest of
    the app through its eventual real-world effects: stock moves when
    goods are dispatched, party balances update when the order is
    confirmed and priced. Everything about how it gets there - its own
    screens, its own workflow - is deliberately its own thing.

    Stock is untouched at "draft" - creating an order commits nothing.
    Only "in_transit" (dispatch) and "confirmed" (accepted/rejected split)
    ever cause a real StockMovement, matching the client's own principle:
    stock only changes on approval, never before.
    """
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)

    # The buying company - a Company, deliberately NOT a Party. A
    # company is never a customer and never will be; mixing it into
    # Party would pollute the regular customer dropdowns used
    # throughout the rest of the app and risk it picking up a real
    # sale/purchase balance through PartyBalanceService.
    buyer_company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    buyer = relationship("Company")

    status: Mapped[str] = mapped_column(SalesOrderStatusEnum, nullable=False, default="draft")

    # Set at creation, editable while still a draft - when the buyer
    # expects the goods, not when they're actually confirmed to arrive.
    expected_delivery_date: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set at dispatch, not creation - that's the real moment a driver
    # and vehicle actually get involved. Nullable: not every dispatch
    # necessarily has a third-party driver (buyer pickup, company
    # vehicle with no separate driver record needed).
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    driver = relationship("Driver")

    # ---- audit trail - who did what, and when ----
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    dispatched_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    dispatched_by = relationship("User", foreign_keys=[dispatched_by_user_id])
    dispatched_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    confirmed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id])
    confirmed_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    cancelled_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancelled_by = relationship("User", foreign_keys=[cancelled_by_user_id])
    cancelled_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Real, final totals - only meaningful once confirmed. Kept NULL until
    # then rather than defaulted to 0, so "not yet priced" is never
    # confused with "genuinely priced at zero".
    final_total_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items = relationship("SalesOrderItem", back_populates="sales_order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_sales_orders_buyer", "buyer_company_id"),
        Index("idx_sales_orders_status", "status"),
    )