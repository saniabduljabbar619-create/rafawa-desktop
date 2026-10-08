# database/models/quick_purchase_order.py
"""
Quick Purchase orders: a shortfall we are sourcing from suppliers, tracked until it is settled.

    QuickPurchaseOrder (status: draft -> pending -> completed | cancelled)
      ├─ QuickPurchaseLine   (one product + quantity + the supplier it is currently asked from)
      │    └─ QuickPurchaseRoute (every supplier that line was ever routed to, and what came of it)

draft      planned, nothing sent anywhere - freely editable / deletable.
pending    sent to supplier(s), waiting for the goods. Still no stock effect (we only trust goods once
           they are here), so cancelling or deleting at this point leaves stock and books untouched.
completed  every line is either received (one real Purchase per supplier, stock in) or given up.
cancelled  withdrawn - or closed with only what was already in stock ("sell available").

If a supplier does not have the goods, the line is ROUTED to another supplier (history kept in
QuickPurchaseRoute) instead of being bought blindly. The Purchase is created only on receipt.
"""
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import relationship

from database.base import Base

QuickPurchaseStatusEnum = Enum("draft", "pending", "completed", "cancelled", name="quick_purchase_status_enum")


class QuickPurchaseOrder(Base):
    __tablename__ = "quick_purchase_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String(20), unique=True, nullable=True)           # QP-00001
    status = Column(QuickPurchaseStatusEnum, nullable=False, default="draft")

    customer_name = Column(String(120), nullable=True)                  # who the goods are being found for
    notes = Column(Text, nullable=True)
    close_reason = Column(String(255), nullable=True)                   # why cancelled / closed early

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    lines = relationship("QuickPurchaseLine", back_populates="order", cascade="all, delete-orphan",
                         order_by="QuickPurchaseLine.sort_order")

    __table_args__ = (Index("idx_qp_orders_status", "status"),)


class QuickPurchaseLine(Base):
    __tablename__ = "quick_purchase_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("quick_purchase_orders.id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    qty_bags = Column(Integer, nullable=False, default=0)
    total_weight_kg = Column(Numeric(12, 2), nullable=False, default=0)   # crops only; fertilizer is bags
    unit_cost = Column(Numeric(12, 2), nullable=False, default=0)         # per bag (fertilizer) / per kg (crops)

    supplier_id = Column(Integer, ForeignKey("parties.id"), nullable=True)   # who it is currently asked from
    state = Column(String(12), nullable=False, default="open")               # open | received | dropped
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)  # set when received

    order = relationship("QuickPurchaseOrder", back_populates="lines")
    product = relationship("Product")
    supplier = relationship("Party", foreign_keys=[supplier_id])
    routes = relationship("QuickPurchaseRoute", cascade="all, delete-orphan",
                          order_by="QuickPurchaseRoute.id")

    __table_args__ = (Index("idx_qp_lines_order", "order_id"),)


class QuickPurchaseRoute(Base):
    """One supplier a line was asked from. outcome: asked -> unavailable | received."""
    __tablename__ = "quick_purchase_routes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    line_id = Column(Integer, ForeignKey("quick_purchase_lines.id", ondelete="CASCADE"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("parties.id"), nullable=False)
    outcome = Column(String(12), nullable=False, default="asked")
    note = Column(String(255), nullable=True)
    by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    supplier = relationship("Party", foreign_keys=[supplier_id])

    __table_args__ = (Index("idx_qp_routes_line", "line_id"),)
