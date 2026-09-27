from sqlalchemy import String, Integer, Boolean, DateTime, func, Enum
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base


PartyTypeEnum = Enum("customer", "walkin", "supplier", "company", name="party_type_enum")


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    party_type: Mapped[str] = mapped_column(PartyTypeEnum, nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)

    phone: Mapped[str] = mapped_column(String(40), nullable=True, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Capability flags, independent of party_type. A real business can be
    # BOTH - we buy from them AND occasionally sell to them (a fertilizer
    # dealer who also buys surplus crops, for example). party_type stays
    # as the party's PRIMARY label for how it was first created; these two
    # flags are the actual truth used to decide which lists/pickers a party
    # appears in, and they are self-maintaining: SaleService.record() sets
    # is_customer, PurchaseService.record() sets is_supplier, the moment a
    # real transaction happens - no manual toggle, no separate duplicate row.
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped["DateTime"] = mapped_column(
        MySQLDateTime(fsp=6), server_default=func.now(6), onupdate=func.now(6), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Party id={self.id} type={self.party_type!r} name={self.display_name!r}>"