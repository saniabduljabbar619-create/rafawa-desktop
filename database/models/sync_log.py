#database/models/sync_log.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class SyncLog(Base):
    """
    One row per sync attempt for one table - a genuine audit trail, not
    just a "last status" flag. Answers "is this actually backing up" and
    "when did it last succeed" precisely, rather than trusting that a
    background thread quietly worked. Local-only, like SyncState -
    never pushed to the cloud itself.
    """
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    table_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)

    rows_pushed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)