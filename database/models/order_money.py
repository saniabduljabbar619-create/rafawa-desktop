# database/models/order_money.py
"""
The money side of Sales / Purchase Orders that is NOT the order's own price.

OrderMoney            cash that moves between us and a company/supplier outside the order total: they gave us money,
                      or we gave them money (advance, loan, refund, "keep this for me"). Optionally tied to one
                      order, otherwise it is on the partner's account. It is only a record until someone applies it
                      to an order, which turns it into a normal OrderPayment. NOT driven by the cash drawer.
OrderExpense          a cost paid for one order - the driver, fuel, food, loading... `recoverable` says whether the
                      partner will pay it back (a receivable, never part of the order value) or it is our own cost.
OrderExpenseRecovery  money the partner paid back against one recoverable expense.

Nothing here is ever deleted: a mistake is voided with a reason, like every other sheet in the app.
"""
from sqlalchemy import Boolean, Column, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, func

from database.base import Base

OrderKindEnum = Enum("sales", "purchase", name="order_money_kind_enum")


class OrderMoney(Base):
    __tablename__ = "order_money"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(OrderKindEnum, nullable=False)                      # sales (companies) | purchase (suppliers)
    partner_id = Column(Integer, nullable=False)                      # companies.id or purchase_order_suppliers.id
    order_id = Column(Integer, nullable=True)                         # NULL = on the partner's account
    direction = Column(Enum("in", "out", name="order_money_dir_enum"), nullable=False)   # in = they gave us
    amount = Column(Numeric(14, 2), nullable=False)
    method = Column(String(30), nullable=True)
    note = Column(String(255), nullable=True)
    entry_date = Column(Date, nullable=False)

    status = Column(Enum("completed", "applied", "voided", name="order_money_status_enum"), nullable=False,
                    default="completed", server_default="completed")
    applied_payment_id = Column(Integer, nullable=True)               # the OrderPayment it became
    voided_at = Column(DateTime, nullable=True)
    voided_by_user_id = Column(Integer, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (Index("idx_order_money_partner", "kind", "partner_id"), Index("idx_order_money_order", "kind", "order_id"))


class OrderExpense(Base):
    __tablename__ = "order_expenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_no = Column(String(20), unique=True, nullable=True)       # OEX-00001
    kind = Column(OrderKindEnum, nullable=False)
    order_id = Column(Integer, nullable=False)
    partner_id = Column(Integer, nullable=False)

    expense_date = Column(Date, nullable=False)
    category = Column(String(60), nullable=False)
    description = Column(String(255), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    paid_to = Column(String(120), nullable=True)
    method = Column(String(30), nullable=True)
    recoverable = Column(Boolean, nullable=False, default=True, server_default="1")   # charged to the partner?

    status = Column(Enum("active", "voided", name="order_expense_status_enum"), nullable=False,
                    default="active", server_default="active")
    voided_at = Column(DateTime, nullable=True)
    voided_by_user_id = Column(Integer, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (Index("idx_order_expenses_order", "kind", "order_id"),
                      Index("idx_order_expenses_partner", "kind", "partner_id"))


class OrderExpenseRecovery(Base):
    __tablename__ = "order_expense_recoveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    expense_id = Column(Integer, ForeignKey("order_expenses.id"), nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    method = Column(String(30), nullable=True)
    note = Column(String(255), nullable=True)
    entry_date = Column(Date, nullable=False)

    status = Column(Enum("completed", "voided", name="order_recovery_status_enum"), nullable=False,
                    default="completed", server_default="completed")
    voided_at = Column(DateTime, nullable=True)
    voided_by_user_id = Column(Integer, nullable=True)
    void_reason = Column(String(255), nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
