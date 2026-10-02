# database/models/order_review.py
"""
The review log of a Sales or Purchase Order: notes (a delay, a damaged bag spotted, a call with the driver ...) at
any stage, and CORRECTIONS made after delivery was confirmed (goods found damaged or short, a wrong count, a
price dispute). Rows are never edited or deleted - a correction of a correction is a new row.
"""
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, Numeric, String, Text, func

from database.base import Base


class OrderReview(Base):
    __tablename__ = "order_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_type = Column(String(20), nullable=False)               # sales_order | purchase_order
    order_id = Column(Integer, nullable=False)
    is_correction = Column(Boolean, nullable=False, default=False, server_default="0")
    category = Column(String(30), nullable=False, default="Note")
    text = Column(Text, nullable=False)

    # corrections only
    line_id = Column(Integer, nullable=True)
    rej_before = Column(Numeric(14, 2), nullable=True)
    rej_after = Column(Numeric(14, 2), nullable=True)
    price_before = Column(Numeric(14, 2), nullable=True)
    price_after = Column(Numeric(14, 2), nullable=True)
    total_before = Column(Numeric(14, 2), nullable=True)
    total_after = Column(Numeric(14, 2), nullable=True)
    stock_moved = Column(Boolean, nullable=False, default=False, server_default="0")

    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (Index("idx_order_reviews_order", "order_type", "order_id"),)
