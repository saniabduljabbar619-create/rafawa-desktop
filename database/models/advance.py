from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, DECIMAL
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.sql import func
from database.base import Base

class Advance(Base):
    __tablename__ = "advances"

    id = Column(Integer, primary_key=True, autoincrement=True)

    party_id = Column(Integer, nullable=False, index=True)
    product_id = Column(Integer, nullable=True)
    direction = Column(Enum("given", "received", name="advance_direction"), nullable=False)
    advance_type = Column(Enum("money", "crops", "fertilizer", name="advance_type"), nullable=False)

    amount = Column(DECIMAL(14, 2), nullable=True)

    qty_bags = Column(Integer, nullable=True)
    total_weight_kg = Column(DECIMAL(10, 2), nullable=True)

    unit_rate = Column(DECIMAL(12, 2), nullable=True)

    notes = Column(Text, nullable=True)
    ledger = Column(String(32), nullable=True)

    status = Column(Enum("draft", "completed", "voided", name="advance_status"), nullable=False, default="draft")

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(MySQLDateTime(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6))