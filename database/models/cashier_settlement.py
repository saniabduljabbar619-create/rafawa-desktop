# database/models/cashier_settlement.py
# -*- coding: utf-8 -*-
"""
Persistent inter-cashier settlement ledger (Phase 2).

One row per cross-cashier obligation, keyed idempotently to the Payment that
created it (source_payment_id UNIQUE). DrawerService derives obligations fresh
and upserts them here; OPEN rows drive the "collect / give" verdicts, SETTLED
rows drop out of the verdicts and remain as greyed history.

Direction convention (mirrors DrawerService, one law):
    the underlying Payment's cash direction decides debtor/creditor --
      out -> document CREATOR owes the SETTLER
      in  -> the SETTLER owes the document CREATOR
so by the time a row lands here, debtor_user_id / creditor_user_id are final.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Numeric, DateTime, func
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime

# NOTE: confirm this import matches your other models (e.g. payment.py).
# If your models pull Base from elsewhere, change ONLY this line.
from database.base import Base


class CashierSettlement(Base):
    # NOTE: confirm your table-naming convention (plural vs singular).
    __tablename__ = "cashier_settlements"

    id = Column(Integer, primary_key=True)

    # idempotency key: the Payment that generated this obligation
    source_payment_id = Column(Integer, nullable=False, unique=True, index=True)

    # what the obligation came from (sale / purchase / advance) + its id
    entity_type = Column(String(20), nullable=False, default="")
    entity_id = Column(Integer, nullable=True)

    # who owes whom (already resolved by DrawerService's one law)
    debtor_user_id = Column(Integer, nullable=False, index=True)
    creditor_user_id = Column(Integer, nullable=False, index=True)

    amount = Column(Numeric(18, 2), nullable=False, default=0)

    # open -> still to be settled; settled -> cash physically exchanged
    status = Column(String(12), nullable=False, default="open", index=True)
    note = Column(String(255), nullable=True, default="")

    # local naive datetime, matching the rest of the app's clock
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(MySQLDateTime(fsp=6), nullable=False, default=datetime.now, onupdate=datetime.now)

    settled_by_user_id = Column(Integer, nullable=True)
    settled_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return (f"<CashierSettlement id={self.id} pay={self.source_payment_id} "
                f"{self.debtor_user_id}->{self.creditor_user_id} "
                f"{self.amount} {self.status}>")