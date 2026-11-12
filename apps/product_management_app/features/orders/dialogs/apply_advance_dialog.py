# apps/product_management_app/features/orders/dialogs/apply_advance_dialog.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QFrame,
)

from database.services.order_money_service import OrderMoneyService
from database.services.order_profile_service import OrderProfileService


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


class ApplyAdvanceDialog(QDialog):
    """
    Dialog to apply an available recorded advance from the profile to a finalized order.
    """

    def __init__(self, order_data: Dict[str, Any], partner_name: str, parent=None):
        super().__init__(parent)
        self.order = order_data
        self.partner_name = partner_name
        self.kind = self.order.get("kind", "sales")
        self.partner_id = int(self.order.get("partner_id") or 0)
        self.balance = _f(self.order.get("balance") or 0.0)

        direction = "in" if self.kind == "sales" else "out"
        self.available_advances = OrderMoneyService.get_available_advances(
            kind=self.kind,
            partner_id=self.partner_id,
            direction=direction,
        )

        self.setWindowTitle(f"Use Advance • {self.order.get('order_no', '')}")
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(12)

        # Header Info Box
        info_box = QFrame()
        info_box.setObjectName("info_box")
        info_box.setStyleSheet("""
            QFrame#info_box {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 10px;
            }
            QFrame#info_box QLabel {
                border: none;
                background: transparent;
                padding: 0px;
            }
        """)
        ib_lay = QVBoxLayout(info_box)
        ib_lay.setSpacing(4)

        order_no = self.order.get("order_no", "")
        final_amt = _f(self.order.get("final_total_amount") or self.order.get("estimated_total") or 0.0)
        paid_amt = _f(self.order.get("paid_amount") or 0.0)

        t_lbl = QLabel(f"<b>Apply Advance to Order {order_no}</b>")
        t_lbl.setStyleSheet("font-size: 15px; color: #0f172a;")
        ib_lay.addWidget(t_lbl)

        d_lbl = QLabel(f"Partner: <b>{self.partner_name}</b>  |  Total: <b>₦{final_amt:,.2f}</b>  |  Paid: ₦{paid_amt:,.2f}")
        d_lbl.setStyleSheet("color: #475569; font-size: 13px;")
        ib_lay.addWidget(d_lbl)

        bal_lbl = QLabel(f"<b>Outstanding Balance to Settle: ₦{self.balance:,.2f}</b>")
        bal_lbl.setStyleSheet("color: #b91c1c; font-size: 13px; font-weight: bold;")
        ib_lay.addWidget(bal_lbl)
        main_lay.addWidget(info_box)

        if not self.available_advances:
            no_lbl = QLabel("⚠️ <i>No unapplied advances found in this partner's profile.</i>")
            no_lbl.setStyleSheet("color: #b91c1c; font-size: 13px;")
            main_lay.addWidget(no_lbl)
            btn_close = QPushButton("Close")
            btn_close.clicked.connect(self.reject)
            main_lay.addWidget(btn_close)
            return

        # Form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.cbo_adv = QComboBox()
        self.cbo_adv.setMinimumHeight(30)
        for adv in self.available_advances:
            d_str = adv["entry_date"].strftime("%d/%m/%Y") if adv.get("entry_date") else "-"
            note_str = f" ({adv['note']})" if adv.get("note") else ""
            self.cbo_adv.addItem(
                f"Advance #{adv['id']} • ₦{adv['amount']:,.2f} ({d_str}){note_str}",
                adv["id"]
            )
        self.cbo_adv.currentIndexChanged.connect(self._on_adv_changed)
        form.addRow("Select Advance:", self.cbo_adv)

        self.txt_amount = QLineEdit()
        form.addRow("Amount to Apply (₦):", self.txt_amount)
        main_lay.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("Apply Advance to Order")
        self.btn_apply.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 18px; border-radius: 4px;"
        )
        self.btn_apply.clicked.connect(self._apply)

        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_apply)
        main_lay.addLayout(btn_box)

        self._on_adv_changed()

    def _selected_advance(self) -> Optional[Dict[str, Any]]:
        idx = self.cbo_adv.currentIndex()
        if 0 <= idx < len(self.available_advances):
            return self.available_advances[idx]
        return None

    def _on_adv_changed(self):
        adv = self._selected_advance()
        if not adv:
            return
        adv_amt = _f(adv["amount"])
        to_apply = min(self.balance, adv_amt) if self.balance > 0 else adv_amt
        self.txt_amount.setText(f"{to_apply:,.2f}")

    def _apply(self):
        adv = self._selected_advance()
        if not adv:
            QMessageBox.warning(self, "Validation Error", "Please select an advance.")
            return

        amt = _f(self.txt_amount.text())
        if amt <= 0:
            QMessageBox.warning(self, "Validation Error", "Amount to apply must be greater than zero.")
            self.txt_amount.setFocus()
            return

        adv_amt = _f(adv["amount"])
        if amt > adv_amt:
            QMessageBox.warning(
                self, "Validation Error",
                f"Cannot apply ₦{amt:,.2f}: selected advance only has ₦{adv_amt:,.2f}."
            )
            return

        try:
            pid = OrderProfileService.apply_advance(
                kind=self.kind,
                order_id=self.order["id"],
                money_id=adv["id"],
                amount=amt,
            )
            QMessageBox.information(
                self, "Advance Applied",
                f"Successfully applied ₦{amt:,.2f} from Advance #{adv['id']} to Order {self.order.get('order_no')}."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Apply Advance", str(e))
