# database/models/weigh_ticket.py
"""
Weighing tickets: the weigher's record of a delivery, BEFORE it becomes a purchase.

    WeighTicket (party, status)
      └─ WeighTicketLine (product / area / grade; price is set later by admin/cashier)
           └─ WeighRound (bags + kg for one trip over the scale)

The weigher only ever writes bags and kilograms. Prices, payment and the final purchase
record belong to admin/cashier (see WeighService).
"""
from typing import Optional

from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import relationship

from database.base import Base

WeighStatusEnum = Enum("open", "submitted", "finalized", "cancelled", name="weigh_ticket_status_enum")


class WeighTicket(Base):
    __tablename__ = "weigh_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_no = Column(String(20), unique=True, nullable=True)        # WT-00001

    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False)
    party_role = Column(String(20), nullable=False, default="supplier")  # customer | supplier
    walkin_name = Column(String(120), nullable=True)                   # set when the party is the shared walk-in

    status = Column(WeighStatusEnum, nullable=False, default="open")
    receipt_type = Column(String(20), nullable=True)                   # full | summary (weigher's choice)
    notes = Column(Text, nullable=True)

    weigher_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    submitted_at = Column(DateTime, nullable=True)

    finalized_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)

    cancelled_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(String(255), nullable=True)

    party = relationship("Party")
    lines = relationship("WeighTicketLine", back_populates="ticket", cascade="all, delete-orphan",
                         order_by="WeighTicketLine.sort_order")

    __table_args__ = (
        Index("idx_weigh_tickets_status", "status"),
        Index("idx_weigh_tickets_weigher", "weigher_user_id"),
        Index("idx_weigh_tickets_party", "party_id"),
    )


class WeighTicketLine(Base):
    __tablename__ = "weigh_ticket_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("weigh_tickets.id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    area = Column(String(80), nullable=True)
    grade = Column(String(40), nullable=True)

    # The price now being worked with (starts as the weigher's, admin / cashier confirm or change it at intake;
    # a finalized ticket's real price lives on its purchase). Price per kg.
    unit_price = Column(Numeric(12, 2), nullable=True)
    # The weigher's own price: what they set after weighing. Admin / cashier changes never touch it, so the
    # weigher's screens and receipts keep showing what they agreed at the scale.
    weigher_price = Column(Numeric(12, 2), nullable=True)

    ticket = relationship("WeighTicket", back_populates="lines")
    product = relationship("Product")
    rounds = relationship("WeighRound", back_populates="line", cascade="all, delete-orphan",
                          order_by="WeighRound.round_no")

    __table_args__ = (Index("idx_weigh_lines_ticket", "ticket_id"),)


class WeighRound(Base):
    __tablename__ = "weigh_rounds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    line_id = Column(Integer, ForeignKey("weigh_ticket_lines.id", ondelete="CASCADE"), nullable=False)
    round_no = Column(Integer, nullable=False)

    bags = Column(Numeric(8, 2), nullable=False)                       # 1, 2, 3 or a partial bag count
    weight_kg = Column(Numeric(10, 2), nullable=False)

    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    edited_by_user_id = Column(Integer, nullable=True)                 # admin/cashier correction

    line = relationship("WeighTicketLine", back_populates="rounds")

    __table_args__ = (Index("idx_weigh_rounds_line", "line_id"),)
