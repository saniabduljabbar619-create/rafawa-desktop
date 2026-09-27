from sqlalchemy import Integer, ForeignKey, Numeric, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class SaleItemBag(Base):
    __tablename__ = "sale_item_bags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sale_item_id: Mapped[int] = mapped_column(ForeignKey("sale_items.id", ondelete="CASCADE"), nullable=False)
    sale_item = relationship("SaleItem", back_populates="bags")

    bag_no: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint("sale_item_id", "bag_no", name="uq_sale_item_bags_item_bagno"),
        CheckConstraint("weight_kg > 0", name="chk_sale_item_bags_weight"),
    )
