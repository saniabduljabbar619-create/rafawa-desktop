# apps/product_management_app/features/purchase_orders/dialogs/purchase_order_payment_dialog.py
# -*- coding: utf-8 -*-
"""
Payment recording + history for one confirmed Purchase Order. Entirely
built on OrderPaymentService - never touches the shared Payment table
or PartyBalanceService, matching the Purchase Order subsystem's own
isolation.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QDoubleSpinBox,
    QMessageBox, QHeaderView, QInputDialog
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class PurchaseOrderPaymentDialog(QDialog):
    def __init__(self, purchase_order_id: int, parent=None):
        super().__init__(parent)
        self.purchase_order_id = int(purchase_order_id)

        self.setWindowTitle("Purchase Order Payment")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(680, 560)

        layout = QVBoxLayout(self)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(self.header_label)

        self.balance_label = QLabel("")
        self.balance_label.setStyleSheet("color: #b00; font-weight: 700; font-size: 13px;")
        layout.addWidget(self.balance_label)

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

        self.btn_record = QPushButton("Record Payment")
        self.btn_record.setObjectName("PrimaryButton")
        layout.addWidget(self.btn_record)

        layout.addWidget(QLabel("Payment History:"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Amount", "Method", "Status", "By"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.btn_void = QPushButton("Void Selected Payment")
        layout.addWidget(self.btn_void)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.btn_close.clicked.connect(self.accept)
        self.btn_record.clicked.connect(self._record)
        self.btn_void.clicked.connect(self._void_selected)

        self._payments = []
        self._load()

    def _load(self):
        from database.services.purchase_order_service import PurchaseOrderService
        from database.services.order_payment_service import OrderPaymentService
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService

        try:
            detail = PurchaseOrderService.get_order_detail(self.purchase_order_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.reject()
            return

        supplier = PurchaseOrderSupplierService.get(detail["supplier_id"])
        supplier_name = supplier["name"] if supplier else "Unknown supplier"
        self.header_label.setText(f"{detail['order_no']} \u2014 {supplier_name}")

        bal = OrderPaymentService.get_balance("purchase_order", self.purchase_order_id)
        self.balance_label.setText(
            f"Balance: {_money(bal['balance'])} of {_money(bal['total'])} total "
            f"({_money(bal['paid'])} paid so far)"
        )
        self.amount_in.setMaximum(max(float(bal["balance"] or 0), 0.01))

        self._payments = OrderPaymentService.list_payments("purchase_order", self.purchase_order_id)
        self.table.setRowCount(len(self._payments))
        for r, p in enumerate(self._payments):
            when = p["created_at"].strftime("%Y-%m-%d %H:%M") if p["created_at"] else ""
            cells = [when, _money(p["amount"]), p["method"].capitalize(),
                    p["status"].capitalize(), p["created_by"]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setData(Qt.UserRole, p["id"])
                self.table.setItem(r, c, it)

    def _record(self):
        amount = float(self.amount_in.value())
        if amount <= 0:
            QMessageBox.warning(self, "Record Payment", "Enter an amount greater than zero.")
            return

        from database.services.order_payment_service import OrderPaymentService
        bal = OrderPaymentService.get_balance("purchase_order", self.purchase_order_id)
        remaining_after = max(float(bal["balance"] or 0) - amount, 0.0)

        # Deliberately stricter than the regular, single sale/purchase
        # payment flow (which records straight through) - these are
        # large, infrequent, high-value transactions where the extra
        # half-second of confirmation is well worth it.
        confirm = QMessageBox.question(
            self, "Confirm Payment",
            f"Record {_money(amount)} against this order?\n\n"
            f"Remaining balance after: {_money(remaining_after)}",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            OrderPaymentService.record_payment(
                "purchase_order", self.purchase_order_id, amount,
                created_by_user_id=state.require_user_id(),
                method=self.method_cb.currentText(),
                notes=self.notes_in.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Record Failed", str(e))
            return

        self.amount_in.setValue(0.0)
        self.notes_in.clear()
        self._load()

    def _void_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._payments):
            QMessageBox.information(self, "Void Payment", "Select a payment first.")
            return
        p = self._payments[row]
        if p["status"] == "voided":
            QMessageBox.information(self, "Void Payment", "This payment is already voided.")
            return

        confirm = QMessageBox.question(
            self, "Void Payment",
            f"Void this {_money(p['amount'])} payment? It stays in history "
            f"but drops out of the balance right away.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        reason, ok = QInputDialog.getText(
            self, "Reason Required",
            "Why is this being voided? (required - this history matters)"
        )
        if not ok:
            return
        if not reason.strip():
            QMessageBox.warning(
                self, "Reason Required",
                "A reason is required to void a Purchase Order payment - "
                "this history is too important to leave unexplained."
            )
            return

        from database.services.order_payment_service import OrderPaymentService
        try:
            OrderPaymentService.void_payment(p["id"], voided_by_user_id=state.require_user_id(), reason=reason)
        except Exception as e:
            QMessageBox.critical(self, "Void Failed", str(e))
            return

        self._load()