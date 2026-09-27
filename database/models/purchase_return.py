#database/models/purchase_return.py
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    DateTime,
    String,
    Float,
)
from sqlalchemy.orm import relationship

from database.base import Base



class PurchaseReturn(Base):
    __tablename__ = "purchase_returns"

    id = Column(Integer, primary_key=True, autoincrement=True)

    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False, index=True)
    purchase_item_id = Column(Integer, ForeignKey("purchase_items.id"), nullable=False, index=True)

    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False, index=True)  # supplier
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)

    product_type_snapshot = Column(String(32), nullable=False)   # crops | fertilizer
    unit = Column(String(16), nullable=True)                     # kg
    unit_cost_snapshot = Column(Float, nullable=False, default=0.0)

    qty_kg = Column(Float, nullable=False, default=0.0)
    qty_bags = Column(Integer, nullable=False, default=0)

    amount = Column(Float, nullable=False, default=0.0)          # NEGATIVE

    reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    purchase = relationship("Purchase", lazy="joined", foreign_keys=[purchase_id])
    purchase_item = relationship("PurchaseItem", lazy="joined", foreign_keys=[purchase_item_id])
