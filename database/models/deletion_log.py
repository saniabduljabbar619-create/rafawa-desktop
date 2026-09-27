# database/models/deletion_log.py
#
# Audit trail for hard-deleted records. Since Purchases / Sales / Advances
# are permanently removed from their own tables, this is the only place
# that still remembers a deleted row existed: who deleted it, when, and
# what it looked like right before deletion (as a JSON snapshot).
#
# NOTE: adjust the `Base` import below to match wherever your other models
# import it from (e.g. `from database.base import Base` or
# `from database.db import Base`) — it wasn't in the files shared with me,
# so this is a best guess based on common project layout.

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func

from database.base import Base  # ⚠️ adjust if your Base lives elsewhere


class DeletionLog(Base):
    __tablename__ = "deletion_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # What was deleted
    entity_type = Column(String(32), nullable=False)   # "purchase" | "sale" | "advance"
    entity_id = Column(Integer, nullable=False)

    # Full snapshot of the row (and anything useful about it) as JSON text,
    # captured immediately before the DELETE. This is what lets an admin
    # later answer "what was purchase #123 before it was removed?"
    entity_snapshot = Column(Text, nullable=True)

    # Who did it / when / why
    deleted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    deleted_at = Column(DateTime, server_default=func.now(), nullable=False)
    reason = Column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DeletionLog id={self.id} entity_type={self.entity_type} "
            f"entity_id={self.entity_id} deleted_by_user_id={self.deleted_by_user_id}>"
        )