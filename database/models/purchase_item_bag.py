from sqlalchemy import Integer, ForeignKey, Numeric, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class PurchaseItemBag(Base):
    __tablename__ = "purchase_item_bags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    purchase_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_items.id", ondelete="CASCADE"), nullable=False)
    purchase_item = relationship("PurchaseItem", back_populates="bags")

    bag_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..qty_bags
    weight_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("purchase_item_id", "bag_no", name="uq_purchase_item_bags_item_bagno"),
        CheckConstraint("weight_kg > 0", name="chk_purchase_item_bags_weight"),
    )
