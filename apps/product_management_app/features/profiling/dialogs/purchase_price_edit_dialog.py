# apps/product_management_app/features/profiling/dialogs/purchase_price_edit_dialog.py
# -*- coding: utf-8 -*-
"""
Edit the price of a purchase already recorded - one price per product.

Shows every product line of the purchase with the price it carries now. The user types a new price on the lines that
changed, sees the new line amounts and the change to the total, and confirms. The change is saved as an audited
adjustment (the original purchase is never overwritten) - see PurchaseAdjustmentService.edit_prices.
"""
from __future__ import annotations

from typing import Dict, List

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from database.services.purchase_adjustment_service import PurchaseAdjustmentService

COLS = ["Product", "Quantity", "Price now", "New price", "New amount", "Change"]


def _money(v: float) -> str:
    return f"{float(v or 0):,.2f}"


class PurchasePriceEditDialog(QDialog):
    def __init__(self, parent, *, purchase_id: int, ref: str, user_id: int):
        super().__init__(parent)
        self.purchase_id, self.user_id = int(purchase_id), int(user_id)
        self.saved = False
        self.lines: List[dict] = PurchaseAdjustmentService.line_prices(self.purchase_id)
        self.setWindowTitle(f"Edit prices - {ref}")
        self.setMinimumWidth(720)
        root = QVBoxLayout(self)
        intro = QLabel(f"<b>{ref}</b> - type the new price on the product(s) that changed, then confirm. "
                       "The original purchase stays on record; the change is added to it and logged.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.table = QTableWidget(len(self.lines), len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.edits: List[QLineEdit] = []
        for i, l in enumerate(self.lines):
            qty = (f"{l['bags']} bags" + (f" / {l['kg']:,.0f} kg" if l["kg"] else "")) if l["unit"] == "bag" \
                else f"{l['kg']:,.0f} kg"
            for c, v in enumerate((l["product"], qty, f"{_money(l['rate'])} per {l['unit']}")):
                self.table.setItem(i, c, QTableWidgetItem(v))
            ed = QLineEdit(f"{l['rate']:.2f}")
            ed.setAlignment(Qt.AlignRight)
            ed.setToolTip(f"Price per {l['unit']}")
            ed.textChanged.connect(self._recount)
            self.table.setCellWidget(i, 3, ed)
            self.edits.append(ed)
            for c in (4, 5):
                it = QTableWidgetItem("")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        root.addWidget(self.table, 1)

        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason (optional) - e.g. supplier asked for more")
        root.addWidget(self.reason)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("font-weight:600;")
        root.addWidget(self.lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Confirm new prices")
        self.btn_save.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        root.addLayout(row)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)
        self._recount()

    def new_rates(self) -> Dict[int, float]:
        """{item_id: price} for the lines whose price was changed (raises ValueError on a non-number)."""
        out = {}
        for l, ed in zip(self.lines, self.edits):
            raw = ed.text().replace(",", "").strip()
            price = float(raw) if raw else 0.0
            if abs(price - l["rate"]) >= 0.005:
                out[l["item_id"]] = price
        return out

    def _recount(self, *_a) -> None:
        total, bad = 0.0, ""
        for i, (l, ed) in enumerate(zip(self.lines, self.edits)):
            try:
                price = float(ed.text().replace(",", "").strip() or 0)
            except ValueError:
                bad = f"{l['product']}: type the price as a number."
                price = l["rate"]
            if price <= 0 and not bad:
                bad = f"{l['product']}: the price must be more than zero."
            delta = round((price - l["rate"]) * l["qty"], 2)
            total += delta
            self.table.item(i, 4).setText(_money(l["amount"] + delta))
            self.table.item(i, 5).setText(f"{delta:+,.2f}" if abs(delta) >= 0.005 else "-")
        if bad:
            self.lbl.setText(bad)
            self.lbl.setStyleSheet("font-weight:600; color:#a23b34;")
        elif abs(total) < 0.005:
            self.lbl.setText("No price changed yet.")
            self.lbl.setStyleSheet("font-weight:600;")
        else:
            self.lbl.setText(f"The purchase total changes by {total:+,.2f}")
            self.lbl.setStyleSheet("font-weight:600;")
        self.btn_save.setEnabled(not bad and abs(total) >= 0.005)

    def _save(self) -> None:
        try:
            PurchaseAdjustmentService.edit_prices(self.purchase_id, self.new_rates(), self.user_id, self.reason.text())
        except Exception as e:
            QMessageBox.warning(self, "Edit prices", str(e))
            return
        self.saved = True
        self.accept()
