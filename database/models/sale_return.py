# database/models/sale_return.py

from datetime import datetime

from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from database.base import Base


class SaleReturn(Base):
    __tablename__ = "sale_returns"

    id = Column(Integer, primary_key=True, autoincrement=True)

    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False, index=True)
    sale_item_id = Column(Integer, ForeignKey("sale_items.id"), nullable=False, index=True)

    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)

    # Snapshots: MUST match sale_item snapshot at time of sale
    product_type_snapshot = Column(String(32), nullable=False)   # "crops" | "fertilizers"
    unit = Column(String(16), nullable=True)                     # e.g. "kg"
    unit_price_snapshot = Column(Float, nullable=False, default=0.0)

    qty_kg = Column(Float, nullable=False, default=0.0)
    qty_bags = Column(Integer, nullable=False, default=0)

    amount = Column(Float, nullable=False, default=0.0)          # NEGATIVE number

    reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    # Optional relationships (safe even if unused)
    sale = relationship("Sale", lazy="joined", foreign_keys=[sale_id])
    sale_item = relationship("SaleItem", lazy="joined", foreign_keys=[sale_item_id])
