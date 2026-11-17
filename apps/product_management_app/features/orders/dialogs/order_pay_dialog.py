# apps/product_management_app/features/orders/dialogs/order_pay_dialog.py
from __future__ import annotations

from typing import Any, Dict

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from database.services.order_sheet_service import OrderSheetService
from shared.uix.widgets.cards import money


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


class OrderPayDialog(QDialog):
    """Record a direct payment (cash / transfer / POS / cheque) on a Sale or Purchase Order."""

    def __init__(self, order_data: Dict[str, Any], partner_name: str, parent=None):
        super().__init__(parent)
        self.order = order_data
        self.partner_name = partner_name
        self.kind = self.order.get("kind", "sales")
        self.is_sale = self.kind == "sales"
        self.order_id = int(self.order["id"])

        title_action = "Receive Payment" if self.is_sale else "Pay Supplier"
        self.setWindowTitle(f"{title_action} • {self.order.get('order_no', '')}")
        self.setMinimumWidth(440)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        box = QFrame()
        box.setObjectName("pay_info")
        box.setStyleSheet("""
            QFrame#pay_info { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; }
            QFrame#pay_info QLabel { border: none; background: transparent; }
        """)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(4)

        order_no = self.order.get("order_no", "")
        bal = self.order.get("balance")
        paid = self.order.get("paid_amount") or 0.0
        total = self.order.get("final_total_amount")
        est = self.order.get("estimated_total") or 0.0

        bl.addWidget(QLabel(f"<b>{order_no}</b> • {self.partner_name}"))
        if total is not None:
            bl.addWidget(QLabel(
                f"Final Total: <b>{money(total)}</b>  |  Paid: <b>{money(paid)}</b>  |  "
                f"<span style='color:#dc2626;'>Balance: <b>{money(bal or 0)}</b></span>"
            ))
        else:
            bl.addWidget(QLabel(
                f"Estimated Total: <b>{money(est)}</b>  |  Paid so far: <b>{money(paid)}</b> "
                "<i>(will reconcile when finalized)</i>"
            ))
        lay.addWidget(box)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        default_amt = bal if (bal is not None and bal > 0.005) else 0.0
        self.txt_amount = QLineEdit(f"{default_amt:,.2f}" if default_amt > 0 else "")
        self.txt_amount.setPlaceholderText("Enter amount in ₦")
        form.addRow("<b>Amount (₦):</b>", self.txt_amount)

        self.cbo_method = QComboBox()
        self.cbo_method.addItems(["Transfer", "Cash", "POS", "Cheque"])
        form.addRow("Method:", self.cbo_method)

        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText("Bank ref, receipt #, or payment notes...")
        form.addRow("Note / Ref:", self.txt_note)

        lay.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Record Payment")
        btn_save.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px;"
        )
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _save(self):
        amt = _f(self.txt_amount.text())
        if amt <= 0:
            QMessageBox.warning(self, "Validation", "Please enter an amount greater than zero.")
            return
        bal = self.order.get("balance")
        if bal is not None and amt > float(bal) + 0.01:
            QMessageBox.warning(
                self, "Overpayment",
                f"Amount ({money(amt)}) exceeds the remaining balance ({money(bal)})."
            )
            return

        method = self.cbo_method.currentText().lower()
        note = self.txt_note.text().strip()
        try:
            OrderSheetService.pay(self.kind, self.order_id, amt, method=method, note=note)
        except Exception as e:
            QMessageBox.critical(self, "Payment Error", str(e))
            return
        self.accept()
