# shared/uix/widgets/product_select.py
"""One rule for every product dropdown: it opens on a "Select Product" placeholder, even when
products exist, so nothing is picked by accident. The placeholder's data is None, so
``combo.currentData()`` is falsy until a real product is chosen."""
from __future__ import annotations

SELECT_PRODUCT = "Select Product"


def add_select_placeholder(combo, text: str = SELECT_PRODUCT) -> None:
    """Insert the placeholder as item 0 of an already-filled combo and make it the current item."""
    combo.insertItem(0, text, None)
    combo.setCurrentIndex(0)
