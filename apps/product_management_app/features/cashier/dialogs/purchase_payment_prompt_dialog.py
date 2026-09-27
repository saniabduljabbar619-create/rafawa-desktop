# apps/product_management_app/features/cashier/dialogs/purchase_payment_prompt_dialog.py

from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QDoubleSpinBox,
    QMessageBox,
)


class PurchasePaymentPromptDialog(QDialog):

    def __init__(self, total_amount: float, parent=None):
        super().__init__(parent)

        self.total_amount = float(total_amount or 0.0)
        self.selected_amount = 0.0
        self.mode = "none"

        self.setWindowTitle("Pay Now?")
        self.setModal(True)
        self.resize(400, 220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        # Header
        layout.addWidget(QLabel(f"Total Amount: ₦{self.total_amount:,.2f}"))

        # Options
        self.full_btn = QRadioButton("Full Payment")
        self.partial_btn = QRadioButton("Partial Payment")
        self.none_btn = QRadioButton("Leave as Unpaid")

        self.none_btn.setChecked(True)

        layout.addWidget(self.full_btn)
        layout.addWidget(self.partial_btn)
        layout.addWidget(self.none_btn)

        # Partial amount input
        self.amount_in = QDoubleSpinBox()
        self.amount_in.setDecimals(2)
        self.amount_in.setMinimum(0.01)
        self.amount_in.setMaximum(self.total_amount)
        self.amount_in.setEnabled(False)
        layout.addWidget(self.amount_in)

        self.partial_btn.toggled.connect(
            lambda checked: self.amount_in.setEnabled(checked)
        )

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("Submit")

        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)

        layout.addLayout(btns)

        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._submit)

    # ------------------------------------------------
    # Submission Logic (Hardened)
    # ------------------------------------------------
    def _submit(self):

        # Full payment
        if self.full_btn.isChecked():
            self.mode = "full"
            self.selected_amount = self.total_amount
            self.accept()
            return

        # Partial payment
        if self.partial_btn.isChecked():
            amt = float(self.amount_in.value())

            if amt <= 0:
                QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than zero.")
                return

            if amt > self.total_amount:
                QMessageBox.warning(self, "Invalid Amount", "Amount cannot exceed total amount.")
                return

            self.mode = "partial"
            self.selected_amount = amt
            self.accept()
            return

        # Leave unpaid
        self.mode = "none"
        self.selected_amount = 0.0
        self.accept()
