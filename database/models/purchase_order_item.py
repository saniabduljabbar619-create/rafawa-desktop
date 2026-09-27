#database/models/purchase_order_item.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, Numeric, String, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class PurchaseOrderItem(Base):
    """
    One product line on a Purchase Order - crops (by weight, kg) or
    fertilizer (by bags), matching the same dual-unit convention used
    throughout this schema. Mirrors SalesOrderItem's structure, with
    field names reflecting the reversed direction (we're ordering and
    receiving, not dispatching and selling) and the confirmed stock-
    timing difference: nothing here ever creates a StockMovement until
    the order is confirmed - "ordered" and "expected_price" are just
    our own plan, never trusted as real stock on their own.

    Carries two, deliberately separate prices:

      expected_price  - what we expected to pay, entered at order
                        creation. Kept purely for comparison; NEVER
                        used in the final calculation.
      received_price  - what we actually agree to pay once the goods
                        arrive and are verified, which may differ from
                        expected_price. This is the ONLY price that
                        ever determines the real, final amount:
                        Final Amount = accepted_qty * received_price,
                        where accepted_qty is whichever unit (kg or
                        bags) this line's product_type_snapshot uses.

    received_*, accepted_*, and rejected_* all stay NULL until the order
    is confirmed - accepted + rejected must always equal received_qty
    (in whichever unit applies) once set, enforced by the service layer,
    not just trusted from the caller. Only accepted_* ever enters
    Available Stock; rejected_* never does, since nothing was ever added
    for it to begin with (unlike Sales Order's rejected, which reverses
    a movement that already happened at dispatch).
    """
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    purchase_order = relationship("PurchaseOrder", back_populates="items")

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product = relationship("Product")

    # "crops" or "fertilizer" - stored explicitly (rather than assumed or
    # looked up live) so every reader can verify it directly, matching how
    # every other item table in this schema snapshots type at the moment
    # of the transaction, not at read time.
    product_type_snapshot: Mapped[str] = mapped_column(String(20), nullable=False, default="crops")

    # ---- at order creation - only ONE of each pair is ever genuinely
    # used, matching product_type_snapshot; the other stays 0 ----
    ordered_weight_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    ordered_qty_bags: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    expected_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    # ---- at confirmation time - NULL until then, and the ONLY point any
    # of this ever creates a real StockMovement ----
    received_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    received_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    received_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    accepted_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    accepted_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    rejected_weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    rejected_qty_bags: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # accepted_qty * received_price (whichever unit applies) - stored once
    # confirmed, rather than recomputed every read, matching final_line_
    # amount on SalesOrderItem.
    final_line_amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "ordered_weight_kg > 0 OR ordered_qty_bags > 0",
            name="chk_purchase_order_items_ordered_qty"
        ),
        Index("idx_purchase_order_items_order", "purchase_order_id"),
        Index("idx_purchase_order_items_product", "product_id"),
    )