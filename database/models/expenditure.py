# database/models/expenditure.py
"""
Expenditures - larger outlays (equipment, vehicles, building work, stock-up trips), kept in their OWN
table and sheet, apart from Expenses (day-to-day running costs). Same shape as Expense; never mixed.
"""
from sqlalchemy import Column, Date, DateTime, Index, Integer, Numeric, String, Text, func

from database.base import Base
from database.models.expense import ExpenseStatusEnum


class Expenditure(Base):
    __tablename__ = "expenditures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_no = Column(String(20), unique=True, nullable=True)          # EXD-00001

    expense_date = Column(Date, nullable=False)
    category = Column(String(60), nullable=False)
    description = Column(String(255), nullable=False)
    paid_to = Column(String(120), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    method = Column(String(30), nullable=True)
    reference = Column(String(80), nullable=True)
    notes = Column(Text, nullable=True)

    status = Column(ExpenseStatusEnum, nullable=False, default="active", server_default="active")
    voided_by_user_id = Column(Integer, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_expenditures_date", "expense_date"),
        Index("idx_expenditures_category", "category"),
        Index("idx_expenditures_status", "status"),
    )
