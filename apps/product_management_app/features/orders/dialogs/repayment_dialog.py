# apps/product_management_app/features/orders/dialogs/repayment_dialog.py
"""The partner paid some of our expenses back: how much, how, and a note. The service spreads it oldest first."""
from __future__ import annotations

from PySide2.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)


class RepaymentDialog(QDialog):
    def __init__(self, owed: float, parent=None, who: str = "They"):
        super().__init__(parent)
        self.values = None
        self.setWindowTitle("Record repayment")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Still owed back to us: ₦{owed:,.2f}\n{who} paid some of it back - it is taken off the "
                             "oldest expenses first."))
        form = QFormLayout()
        self.amount = QLineEdit(f"{owed:.2f}" if owed else "")
        self.method = QComboBox()
        self.method.addItems(["Cash", "Transfer", "POS", "Cheque"])
        self.note = QLineEdit()
        form.addRow("Amount (₦)", self.amount)
        form.addRow("How", self.method)
        form.addRow("Note", self.note)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _save(self):
        try:
            amount = float(self.amount.text().replace(",", ""))
        except ValueError:
            QMessageBox.warning(self, "Repayment", "The amount must be a number.")
            return
        if amount <= 0:
            QMessageBox.warning(self, "Repayment", "The amount must be more than zero.")
            return
        self.values = {"amount": amount, "method": self.method.currentText().lower(), "note": self.note.text().strip()}
        self.accept()
