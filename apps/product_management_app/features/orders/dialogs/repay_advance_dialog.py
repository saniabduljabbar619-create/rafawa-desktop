# apps/product_management_app/features/orders/dialogs/repay_advance_dialog.py
from __future__ import annotations

from typing import Any, Dict, List

from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from database.services.order_money_service import OrderMoneyService
from shared.uix.widgets.cards import money


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


class RepayAdvanceDialog(QDialog):
    """
    Record a direct cash/transfer repayment or refund of an Advance
    (e.g., supplier pays back Advance OUT we gave them, or we refund Advance IN to a company)
    instead of applying it to an order.
    """

    def __init__(self, advances: List[Dict[str, Any]], partner_name: str, selected_id: int = 0, parent=None):
        super().__init__(parent)
        self.advances = [a for a in advances if a.get("status") == "completed"]
        self.partner_name = partner_name
        self.selected_id = int(selected_id or 0)

        self.setWindowTitle(f"Advance Repayment / Refund • {self.partner_name}")
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        info = QFrame()
        info.setObjectName("rep_info")
        info.setStyleSheet("""
            QFrame#rep_info { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px; }
            QFrame#rep_info QLabel { border: none; background: transparent; }
        """)
        il = QVBoxLayout(info)
        il.setContentsMargins(12, 10, 12, 10)
        il.addWidget(QLabel(f"<b>Advance Payment / Repayment for {self.partner_name}</b>"))
        sub = QLabel(
            "Use this when an advance is paid back directly in cash/transfer "
            "(for example, supplier returning an Advance OUT, or refunding an Advance IN) "
            "instead of using it on a Sale/Purchase Order."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #1e40af; font-size: 12px;")
        il.addWidget(sub)
        lay.addWidget(info)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.cbo_adv = QComboBox()
        self.cbo_adv.setMinimumHeight(30)
        sel_idx = 0
        for i, a in enumerate(self.advances):
            dir_lbl = "Advance IN" if a["direction"] == "in" else "Advance OUT"
            dt_s = a["date"].strftime("%d/%m/%Y") if hasattr(a.get("date"), "strftime") else str(a.get("date", ""))
            self.cbo_adv.addItem(f"#{a['id']} • {dir_lbl} • {money(a['amount'])} ({dt_s})", a)
            if int(a["id"]) == self.selected_id:
                sel_idx = i
        self.cbo_adv.setCurrentIndex(sel_idx)
        self.cbo_adv.currentIndexChanged.connect(self._on_adv_changed)
        form.addRow("<b>Select Advance:</b>", self.cbo_adv)

        self.txt_amount = QLineEdit()
        form.addRow("<b>Repayment Amount (₦):</b>", self.txt_amount)

        self.cbo_method = QComboBox()
        self.cbo_method.addItems(["Transfer", "Cash", "POS", "Cheque"])
        form.addRow("Method:", self.cbo_method)

        self.dt_date = QDateEdit()
        self.dt_date.setCalendarPopup(True)
        self.dt_date.setDate(QDate.currentDate())
        form.addRow("Date:", self.dt_date)

        self.txt_note = QLineEdit()
        self.txt_note.setPlaceholderText("Reason / bank reference / receipt #...")
        form.addRow("Note:", self.txt_note)

        lay.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Confirm Repayment / Refund")
        btn_save.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; padding: 8px 18px; border-radius: 4px;"
        )
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

        self._on_adv_changed()

    def _on_adv_changed(self):
        a = self.cbo_adv.currentData()
        if a:
            self.txt_amount.setText(f"{float(a['amount']):,.2f}")

    def _save(self):
        a = self.cbo_adv.currentData()
        if not a:
            QMessageBox.warning(self, "Validation", "No available advance selected.")
            return
        amt = _f(self.txt_amount.text())
        if amt <= 0:
            QMessageBox.warning(self, "Validation", "Please enter an amount greater than zero.")
            return
        if amt > float(a["amount"]) + 0.01:
            QMessageBox.warning(
                self, "Amount Too High",
                f"Amount ({money(amt)}) exceeds available advance ({money(a['amount'])})."
            )
            return

        method = self.cbo_method.currentText().lower()
        note = self.txt_note.text().strip()
        entry_date = self.dt_date.date().toPython()
        try:
            OrderMoneyService.repay_advance(
                money_id=int(a["id"]),
                amount=amt,
                method=method,
                note=note,
                entry_date=entry_date,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.accept()
