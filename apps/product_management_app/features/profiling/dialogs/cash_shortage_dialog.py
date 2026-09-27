# apps/product_management_app/features/profiling/dialogs/cash_shortage_dialog.py
# -*- coding: utf-8 -*-
"""
Records a cash-handling shortage/overpayment correction - not a
sale/purchase balance problem, a physical cash-handling mistake caught
after the fact. See CashShortage's own docstring for the full reasoning.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDoubleSpinBox,
    QTextEdit, QPushButton, QMessageBox
)
from PySide2.QtCore import Qt


class CashShortageDialog(QDialog):
    def __init__(self, party_id: int, party_name: str,
                reference_type: str, reference_id: int,
                created_by_user_id: int, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.reference_type = reference_type
        self.reference_id = int(reference_id)
        self.created_by_user_id = int(created_by_user_id)

        self.setWindowTitle("Record Cash Shortage")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(520, 400)

        layout = QVBoxLayout(self)

        title = QLabel("Record Cash Shortage")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(
            f"For {party_name} - a physical cash-handling mistake caught "
            f"after the fact, not a problem with the transaction itself. "
            f"This stays separate from every balance and summary card - "
            f"it only ever shows here, in the payment dialogs, until it's "
            f"settled."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Who must pay:"))
        self.direction_cb = QComboBox()
        self.direction_cb.addItem(f"Company must pay {party_name} (shortage given to them)", "owed_to_party")
        self.direction_cb.addItem(f"{party_name} must pay the company (overpaid to them)", "owed_to_company")
        dir_row.addWidget(self.direction_cb, 1)
        layout.addLayout(dir_row)

        amt_row = QHBoxLayout()
        amt_row.addWidget(QLabel("Amount:"))
        self.amount_in = QDoubleSpinBox()
        self.amount_in.setDecimals(2)
        self.amount_in.setMaximum(100_000_000.0)
        amt_row.addWidget(self.amount_in, 1)
        layout.addLayout(amt_row)

        layout.addWidget(QLabel("Notes (what happened, when it was caught):"))
        self.notes_in = QTextEdit()
        self.notes_in.setPlaceholderText(
            "e.g. Customer recounted after leaving, confirmed short by "
            "20,000. Verified with cashier on [date]."
        )
        self.notes_in.setMaximumHeight(100)
        layout.addWidget(self.notes_in)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Record Shortage")
        self.btn_save.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)

    def _save(self):
        amount = float(self.amount_in.value())
        if amount <= 0:
            QMessageBox.warning(self, "Record Shortage", "Enter an amount greater than zero.")
            return

        direction = self.direction_cb.currentData()
        notes = self.notes_in.toPlainText().strip()

        from database.services.cash_shortage_service import CashShortageService
        try:
            CashShortageService.record_shortage(
                party_id=self.party_id,
                reference_type=self.reference_type,
                reference_id=self.reference_id,
                direction=direction,
                amount=amount,
                created_by_user_id=self.created_by_user_id,
                notes=notes,
            )
        except Exception as e:
            QMessageBox.critical(self, "Record Failed", str(e))
            return

        QMessageBox.information(
            self, "Recorded",
            "Shortage recorded. It will show as a banner here in the "
            "payment dialog until it's settled - it won't appear on any "
            "balance or summary card."
        )
        self.accept()