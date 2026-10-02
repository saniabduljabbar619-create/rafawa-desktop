# apps/product_management_app/features/orders/dialogs/correction_dialog.py
"""Correct one line of a CONFIRMED order: goods found damaged or short, a wrong count, a price dispute."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QVBoxLayout,
)

from database.services.order_sheet_service import CORRECTION_CATEGORIES, OrderSheetService
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import money


class CorrectionDialog(QDialog):
    def __init__(self, kind: str, order: Dict[str, Any], line: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.kind, self.order, self.line = kind, order, line
        self.result: Optional[Dict[str, Any]] = None
        self.fert = line["type"] == "fertilizer"
        self.unit = "bags" if self.fert else "kg"
        self.setWindowTitle("Review / correct a line")
        self.setMinimumWidth(480)
        self.rcv = float(line["rcv_qty"] or 0)
        self.rej0 = float(line["rej_qty"] or 0)
        self.price0 = float(line["rcv_price"] or 0)

        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{order['order_no']}  -  {line['name']}</b><br>"
                      f"{'Company' if kind == 'sales' else 'We'} received {self.rcv:,.2f} {self.unit} at "
                      f"{money(self.price0)}; rejected {self.rej0:,.2f}.")
        head.setWordWrap(True)
        lay.addWidget(head)
        form = QFormLayout()
        self.category = QComboBox()
        self.category.addItems(CORRECTION_CATEGORIES)
        self.rej = QDoubleSpinBox()
        self.rej.setRange(0, max(self.rcv, 0))
        self.rej.setDecimals(2)
        self.rej.setValue(self.rej0)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000_000)
        self.price.setDecimals(2)
        self.price.setGroupSeparatorShown(True)
        self.price.setValue(self.price0)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("What happened? (required - kept in the order's history)")
        self.stock = QCheckBox()
        self.stock.setChecked(True)
        form.addRow("What kind of problem", self.category)
        form.addRow(f"Rejected / damaged ({self.unit})", self.rej)
        form.addRow("Price (₦)", self.price)
        form.addRow("Reason", self.reason)
        form.addRow("", self.stock)
        lay.addLayout(form)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("font-weight:600; color:#1c2333; padding:6px 0;")
        lay.addWidget(self.preview)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Apply correction")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.rej.valueChanged.connect(self._update)
        self.price.valueChanged.connect(self._update)
        self._update()

    def _update(self, *_a) -> None:
        delta = round(self.rej.value() - self.rej0, 2)
        sales = self.kind == "sales"
        if abs(delta) < 0.005:
            self.stock.setText("No change in the rejected quantity - no stock movement")
            self.stock.setEnabled(False)
        else:
            self.stock.setEnabled(True)
            more = delta > 0
            if sales:
                txt = (f"Put the {abs(delta):,.2f} {self.unit} back on the shelf" if more
                       else f"Take {abs(delta):,.2f} {self.unit} off the shelf again")
                txt += " (untick if the goods were destroyed / never came back)" if more else ""
            else:
                txt = (f"Remove {abs(delta):,.2f} {self.unit} from stock" if more
                       else f"Add {abs(delta):,.2f} {self.unit} back to stock")
                txt += " (untick if it never entered stock)" if more else ""
            self.stock.setText(txt)
        acc = self.rcv - self.rej.value()
        new_line = acc * self.price.value()
        old_line = (self.rcv - self.rej0) * self.price0
        diff = new_line - old_line
        total_after = float(self.order["total"]) + diff
        paid = float(self.order["paid"])
        txt = (f"Accepted becomes {acc:,.2f} {self.unit}; this line {money(new_line)} "
               f"({'+' if diff >= 0 else '-'}{money(abs(diff))}). Order total {money(total_after)}.")
        if paid - total_after > 0.005:
            txt += f"\nThis leaves the order overpaid by {money(paid - total_after)} - settle it in the Payments tab."
        self.preview.setText(txt)

    def _save(self) -> None:
        try:
            self.result = OrderSheetService.correct_line(
                self.kind, self.order["id"], self.line["id"], self.reason.text(),
                rej_qty=self.rej.value() if abs(self.rej.value() - self.rej0) >= 0.005 else None,
                price=self.price.value() if abs(self.price.value() - self.price0) >= 0.005 else None,
                category=self.category.currentText(),
                stock_move=self.stock.isChecked() and self.stock.isEnabled())
        except (ValueError, PermissionDenied) as e:
            QMessageBox.warning(self, "Correction", str(e))
            return
        self.accept()
