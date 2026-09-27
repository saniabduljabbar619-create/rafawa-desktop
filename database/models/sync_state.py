#database/models/sync_state.py
from typing import Optional
from sqlalchemy import Integer, String, DateTime, Boolean, func
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


class SyncState(Base):
    """
    One row per table that participates in cloud sync - the local,
    persistent memory of "how far has this table's sync gotten".

    last_synced_cursor is an updated_at watermark: the next sync run
    only pulls rows where updated_at > last_synced_cursor, so a partial
    table scan finds exactly what's genuinely new or changed since the
    last successful push, never the whole table again. Only advances
    once a full batch has been pushed successfully - a failed or partial
    batch leaves the cursor untouched, so the same rows are simply
    retried next run rather than silently skipped.

    This table itself is local-only - it never gets pushed to the cloud
    (syncing your own sync progress makes no sense).
    """
    __tablename__ = "sync_state"

    table_name: Mapped[str] = mapped_column(String(64), primary_key=True)

    last_synced_cursor: Mapped[Optional["DateTime"]] = mapped_column(MySQLDateTime(fsp=6), nullable=True)
    last_synced_row_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    last_run_at: Mapped[Optional["DateTime"]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )