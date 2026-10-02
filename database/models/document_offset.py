# database/models/document_offset.py
"""
A purchase and a sale of the SAME party set against each other, with no money moving.

    we buy goods from a customer who owes us for an earlier sale  -> the purchase pays the sale
    a goods swap (we buy from him and sell to him in one visit)   -> the two documents cancel out

An offset reduces what BOTH documents still owe by `amount`. It is not cash, so it never touches
the drawer; document_balance counts it as a settlement. Reversing it (status="reversed") restores
both balances; a voided counterpart document releases it automatically.
"""
from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, func

from database.base import Base

OffsetStatusEnum = Enum("active", "reversed", name="document_offset_status_enum")


class DocumentOffset(Base):
    __tablename__ = "document_offsets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    kind = Column(String(20), nullable=False, default="settlement")      # settlement | swap | net_off
    note = Column(String(255), nullable=True)

    status = Column(OffsetStatusEnum, nullable=False, default="active", server_default="active")
    reversed_by_user_id = Column(Integer, nullable=True)
    reversed_at = Column(DateTime, nullable=True)
    reverse_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_offsets_purchase", "purchase_id"),
        Index("idx_offsets_sale", "sale_id"),
        Index("idx_offsets_party", "party_id"),
    )
