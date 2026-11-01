# apps/product_management_app/features/profiling/dialogs/purchase_price_edit_dialog.py
# -*- coding: utf-8 -*-
"""
Edit the price of a purchase or sale already recorded - one price per product.

Shows every product line of the purchase/sale with the price it carries now. The user types a new price on the lines that
changed, sees the new line amounts and the change to the total, and confirms. The change is saved as an audited
adjustment (the original document is never overwritten) - see PurchaseAdjustmentService.edit_prices /
SaleAdjustmentService.edit_prices.
"""
from __future__ import annotations

from typing import Dict, List

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from database.services.purchase_adjustment_service import PurchaseAdjustmentService
from database.services.sale_adjustment_service import SaleAdjustmentService

COLS = ["Product", "Quantity", "Price now", "New price", "New amount", "Change"]


def _money(v: float) -> str:
    return f"{float(v or 0):,.2f}"


class PurchasePriceEditDialog(QDialog):
    def __init__(self, parent, *, purchase_id: int = 0, sale_id: int = 0, ref: str = "", user_id: int = 0, kind: str = ""):
        super().__init__(parent)
        resolved_kind = (kind or ("sale" if int(sale_id or 0) > 0 else "purchase")).strip().lower()
        self.kind = "sale" if resolved_kind == "sale" else "purchase"
        self.doc_id = int(sale_id if self.kind == "sale" and int(sale_id or 0) > 0 else purchase_id)
        self.purchase_id = self.doc_id
        self.sale_id = self.doc_id if self.kind == "sale" else 0
        self.user_id = int(user_id or 0)
        if not self.user_id:
            from shared.auth.session import get_session as get_login_session
            self.user_id = int(getattr(get_login_session(), "user_id", 0) or 1)
        if not ref:
            ref = f"{'SAL' if self.kind == 'sale' else 'PUR'}-{self.doc_id}"
        self.ref = ref
        self.saved = False
        svc = SaleAdjustmentService if self.kind == "sale" else PurchaseAdjustmentService
        self.lines: List[dict] = svc.line_prices(self.doc_id)
        self.setWindowTitle(f"Edit prices - {ref}")
        self.setMinimumWidth(720)
        root = QVBoxLayout(self)
        intro = QLabel(f"<b>{ref}</b> - type the new price on the product(s) that changed, then confirm. "
                       f"The original {self.kind} stays on record; the change is added to it and logged.")
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
        self.reason.setPlaceholderText(
            "Reason (optional) - e.g. customer price agreed"
            if self.kind == "sale"
            else "Reason (optional) - e.g. supplier asked for more"
        )
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
            self.lbl.setText(f"The {self.kind} total changes by {total:+,.2f}")
            self.lbl.setStyleSheet("font-weight:600;")
        self.btn_save.setEnabled(not bad and abs(total) >= 0.005)

    def _save(self) -> None:
        try:
            if self.kind == "sale":
                SaleAdjustmentService.edit_prices(self.doc_id, self.new_rates(), self.user_id, self.reason.text())
            else:
                PurchaseAdjustmentService.edit_prices(self.doc_id, self.new_rates(), self.user_id, self.reason.text())
        except Exception as e:
            QMessageBox.warning(self, "Edit prices", str(e))
            return
        self.saved = True
        self.accept()


class SalePriceEditDialog(PurchasePriceEditDialog):
    def __init__(self, parent, *, sale_id: int, ref: str = "", user_id: int = 0):
        super().__init__(parent, sale_id=sale_id, ref=ref, user_id=user_id, kind="sale")


def open_price_editor(parent, kind: str, doc_id: int, ref: str = "", user_id: int = 0) -> bool:
    """Open the price edit dialog for a purchase or sale and return True if saved."""
    kind = (kind or "purchase").strip().lower()
    doc_id = int(doc_id or 0)
    if doc_id <= 0 or kind not in ("purchase", "sale"):
        return False
    dlg = PurchasePriceEditDialog(
        parent,
        purchase_id=(doc_id if kind == "purchase" else 0),
        sale_id=(doc_id if kind == "sale" else 0),
        ref=ref or f"{'SAL' if kind == 'sale' else 'PUR'}-{doc_id}",
        user_id=user_id,
        kind=kind,
    )
    dlg.exec_()
    return bool(dlg.saved)
