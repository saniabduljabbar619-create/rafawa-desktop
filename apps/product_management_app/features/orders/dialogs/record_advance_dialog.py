# apps/product_management_app/features/orders/dialogs/record_advance_dialog.py
from __future__ import annotations

from datetime import date
from typing import Optional

from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from database.services.order_profile_service import OrderProfileService


class RecordAdvanceDialog(QDialog):
    """
    Dialog to record an advance on a partner's profile:
      - IN: Advance from company for Sale Orders
      - OUT: Advance paid to supplier for Purchase Orders
    Advances remain safely in the profile until explicitly applied during/after finalization.
    """

    def __init__(self, partner_name: str, company_id: Optional[int], supplier_id: Optional[int],
                 default_direction: str = "in", parent=None):
        super().__init__(parent)
        self.partner_name = partner_name
        self.company_id = company_id
        self.supplier_id = supplier_id
        self.default_direction = default_direction
        self.recorded_id: Optional[int] = None

        self.setWindowTitle("Record Advance • " + partner_name)
        self.setMinimumWidth(440)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # Header note
        hdr = QLabel(f"<b>Record Advance for {self.partner_name}</b>")
        hdr.setStyleSheet("font-size: 15px; color: #1e293b;")
        lay.addWidget(hdr)

        desc = QLabel(
            "Advances recorded here are safely held in the profile. "
            "They will <b>never</b> be used automatically unless you explicitly choose to apply them to an order."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 6px;")
        lay.addWidget(desc)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # Direction / Type
        self.cbo_dir = QComboBox()
        self.cbo_dir.addItem("IN — Company Advance (for Sale Orders)", "in")
        self.cbo_dir.addItem("OUT — Supplier Advance (for Purchase Orders)", "out")
        if self.default_direction == "out":
            self.cbo_dir.setCurrentIndex(1)
        form.addRow("Advance Direction:", self.cbo_dir)

        # Amount
        self.txt_amount = QLineEdit()
        self.txt_amount.setPlaceholderText("0.00")
        form.addRow("Amount (₦):", self.txt_amount)

        # Date
        self.dt_date = QDateEdit()
        self.dt_date.setCalendarPopup(True)
        self.dt_date.setDate(QDate.currentDate())
        form.addRow("Entry Date:", self.dt_date)

        # Payment Method
        self.cbo_method = QComboBox()
        self.cbo_method.addItems(["Transfer", "Cash", "POS", "Cheque"])
        form.addRow("Payment Method:", self.cbo_method)

        # Note / Ref
        self.txt_notes = QLineEdit()
        self.txt_notes.setPlaceholderText("e.g. Deposit for truck delivery, advance contract payment...")
        form.addRow("Notes / Reference:", self.txt_notes)

        lay.addLayout(form)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Save Advance")
        self.btn_save.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; padding: 7px 18px; border-radius: 4px;"
        )
        self.btn_save.clicked.connect(self._save)

        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_save)
        lay.addLayout(btn_box)

    def _save(self):
        try:
            amt_str = self.txt_amount.text().strip().replace(",", "")
            amt = float(amt_str)
            if amt <= 0:
                raise ValueError("Amount must be greater than zero.")
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Please enter a valid amount greater than zero.")
            self.txt_amount.setFocus()
            return

        direction = self.cbo_dir.currentData()
        kind = "sales" if direction == "in" else "purchase"
        partner_id = self.company_id if kind == "sales" else self.supplier_id

        if not partner_id:
            partner_id = self.supplier_id if kind == "sales" else self.company_id

        if not partner_id:
            QMessageBox.critical(self, "Error", "Partner ID could not be determined.")
            return

        method = self.cbo_method.currentText()
        notes = self.txt_notes.text().strip()
        qd = self.dt_date.date()
        entry_d = date(qd.year(), qd.month(), qd.day())

        try:
            self.recorded_id = OrderProfileService.record_advance(
                kind=kind,
                partner_id=partner_id,
                direction=direction,
                amount=amt,
                method=method,
                note=notes,
                entry_date=entry_d,
            )
            QMessageBox.information(
                self, "Advance Recorded",
                f"Successfully recorded ₦{amt:,.2f} advance for {self.partner_name}.\n"
                f"Status: Available in profile."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Record Advance", str(e))
