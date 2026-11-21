# apps/product_management_app/features/orders/dialogs/order_pay_dialog.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.order_sheet_service import OrderSheetService
from shared.uix.widgets.cards import money


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


class OrderPayDialog(QDialog):
    """Record a direct payment (cash / transfer / POS / cheque) on a single Sale or Purchase Order."""

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


class OrderBatchPayDialog(QDialog):
    """
    Customer-Profile-style Payment Router for Orders:
      - Shows all unpaid / open Sale & Purchase Orders for this partner.
      - Lets user choose:
          ( ) Pay selected order
          ( ) Pay a lump sum across oldest orders (FIFO, only when user explicitly selects this)
      - Records payment via OrderSheetService.pay().
    """

    def __init__(self, orders: List[Dict[str, Any]], partner_name: str, preselect_order_id: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.partner_name = partner_name
        self.orders = [
            o for o in orders
            if o.get("status") != "cancelled" and (o.get("balance") is None or float(o.get("balance") or 0) > 0.005)
        ]
        self.preselect_order_id = preselect_order_id
        self.setWindowTitle(f"Make Order Payment • {self.partner_name}")
        self.resize(760, 480)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        tot_bal = sum(float(o.get("balance") or 0) for o in self.orders if o.get("balance") is not None)
        hdr = QLabel(f"<b>Make Payment • {self.partner_name}</b>")
        hdr.setStyleSheet("font-size: 16px; color: #0f172a;")
        lay.addWidget(hdr)

        self.lbl_outstanding = QLabel(f"<b>Total Confirmed Outstanding: ₦{tot_bal:,.2f}</b>")
        self.lbl_outstanding.setStyleSheet("color: #dc2626; font-size: 14px;")
        lay.addWidget(self.lbl_outstanding)

        # Mode radio buttons (matching SalePaymentDialog / PurchasePaymentDialog)
        mode_row = QHBoxLayout()
        self.grp = QButtonGroup(self)
        self.rad_single = QRadioButton("Pay selected order")
        self.rad_fifo = QRadioButton("Pay a lump sum across oldest confirmed orders")
        self.rad_single.setChecked(True)
        self.grp.addButton(self.rad_single)
        self.grp.addButton(self.rad_fifo)
        mode_row.addWidget(self.rad_single)
        mode_row.addWidget(self.rad_fifo)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        self.rad_single.toggled.connect(self._sync_default_amount)

        # Open orders table
        cols = ["Order #", "Date", "Type", "Product", "Total", "Paid", "Balance", "Status"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_default_amount)
        lay.addWidget(self.table, 1)

        # Payment inputs
        form = QHBoxLayout()
        form.addWidget(QLabel("Amount (₦):"))
        self.txt_amount = QLineEdit()
        self.txt_amount.setMaximumWidth(160)
        form.addWidget(self.txt_amount)

        form.addWidget(QLabel("Method:"))
        self.cbo_method = QComboBox()
        self.cbo_method.addItems(["Transfer", "Cash", "POS", "Cheque"])
        form.addWidget(self.cbo_method)

        form.addWidget(QLabel("Note:"))
        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText("Optional payment note / reference...")
        form.addWidget(self.txt_note, 1)
        lay.addLayout(form)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("Close")
        btn_cancel.clicked.connect(self.reject)
        self.btn_pay = QPushButton("Record Payment")
        self.btn_pay.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 22px; border-radius: 4px;"
        )
        self.btn_pay.clicked.connect(self._record)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_pay)
        lay.addLayout(btns)

    def _populate(self):
        self.table.setRowCount(len(self.orders))
        sel_row = 0
        for i, o in enumerate(self.orders):
            if self.preselect_order_id and int(o["id"]) == int(self.preselect_order_id):
                sel_row = i
            dt_s = o["created_at"].strftime("%Y-%m-%d") if hasattr(o.get("created_at"), "strftime") else str(o.get("created_at", ""))[:10]
            tot_s = money(o["final_total_amount"]) if o.get("final_total_amount") is not None else f"{money(o.get('estimated_total', 0))} (est.)"
            bal = o.get("balance")
            bal_s = money(bal) if bal is not None else "Awaiting Final"
            vals = [
                o["order_no"], dt_s,
                "Sale Order" if o["kind"] == "sales" else "Purchase Order",
                o.get("product_name", "-"),
                tot_s, money(o.get("paid_amount", 0)), bal_s,
                o.get("status", "").replace("_", " ").title(),
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (4, 5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        if self.orders:
            self.table.selectRow(sel_row)
        self._sync_default_amount()

    def _sync_default_amount(self):
        if self.rad_fifo.isChecked():
            tot = sum(float(o.get("balance") or 0) for o in self.orders if o.get("status") == "confirmed" and o.get("balance") is not None)
            self.txt_amount.setText(f"{tot:,.2f}" if tot > 0 else "")
        else:
            r = self.table.currentRow()
            if 0 <= r < len(self.orders):
                bal = self.orders[r].get("balance")
                self.txt_amount.setText(f"{float(bal):,.2f}" if bal and bal > 0 else "")

    def _record(self):
        amt = _f(self.txt_amount.text())
        if amt <= 0:
            QMessageBox.warning(self, "Validation", "Enter a payment amount greater than zero.")
            return
        method = self.cbo_method.currentText().lower()
        note = self.txt_note.text().strip()

        try:
            if self.rad_single.isChecked():
                r = self.table.currentRow()
                if not (0 <= r < len(self.orders)):
                    QMessageBox.warning(self, "Select Order", "Select an order row first.")
                    return
                o = self.orders[r]
                bal = o.get("balance")
                if bal is not None and amt > float(bal) + 0.01:
                    QMessageBox.warning(self, "Overpayment", f"Amount exceeds order balance ({money(bal)}).")
                    return
                OrderSheetService.pay(o["kind"], int(o["id"]), amt, method=method, note=note)
            else:
                # Manual FIFO across oldest confirmed orders
                confirmed = sorted(
                    [o for o in self.orders if o.get("status") == "confirmed" and float(o.get("balance") or 0) > 0.005],
                    key=lambda x: str(x.get("created_at") or ""),
                )
                if not confirmed:
                    QMessageBox.warning(self, "No Confirmed Unpaid Orders", "Only confirmed orders can receive a lump-sum payment.")
                    return
                tot_bal = sum(float(o["balance"]) for o in confirmed)
                if amt > tot_bal + 0.01:
                    QMessageBox.warning(self, "Overpayment", f"Lump sum ({money(amt)}) exceeds total confirmed balance ({money(tot_bal)}).")
                    return
                rem = amt
                for o in confirmed:
                    if rem <= 0.005:
                        break
                    take = min(rem, float(o["balance"]))
                    OrderSheetService.pay(o["kind"], int(o["id"]), take, method=method, note=note or "Lump sum payment")
                    rem = round(rem - take, 2)
        except Exception as e:
            QMessageBox.critical(self, "Payment Failed", str(e))
            return

        QMessageBox.information(self, "Payment Recorded", f"Recorded payment of {money(amt)} successfully.")
        self.accept()
