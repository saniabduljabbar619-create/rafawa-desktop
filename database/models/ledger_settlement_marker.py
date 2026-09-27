#database/models/ledger_settlement_marker.py
from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.sql import func
from database.base import Base


class LedgerSettlementMarker(Base):
    """
    Records the exact moment a party's ledger category - Payables,
    Receivables, Advances, or all three together - genuinely settled to
    zero, checked right after a payment commits (see LedgerSettlement
    Service). Deliberately a real, persisted event rather than something
    recomputed by scanning history each time the Activity tab opens:
    reconstructing "the exact moment all three hit zero together"
    retroactively would be far more fragile than recording it once, at
    the real moment it happened.

    category: "payables" | "receivables" | "advances" | "all" - "all"
    is the more prominent, bolder marker shown when every category is
    genuinely zero at once; the other three are the quieter, per-
    category milestones.
    """
    __tablename__ = "ledger_settlement_markers"

    id = Column(Integer, primary_key=True, autoincrement=True)

    party_id = Column(Integer, nullable=False, index=True)
    category = Column(
        Enum("payables", "receivables", "advances", "all", name="ledger_settlement_category"),
        nullable=False,
    )

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())