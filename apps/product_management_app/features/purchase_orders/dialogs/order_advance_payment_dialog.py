# apps/product_management_app/features/purchase_orders/dialogs/order_advance_payment_dialog.py
# -*- coding: utf-8 -*-
"""
Shown right after a Purchase Order or Sales Order is saved as a draft,
asking whether anything was already paid at order time (a supplier
deposit, an advance from a company, etc). Deliberately separate from
PurchaseOrderPaymentDialog / SalesOrderPaymentDialog - those assume a
confirmed, fully-priced order (their amount cap is read straight off
final_total_amount), which doesn't exist yet for a brand-new draft.
No cap is applied here on purpose: there's no final price yet to
validate against, so whatever was actually paid is recorded as-is and
reconciled automatically once the order is confirmed and get_balance()
has a real total to subtract it from.

Shared between both order types - same dialog, same OrderPaymentService
call, just a different order_type/order_id passed in.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QDoubleSpinBox, QMessageBox
)
from PySide2.QtCore import Qt


class OrderAdvancePaymentDialog(QDialog):
    """
    exec_() as usual. Returns QDialog.Accepted only once a payment has
    actually been recorded (or the operator explicitly said no - that
    path also closes as Accepted, just with nothing recorded, so the
    caller's flow isn't held up either way). Cancel means "let me
    reconsider" and re-shows the yes/no prompt on next attempt - callers
    should treat Rejected as "ask again is fine, nothing happened yet".
    """
    def __init__(self, order_type: str, order_id: int, order_label: str, parent=None):
        super().__init__(parent)
        self.order_type = order_type
        self.order_id = int(order_id)
        self.recorded = False

        self.setWindowTitle("Advance Payment")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint
        )
        self.resize(480, 260)

        layout = QVBoxLayout(self)

        title = QLabel(f"Record advance payment for {order_label}")
        title.setStyleSheet("font-size: 14px; font-weight: 800;")
        title.setWordWrap(True)
        layout.addWidget(title)

        hint = QLabel(
            "No final price exists yet for a draft order, so this isn't "
            "capped against a balance - enter exactly what was actually "
            "handed over. It counts toward the balance automatically once "
            "this order is confirmed and priced."
        )
        hint.setStyleSheet("color: #666;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QHBoxLayout()
        form.addWidget(QLabel("Amount:"))
        self.amount_in = QDoubleSpinBox()
        self.amount_in.setDecimals(2)
        self.amount_in.setMaximum(1_000_000_000.0)
        form.addWidget(self.amount_in, 1)

        form.addWidget(QLabel("Method:"))
        self.method_cb = QComboBox()
        self.method_cb.addItems(["Cash", "Transfer", "Cheque", "Other"])
        form.addWidget(self.method_cb)
        layout.addLayout(form)

        layout.addWidget(QLabel("Note:"))
        self.notes_in = QLineEdit()
        self.notes_in.setPlaceholderText("Optional")
        layout.addWidget(self.notes_in)

        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_record = QPushButton("Record Advance")
        self.btn_record.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_record)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_record.clicked.connect(self._record)

    def _record(self):
        amount = float(self.amount_in.value())
        if amount <= 0:
            QMessageBox.warning(self, "Record Advance", "Enter an amount greater than zero.")
            return

        from apps.product_management_app.app_state import state
        from database.services.order_payment_service import OrderPaymentService
        try:
            OrderPaymentService.record_payment(
                self.order_type, self.order_id, amount,
                created_by_user_id=state.require_user_id(),
                method=self.method_cb.currentText(),
                notes=self.notes_in.text().strip(),
                is_advance=True,
            )
        except Exception as e:
            QMessageBox.critical(self, "Record Failed", str(e))
            return

        self.recorded = True
        self.accept()