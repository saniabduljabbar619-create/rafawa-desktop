# database/models/audit_log.py
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class AuditLog(Base):
    """
    Append-only record of WHO did WHAT to financial/administrative data.

    Written for every void, hard delete, user-administration change and
    cross-cashier settlement. Never updated or deleted by the app.
    """
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Snapshot of the actor (kept even if the user is later renamed/disabled).
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    action: Mapped[str] = mapped_column(String(60), nullable=False)          # e.g. "sale.void"
    entity_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # JSON

    __table_args__ = (
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_action", "action"),
    )
