# database/models/debt.py
"""
The Debt Center's own book: people we owe (creditors) and people who owe us (debtors).

Deliberately STANDALONE. These tables do not reference parties, customers, suppliers, sales or
purchases, and nothing else in the app reads them: recording a debt here never changes a customer's
balance, a sale, a purchase or the drawer. It is a ledger kept by hand, one sheet per person.

    DebtPerson (creditor | debtor)
      └─ DebtEntry     a dated debt: what it was for and the amount ("owed")
           └─ DebtPayment   each payment made against that one debt, saved on its own

A row's paid-so-far is the sum of its payments, so paying a debt in instalments (30m, then 10m, ...) never
overwrites an earlier payment. balance = sum(owed) - sum(payments) over rows and payments not removed.
"""
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import relationship

from database.base import Base

DebtKindEnum = Enum("creditor", "debtor", name="debt_person_kind_enum")


class DebtPerson(Base):
    __tablename__ = "debt_people"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(DebtKindEnum, nullable=False)
    name = Column(String(150), nullable=False)
    phone = Column(String(40), nullable=True)
    address = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, server_default="0")
    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    entries = relationship("DebtEntry", back_populates="person", cascade="all, delete-orphan")

    __table_args__ = (Index("idx_debt_people_kind", "kind"), Index("idx_debt_people_name", "name"))


class DebtEntry(Base):
    __tablename__ = "debt_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(Integer, ForeignKey("debt_people.id", ondelete="CASCADE"), nullable=False)
    entry_date = Column(Date, nullable=False)
    details = Column(String(255), nullable=False)
    owed = Column(Numeric(14, 2), nullable=False, default=0, server_default="0")      # adds to the debt
    method = Column(String(30), nullable=True)
    note = Column(Text, nullable=True)

    is_removed = Column(Boolean, nullable=False, default=False, server_default="0")
    removed_by_user_id = Column(Integer, nullable=True)
    removed_at = Column(DateTime, nullable=True)
    removed_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_by_user_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    person = relationship("DebtPerson", back_populates="entries")
    payments = relationship("DebtPayment", back_populates="entry", cascade="all, delete-orphan",
                            order_by="DebtPayment.pay_date, DebtPayment.id")

    __table_args__ = (Index("idx_debt_entries_person", "person_id"), Index("idx_debt_entries_date", "entry_date"))


class DebtPayment(Base):
    __tablename__ = "debt_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(Integer, ForeignKey("debt_entries.id", ondelete="CASCADE"), nullable=False)
    pay_date = Column(Date, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    method = Column(String(30), nullable=True)
    note = Column(Text, nullable=True)

    is_removed = Column(Boolean, nullable=False, default=False, server_default="0")
    removed_by_user_id = Column(Integer, nullable=True)
    removed_at = Column(DateTime, nullable=True)
    removed_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    entry = relationship("DebtEntry", back_populates="payments")

    __table_args__ = (Index("idx_debt_payments_entry", "entry_id"),)
