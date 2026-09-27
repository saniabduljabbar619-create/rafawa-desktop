# apps/product_management_app/features/suppliers/views/supplier_new_sale_dialog.py
# -*- coding: utf-8 -*-
"""
"New Sale" from the Supplier Profile.

Mirrors "Bulk Purchase" on the opposite side of the ledger: a self-contained
dialog pre-bound to this party as the buyer, so the operator never has to
leave the profile and re-find them in the main Sales Console. It reuses the
SAME pieces the main console uses - SaleItemDialog for adding a line (already
handles fertilizer-bags vs crops-kg correctly), SaleService.record() for the
write, and SalePaymentDialog for collecting payment afterwards - so behavior
stays identical to a normal sale, just entered from a different door.

Deliberately simpler than the main console: no discount field (adjust the
rate per line instead - same effect, one less control) and no Quick Purchase
shortfall interlude (this is a direct, deliberate sale of what's already in
stock, not a walk-up counter transaction).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)

from database.services.sales_service import SaleService
from apps.product_management_app.app_state import state
from apps.product_management_app.features.cashier.dialogs.sale_item_dialog import SaleItemDialog

NAIRA = "\u20a6"


def money(v) -> str:
    try:
        return f"{NAIRA}{float(v or 0):,.2f}"
    except Exception:
        return f"{NAIRA}0.00"


@dataclass
class _SaleLine:
    product_id: int
    product_type: str
    name: str
    unit_price: float
    unit_cost_at_sale: float
    qty_bags: int
    total_weight_kg: float

    @property
    def subtotal(self) -> float:
        ptype = (self.product_type or "").strip().lower()
        if ptype == "fertilizer":
            return float(self.qty_bags) * float(self.unit_price)
        return float(self.total_weight_kg) * float(self.unit_price)


class SupplierNewSaleDialog(QDialog):

    def __init__(self, supplier_party_id: int, supplier_name: str, parent=None):
        super().__init__(parent)
        self.supplier_party_id = int(supplier_party_id)
        self.supplier_name = supplier_name
        self.lines: List[_SaleLine] = []
        self._last_sale_id = 0

        self.setWindowTitle(f"New Sale - {supplier_name}")
        self.setMinimumWidth(720)
        self.setMinimumHeight(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel(f"Selling to: {supplier_name}")
        title.setStyleSheet("font-size:15px; font-weight:800;")
        root.addWidget(title)

        # Category first - same rule as the main Sales Console: crops and
        # fertilizer are priced/tracked on different bases (kg vs bags), and
        # once the cart has a line, switching category is blocked until it's
        # cleared, so a sale can't silently mix the two ledgers.
        self.current_category = ""
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Category:"))
        self.category_cb = QComboBox()
        self.category_cb.addItems(["Select Category", "Crops", "Fertilizer"])
        self.category_cb.currentTextChanged.connect(self._on_category_changed)
        cat_row.addWidget(self.category_cb, 1)
        root.addLayout(cat_row)

        # product picker - identical source to the main Sales Console, now
        # filtered to whichever category is selected above
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Product name or SKU...")
        self.search.textChanged.connect(self._refresh_picker)
        pick_row.addWidget(self.search, 1)
        root.addLayout(pick_row)

        prod_row = QHBoxLayout()
        self.product_cb = QComboBox()
        prod_row.addWidget(self.product_cb, 1)
        self.btn_add = QPushButton("Add Item")
        self.btn_add.clicked.connect(self._add_item)
        prod_row.addWidget(self.btn_add)
        root.addLayout(prod_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Product", "Qty / Weight", "Rate", "Subtotal"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        rm_row = QHBoxLayout()
        rm_row.addStretch(1)
        self.btn_remove = QPushButton("Remove Selected")
        self.btn_remove.clicked.connect(self._remove_selected)
        rm_row.addWidget(self.btn_remove)
        root.addLayout(rm_row)

        self.lbl_total = QLabel(f"Total: {money(0)}")
        self.lbl_total.setStyleSheet("font-size:14px; font-weight:800;")
        root.addWidget(self.lbl_total)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_finalize = QPushButton("Finalize Sale")
        self.btn_finalize.setStyleSheet(
            "QPushButton { background:#27ae60; color:#fff; border:none;"
            " border-radius:8px; padding:8px 20px; font-weight:800; }")
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_finalize)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_finalize.clicked.connect(self._finalize)

        self._refresh_picker()

    # ------------------------------------------------------------------
    def _on_category_changed(self, text: str):
        cat = text.strip().lower()
        if cat not in ("crops", "fertilizer"):
            self.current_category = ""
            self.product_cb.clear()
            return

        if self.lines:
            existing = self.lines[0].product_type
            if existing != cat:
                QMessageBox.warning(
                    self, "Ledger Conflict",
                    "Cannot change category while the cart contains items.\n"
                    "Remove them first.")
                self.category_cb.blockSignals(True)
                self.category_cb.setCurrentText(existing.capitalize())
                self.category_cb.blockSignals(False)
                return

        self.current_category = cat
        self._refresh_picker()

    def _refresh_picker(self):
        self.product_cb.clear()
        if not self.current_category:
            return
        try:
            items = SaleService.list_products_for_sale(q=self.search.text(), limit=500)
        except Exception as e:
            QMessageBox.warning(self, "Products", f"Could not load products:\n{e}")
            return
        for p in items:
            ptype = str(p.get("product_type") or "").strip().lower()
            if ptype != self.current_category:
                continue
            self.product_cb.addItem(
                f"{p['name']} \u2022 {p['sku']} \u2022 stock:{p['stock']:,.2f} "
                f"\u2022 {NAIRA}{p['sale_rate']:,.2f}",
                p,
            )

    def _add_item(self):
        if not self.current_category:
            QMessageBox.information(self, "Sale", "Select a category first.")
            return
        p = self.product_cb.currentData()
        if not p:
            QMessageBox.information(self, "Sale", "Select a product first.")
            return

        ptype = str(p.get("product_type") or "").strip().lower()
        dlg = SaleItemDialog(
            self,
            product_id=int(p["id"]),
            product_name=str(p["name"]),
            product_type=ptype,
            unit_price=float(p.get("sale_rate") or 0.0),
        )
        if dlg.exec_() != dlg.Accepted:
            return

        payload = dlg.payload()
        variant = payload.get("variant", "Default")
        label = str(p["name"]) if variant.lower() == "default" else f"{p['name']} \u2022 {variant}"

        self.lines.append(_SaleLine(
            product_id=int(p["id"]),
            product_type=ptype,
            name=label,
            unit_price=float(payload["unit_price"]),
            unit_cost_at_sale=float(payload["unit_cost_at_sale"]),
            qty_bags=int(payload["qty_bags"]),
            total_weight_kg=float(payload["total_weight_kg"]),
        ))
        self._refresh_table()

    def _remove_selected(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.lines):
            return
        del self.lines[r]
        self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(len(self.lines))
        total = 0.0
        for i, ln in enumerate(self.lines):
            qty_txt = (f"{ln.qty_bags} bag(s)" if ln.product_type == "fertilizer"
                      else f"{ln.total_weight_kg:g} kg")
            cells = [ln.name, qty_txt, money(ln.unit_price), money(ln.subtotal)]
            for c, text in enumerate(cells):
                self.table.setItem(i, c, QTableWidgetItem(str(text)))
            total += ln.subtotal
        self.lbl_total.setText(f"Total: {money(total)}")

    # ------------------------------------------------------------------
    def _finalize(self):
        if not self.lines:
            QMessageBox.information(self, "Sale", "Add at least one item first.")
            return

        try:
            created_by = state.require_user_id()
            res = SaleService.record({
                "buyer_party_id": self.supplier_party_id,
                "created_by_user_id": created_by,
                "items": [{
                    "product_id": ln.product_id,
                    "product_type": ln.product_type,
                    "qty_bags": ln.qty_bags,
                    "total_weight_kg": ln.total_weight_kg,
                    "unit_price": ln.unit_price,
                } for ln in self.lines],
                "total": sum(ln.subtotal for ln in self.lines),
                "note": f"Recorded from {self.supplier_name}'s profile",
            })
        except Exception as e:
            QMessageBox.critical(self, "Sale", f"Could not record sale:\n{e}")
            return

        sale_id = int(res.get("sale_id") or res.get("id") or 0)
        self._last_sale_id = sale_id

        # collect payment now, same dialog the main console uses - keeps
        # behavior identical regardless of which door the sale came through
        if sale_id > 0:
            from database.session import get_session
            from database.models.party import Party
            from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import (
                SalePaymentDialog,
            )
            with get_session() as s:
                party = s.get(Party, self.supplier_party_id)
            dlg = SalePaymentDialog(party=party, user_id=created_by,
                                    parent=self, print_slip=True)
            dlg.exec_()

        QMessageBox.information(self, "Recorded", f"Sale recorded (#{sale_id}).")
        self.accept()