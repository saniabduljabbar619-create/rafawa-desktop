#database/models/sales_order_item.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, Numeric, String, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class SalesOrderItem(Base):
    """
    One product line on a Sales Order - crops (measured by weight, kg) or
    fertilizer (measured by bags), matching the same dual-unit convention
    used throughout this schema (SaleItem, PurchaseItem, StockMovement):
    each row carries both a *_qty_bags and a *_weight_kg field, and only
    the one matching the line's own product_type_snapshot is ever
    genuinely used - the other stays 0. Never both at once for a single
    line, and never inferred - product_type_snapshot says which applies.

    Carries two, deliberately separate prices:

      my_price       - what we expected to get, entered at dispatch time.
                        Kept purely for comparison; NEVER used in the final
                        calculation.
      company_price  - what the buying company actually confirms paying,
                        which may differ from my_price depending on the
                        company's own assessment of quality. This is the
                        ONLY price that ever determines the real, final
                        amount: Final Amount = company_qty * company_price,
                        where company_qty is whichever unit (kg or bags)
                        this line's product_type_snapshot uses.

    The dispatched_* fields are what physically left the warehouse
    (recorded at dispatch, when the corresponding StockMovement is
    created). company_*, accepted_*, and rejected_* all stay NULL until
    the order is confirmed - accepted + rejected must always equal
    company_qty (in whichever unit applies) once set, enforced by the
    service layer, not just trusted from the caller.
    """
    __tablename__ = "sales_order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False)
    sales_order = relationship("SalesOrder", back_populates="items")

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product = relationship("Product")

    # "crops" or "fertilizer" - stored explicitly (rather than assumed or
    # looked up live) so every reader can verify it directly, matching how
    # every other item table in this schema snapshots type at the moment
    # of the transaction, not at read time.
    product_type_snapshot: Mapped[str] = mapped_column(String(20), nullable=False, default="crops")

    # ---- at dispatch time - only ONE of each pair is ever genuinely used,
    # matching product_type_snapshot; the other stays 0 ----
    dispatched_weight_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    dispatched_qty_bags: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    my_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    # ---- at confirmation time - NULL until then ----
    company_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    company_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    company_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    accepted_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    accepted_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    rejected_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    rejected_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # accepted_qty * company_price (whichever unit applies) - stored once
    # confirmed, rather than recomputed every read, matching line_total
    # elsewhere in this schema.
    final_line_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        # Replaces the earlier "dispatched_weight_kg > 0" constraint, which
        # would have wrongly rejected every genuine fertilizer line (where
        # weight is legitimately 0 and bags is what's real) - matches the
        # same "at least one of the pair" pattern already used on
        # StockMovement for this exact reason.
        CheckConstraint(
            "dispatched_weight_kg > 0 OR dispatched_qty_bags > 0",
            name="chk_sales_order_items_dispatched_qty"
        ),
        Index("idx_sales_order_items_order", "sales_order_id"),
        Index("idx_sales_order_items_product", "product_id"),
    )