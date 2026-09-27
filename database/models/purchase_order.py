#database/models/purchase_order.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, DateTime, func, Enum, Text, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


# draft         - created, nothing has happened to stock yet, freely editable/cancellable
# in_transit    - the supplier has acknowledged/is sending the order - PURELY
#                 informational, deliberately no stock effect at all here.
#                 We're the receiving party; we can't trust a quantity we
#                 haven't verified ourselves, unlike Sales Order's dispatch
#                 (where WE are the ones sending, so we know for certain).
# confirmed     - we've physically received, weighed/counted, and verified
#                 what actually arrived - the ONLY point stock genuinely
#                 changes. Accepted goods enter Available Stock; rejected
#                 goods never enter it at all (nothing to reverse, since
#                 nothing was ever added for them in the first place).
# cancelled     - withdrawn before confirmation; nothing to reverse, since
#                 stock was never touched before confirm in the first place
PurchaseOrderStatusEnum = Enum(
    "draft", "in_transit", "confirmed", "cancelled",
    name="purchase_order_status_enum",
)


class PurchaseOrder(Base):
    """
    A Purchase Order - buying crops/fertilizer from a supplier, entirely
    separate from the existing, immediate Purchase/Quick Purchase flow.
    Mirrors SalesOrder's own isolation and structure closely, with one
    deliberate, confirmed difference: stock only ever changes at
    "confirmed", never at "in_transit" - see the status enum's own
    comments above for the full reasoning.

    Only connects to the rest of the app through its eventual real-world
    effect: stock increases once confirmed. Everything about how it gets
    there - its own screens, its own workflow - is deliberately its own
    thing, matching Sales Order's own principle exactly.
    """
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)

    # The supplying party - a PurchaseOrderSupplier, deliberately NOT a
    # Party. Confirmed decision: keeps PO fully isolated from the rest
    # of the app, matching Company's exact reasoning for Sales Order,
    # even though "supplier" already exists as a concept elsewhere.
    supplier_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_suppliers.id"), nullable=False)
    supplier = relationship("PurchaseOrderSupplier")

    status: Mapped[str] = mapped_column(PurchaseOrderStatusEnum, nullable=False, default="draft")

    # Set at creation, editable while still a draft - when we expect the
    # supplier to deliver, not when they're actually confirmed to arrive.
    expected_delivery_date: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when the order moves to in_transit - who's bringing the goods,
    # if known at that point. Nullable: not every delivery has a known
    # third-party driver at that stage (supplier's own arrangement,
    # unknown until arrival).
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    driver = relationship("Driver")

    # ---- audit trail - who did what, and when ----
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # "dispatched" here means "marked in_transit in our system" - who
    # recorded that the supplier is now sending, not who physically
    # drove it (that's driver_id above).
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

    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_purchase_orders_supplier", "supplier_id"),
        Index("idx_purchase_orders_status", "status"),
    )