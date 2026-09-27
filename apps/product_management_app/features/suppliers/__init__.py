# apps/product_management_app/features/receipts_v2/__init__.py
# -*- coding: utf-8 -*-
"""
Rafawa receipt engine (v2).

Public API:
    from apps.product_management_app.features.receipts_v2 import (
        print_receipt, print_delivery_and_full,
        MODE_DELIVERY, MODE_FULL,
        SALE, PURCHASE, ADVANCE, PAYMENT, SALE_RETURN, PURCHASE_RETURN,
    )

    print_receipt(self, SALE, sale_id, MODE_FULL)       # amounts + payments
    print_receipt(self, SALE, sale_id, MODE_DELIVERY)   # products only
"""

from .receipt_models import (          # pure, always safe to import
    MODE_DELIVERY, MODE_FULL,
    SALE, PURCHASE, ADVANCE, PAYMENT, SALE_RETURN, PURCHASE_RETURN,
    ReceiptDoc, ReceiptLine, PaymentLine, ShopInfo, PartyInfo,
)
from .receipt_render import render_receipt
from .shop import get_shop

# Qt-dependent entry points. Guarded so the pure pieces (models/render) can be
# imported and unit-tested even in an environment without PySide2.
try:
    from .receipt_print import (
        print_receipt, print_delivery_and_full, preview_and_print,
    )
except Exception:  # pragma: no cover
    print_receipt = None
    print_delivery_and_full = None
    preview_and_print = None

__all__ = [
    "print_receipt", "print_delivery_and_full", "preview_and_print",
    "render_receipt", "get_shop",
    "MODE_DELIVERY", "MODE_FULL",
    "SALE", "PURCHASE", "ADVANCE", "PAYMENT", "SALE_RETURN", "PURCHASE_RETURN",
    "ReceiptDoc", "ReceiptLine", "PaymentLine", "ShopInfo", "PartyInfo",
]