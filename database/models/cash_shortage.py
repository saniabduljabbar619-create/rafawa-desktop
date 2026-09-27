from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, DECIMAL
from sqlalchemy.sql import func
from database.base import Base


class CashShortage(Base):
    """
    A physical cash-handling mistake, corrected after the fact - not a
    sale/purchase balance problem (the transaction itself was recorded
    correctly), and not a deliberate advance. The classic case: a sale
    for 450,000 is recorded correctly, but the cashier physically hands
    the customer only 430,000 change, discovered later when the customer
    recounts. The company now genuinely owes the customer 20,000 in real
    cash - a standalone debt, unrelated to the sale's own total.

    Deliberately kept OUT of the party summary cards and any balance
    calculation - showing it there would make it look like a deliberate
    advance, which it isn't. It surfaces only via a dedicated banner in
    the payment dialogs, so it stays impossible to miss without being
    confused for something it's not.
    """
    __tablename__ = "cash_shortages"

    id = Column(Integer, primary_key=True, autoincrement=True)

    party_id = Column(Integer, nullable=False, index=True)

    # Which transaction this arose from, purely for context/traceability -
    # never used to affect that transaction's own balance in any way.
    reference_type = Column(Enum("sale", "purchase", name="cash_shortage_reference_type"), nullable=False)
    reference_id = Column(Integer, nullable=False)

    # "owed_to_party" - the classic case: company shorted the customer,
    #                   the company owes them.
    # "owed_to_company" - the mirror case: an overpayment was physically
    #                   handed out, the party owes the company back.
    direction = Column(Enum("owed_to_party", "owed_to_company", name="cash_shortage_direction"), nullable=False)

    amount = Column(DECIMAL(14, 2), nullable=False)

    status = Column(Enum("open", "settled", name="cash_shortage_status"), nullable=False, default="open")

    notes = Column(Text, nullable=True)

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    settled_by_user_id = Column(Integer, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    settlement_notes = Column(Text, nullable=True)