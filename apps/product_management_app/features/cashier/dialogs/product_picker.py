# apps/product_management_app/features/cashier/dialogs/product_picker.py
# -*- coding: utf-8 -*-
"""
Shared product picker + lazy-create, for the Bulk Purchase intake dialogs.

Fixes a real data-integrity bug in the old flow: the intake dialogs let the
operator type free-text (a fertilizer "kind" + "pack size", or a crop name),
then ALGORITHMICALLY generated a SKU from that text on every submit. If the
typed text didn't reproduce an existing product's REAL sku exactly, a SECOND,
disconnected Product row got created - same real item, split stock, split
cost history, invisible to each other and to the rest of the app.

This module never regenerates a sku from what's typed. An existing product
is always resolved by its own database id; a genuinely new product gets a
sku generated exactly ONCE, at creation, and that becomes its permanent
identity from then on - matching how every other product in the catalog
already works.
"""
from __future__ import annotations

from typing import Optional, Dict, Any

from PySide2.QtWidgets import QComboBox, QInputDialog, QMessageBox

from database.session import get_session
from shared.uix.widgets.product_select import SELECT_PRODUCT, add_select_placeholder
from database.models.product import Product

NEW_PRODUCT = "__new__"


def populate_product_picker(combo: QComboBox, product_type: str) -> None:
    """
    Fill combo with every active product of this type, from the REAL
    Product table - not a hardcoded list. "+ New ..." is always first, so
    the counter can never dead-end on a genuinely new item.
    """
    combo.blockSignals(True)
    combo.clear()
    label = "Fertilizer" if product_type == "fertilizer" else "Crop"
    combo.addItem(f"+ New {label}...", NEW_PRODUCT)
    try:
        with get_session() as s:
            rows = (
                s.query(Product)
                .filter(Product.product_type == product_type)
                .filter(Product.is_active == True)  # noqa: E712
                .order_by(Product.name)
                .all()
            )
            for p in rows:
                combo.addItem(f"{p.name}  ({p.sku})", int(p.id))
    except Exception as e:
        QMessageBox.warning(combo, "Products", f"Could not load products:\n{e}")
    add_select_placeholder(combo)       # never start on "+ New ..." or the first product
    combo.blockSignals(False)


def resolve_selected_product(combo: QComboBox, product_type: str,
                             parent=None) -> Optional[Dict[str, Any]]:
    """
    Returns {"id", "sku", "name"} for whatever is currently selected.

    Existing product -> its REAL id/sku/name, read straight from the row.
    "+ New ..." -> prompts for a name, creates the product ONCE (sku
    generated a single time here, never again), and returns the new row.
    Returns None if the operator cancels the create prompt.
    """
    data = combo.currentData()
    if data is None:
        QMessageBox.warning(parent, "Product", f"{SELECT_PRODUCT} first.")
        return None
    if data != NEW_PRODUCT:
        with get_session() as s:
            p = s.get(Product, int(data))
            if p is None:
                QMessageBox.warning(parent, "Product", "That product no longer exists.")
                return None
            return {"id": int(p.id), "sku": p.sku, "name": p.name}

    label = "Fertilizer" if product_type == "fertilizer" else "Crop"
    name, ok = QInputDialog.getText(parent, f"New {label}", f"{label} name:")
    name = (name or "").strip()
    if not ok or not name:
        return None

    with get_session() as s:
        # dedupe by exact name within the same type, in case of a retry/typo
        existing = (s.query(Product)
                   .filter(Product.product_type == product_type)
                   .filter(Product.name == name)
                   .first())
        if existing:
            return {"id": int(existing.id), "sku": existing.sku, "name": existing.name}

        sku = _make_unique_sku(s, product_type, name)
        unit_label = "bag" if product_type == "fertilizer" else "kg"
        p = Product(
            name=name, sku=sku, product_type=product_type,
            unit_label=unit_label, is_active=True,
            default_cost_rate=0, default_sale_rate=0,
        )
        s.add(p)
        s.flush()
        return {"id": int(p.id), "sku": p.sku, "name": p.name}


def _skuify(text: str) -> str:
    t = "".join(ch.upper() if ch.isalnum() else "-" for ch in (text or "").strip())
    while "--" in t:
        t = t.replace("--", "-")
    return t.strip("-") or "ITEM"


def _make_unique_sku(session, product_type: str, name: str) -> str:
    """Generated ONCE, inside the same transaction as the product row it
    belongs to - then stored as that product's permanent identity."""
    prefix = "FERT" if product_type == "fertilizer" else "CROP"
    base = f"{prefix}-{_skuify(name)}"
    sku = base
    n = 1
    while session.query(Product).filter(Product.sku == sku).first() is not None:
        n += 1
        sku = f"{base}-{n}"
    return sku