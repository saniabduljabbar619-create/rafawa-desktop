# database/models/payment.py
from database.base import Base
from sqlalchemy import Column, Integer, String, DateTime, Enum, DECIMAL
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.sql import func


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    entity_type = Column(Enum("sale", "purchase", "advance"), nullable=False)
    entity_id = Column(Integer, nullable=False)

    party_id = Column(Integer, nullable=False)
    direction = Column(Enum("in", "out"), nullable=False)

    amount = Column(DECIMAL(14, 2), nullable=False, default=0)
    method = Column(String(40), nullable=True)
    notes = Column(String(255), nullable=True)

    # A voided payment is never deleted or edited in place - every balance
    # calculation across the app excludes it, but the row itself stays
    # forever as the audit trail. Mirrors how Advance/Sale/Purchase already
    # use a status enum rather than a hard delete for the same reason.
    status = Column(Enum("completed", "voided", name="payment_status_enum"),
                     nullable=False, default="completed", server_default="completed")
    voided_at = Column(DateTime, nullable=True)
    voided_by_user_id = Column(Integer, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(MySQLDateTime(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6))

    # -------------------------------------------------
    # SERIALIZATION
    # -------------------------------------------------
    def to_dict(self):
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "party_id": self.party_id,
            "direction": self.direction,
            "amount": float(self.amount or 0),
            "method": self.method or "",
            "notes": self.notes or "",
            "status": self.status or "completed",
            "voided_at": self.voided_at,
            "voided_by_user_id": self.voided_by_user_id,
            "void_reason": self.void_reason or "",
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }