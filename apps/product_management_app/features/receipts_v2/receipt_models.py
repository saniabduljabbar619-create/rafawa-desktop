# apps/product_management_app/features/receipts_v2/receipt_models.py
# -*- coding: utf-8 -*-
"""
Normalized receipt data model.

Every document (sale, intake/purchase, advance, return, payment) is turned
into ONE shape - ReceiptDoc - by the builders in receipt_data.py, and then
rendered by receipt_render.py. Nothing here imports Qt or the database, so it
can be created and tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ---------------------------------------------------------------------------
# Document kinds and print modes
# ---------------------------------------------------------------------------

# doc_type values (what the document IS)
SALE = "sale"
PURCHASE = "purchase"          # intake
ADVANCE = "advance"
PAYMENT = "payment"
SALE_RETURN = "sale_return"
PURCHASE_RETURN = "purchase_return"

# render modes (HOW MUCH to show) -> your two receipt types
MODE_DELIVERY = "delivery"     # products only, NO money / payments
MODE_FULL = "full"             # products + amounts + payments + balance

# A plain-text marker for "make this bold" when printed - \x01 is a
# control character that never appears in genuine receipt text, so it's
# safe as a sentinel. Builders/renderers wrap text this way; receipt_
# print.py's _encode() translates it to real ESC/POS bold codes right
# before sending to the printer. The QPrinter/PDF fallback paths strip
# it instead, since they have no bold support at all.
BOLD_MARKER = "\x01"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class ShopInfo:
    name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    footer_note: str = "Thank you for your patronage!"


@dataclass
class PartyInfo:
    name: str = ""
    phone: str = ""
    kind: str = ""             # customer / walkin / supplier / company


@dataclass
class ReceiptLine:
    """One product line."""
    name: str
    bags: float = 0.0
    kg: float = 0.0
    is_fertilizer: bool = False
    rate: float = 0.0          # per kg (crops) or per bag (fertilizer)
    amount: float = 0.0        # line total (money-mode only)


@dataclass
class PaymentLine:
    amount: float = 0.0
    method: str = ""
    when: Optional[datetime] = None


@dataclass
class ReceiptDoc:
    """The single shape every renderer consumes."""
    doc_type: str                       # one of the constants above
    title: str                          # printed heading, e.g. "SALES RECEIPT"
    receipt_no: str                     # e.g. "SAL-00012"

    shop: ShopInfo = field(default_factory=ShopInfo)
    party: PartyInfo = field(default_factory=PartyInfo)

    when: Optional[datetime] = None
    served_by: str = ""                 # username / cashier
    ledger: str = ""                    # crops / fertilizers (optional label)
    reference: str = ""                 # linked doc, e.g. return -> "SAL-00012"
    note: str = ""

    lines: List[ReceiptLine] = field(default_factory=list)
    payments: List[PaymentLine] = field(default_factory=list)

    # Money summary (money-mode only). Builders fill these from DB truth.
    subtotal: float = 0.0
    discount: float = 0.0
    total: float = 0.0
    paid: float = 0.0

    @property
    def balance(self) -> float:
        return round(float(self.total) - float(self.paid), 2)

    @property
    def has_money(self) -> bool:
        return self.doc_type in (SALE, PURCHASE, ADVANCE, PAYMENT,
                                 SALE_RETURN, PURCHASE_RETURN)