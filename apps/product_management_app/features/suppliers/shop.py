# apps/product_management_app/features/receipts_v2/shop.py
# -*- coding: utf-8 -*-
"""One place for the shop's identity (replaces the copy-pasted blocks)."""

from __future__ import annotations

import os

from .receipt_models import ShopInfo


def get_shop() -> ShopInfo:
    """Shop identity, overridable via environment if ever needed."""
    return ShopInfo(
        name=os.getenv("SHOP_NAME", "Alh. Zakari Rafawa Enterprises"),
        address=os.getenv("SHOP_ADDRESS", "Kwanar Tsaure, Gwarzo L.G.A, Kano State"),
        phone=os.getenv("SHOP_PHONE", "07046329444, 09032371283"),
        email=os.getenv("SHOP_EMAIL", "alhzakarirafawaenterprises@gmail.com"),
        footer_note=os.getenv("SHOP_FOOTER", "Thank you for your patronage!"),
    )