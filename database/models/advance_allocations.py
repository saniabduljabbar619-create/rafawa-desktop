from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, DateTime, Index
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.sql import func
from database.base import Base


class AdvanceAllocation(Base):
    __tablename__ = "advance_allocations"

    id = Column(Integer, primary_key=True)

    advance_id = Column(Integer, ForeignKey("advances.id"), nullable=False)

    entity_type = Column(String(20), nullable=False)
    # sale | purchase

    entity_id = Column(Integer, nullable=False)

    amount = Column(DECIMAL(14, 2), nullable=False)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(MySQLDateTime(fsp=6), server_default=func.now(6), onupdate=func.now(6))

    __table_args__ = (
        Index("idx_advance_alloc_advance", "advance_id"),
        Index("idx_advance_alloc_entity", "entity_type", "entity_id"),
    )