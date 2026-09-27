# database/models/legacy_record.py
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (
    Column, Integer, Date, DateTime, String, Text,
    Enum, ForeignKey
)

from database.base import Base


LEGACY_DIRECTION = ("customer_owes_us", "we_owe_customer")
LEGACY_STATUS = ("open", "cleared")


class LegacyRecord(Base):
    __tablename__ = "legacy_records"

    id = Column(Integer, primary_key=True)

    # Party (customer/supplier)
    party_id = Column(Integer, ForeignKey("party.id"), nullable=False, index=True)

    # "As at" date (e.g. 2022-12-31)
    as_of_date = Column(Date, nullable=False)

    # Who owes who
    direction = Column(Enum(*LEGACY_DIRECTION, name="legacy_direction"), nullable=False)

    # Open / cleared
    status = Column(Enum(*LEGACY_STATUS, name="legacy_status"), nullable=False, default="open")

    # Notebook meta
    title = Column(String(120), nullable=True)
    note = Column(Text, nullable=True)

    # No created_by_user_id, just a name
    recorded_by_name = Column(String(80), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    cleared_at = Column(DateTime, nullable=True)
    cleared_by_name = Column(String(80), nullable=True)
