# database/models/expense.py
"""
Money the business spends that is not stock: rent, wages, fuel, transport, repairs...

Rows are never deleted. A mistaken expense is voided (status="voided") with a reason, so the
books keep the trail; every total in the app excludes voided rows.
"""
from sqlalchemy import Column, Date, DateTime, Enum, Index, Integer, Numeric, String, Text, func

from database.base import Base

ExpenseStatusEnum = Enum("active", "voided", name="expense_status_enum")


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_no = Column(String(20), unique=True, nullable=True)          # EXP-00001

    expense_date = Column(Date, nullable=False)
    category = Column(String(60), nullable=False)
    description = Column(String(255), nullable=False)
    paid_to = Column(String(120), nullable=True)                          # payee
    amount = Column(Numeric(14, 2), nullable=False)
    method = Column(String(30), nullable=True)                            # cash / transfer / pos / cheque
    reference = Column(String(80), nullable=True)                         # receipt / transfer number
    notes = Column(Text, nullable=True)

    status = Column(ExpenseStatusEnum, nullable=False, default="active", server_default="active")
    voided_by_user_id = Column(Integer, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_expenses_date", "expense_date"),
        Index("idx_expenses_category", "category"),
        Index("idx_expenses_status", "status"),
    )
