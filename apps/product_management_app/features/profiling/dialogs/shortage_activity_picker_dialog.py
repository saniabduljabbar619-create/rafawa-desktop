# apps/product_management_app/features/profiling/dialogs/shortage_activity_picker_dialog.py
# -*- coding: utf-8 -*-
"""
Opened by the "Counter Shortage" button - the row picker for recording a
cash shortage/overpayment. Deliberately shows every sale/purchase for a
party regardless of payment status, including fully-paid ones: the exact
scenario Cash Shortage exists for (a mistake discovered only after a
full payment already recorded) is precisely when the transaction would
otherwise have vanished from the normal "needs payment" table.

Color-coded: a row with an open shortage against it stands out clearly
from one with none, so the operator can see at a glance which rows
still need resolving.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class ShortageActivityPickerDialog(QDialog):
    def __init__(self, party_id: int, party_name: str, reference_type: str,
                created_by_user_id: int, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.party_name = party_name
        self.reference_type = reference_type  # "sale" or "purchase"
        self.created_by_user_id = int(created_by_user_id)

        kind_label = "Sale" if reference_type == "sale" else "Purchase"
        self.setWindowTitle(f"Counter Shortage - Select {kind_label}")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(760, 480)

        layout = QVBoxLayout(self)

        title = QLabel(f"Select the {kind_label.lower()} this shortage came from")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(
            f"Every {kind_label.lower()} for {party_name} - including ones "
            f"already fully paid, since that's usually when a "
            f"cash-handling mistake gets noticed. Amber rows already have "
            f"an open correction against them."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Reference", "Date", "Goods", "Total", "Paid", "Open Correction"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Continue")
        self.btn_select.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_select)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_select.clicked.connect(self._continue)
        self.table.cellDoubleClicked.connect(lambda *_: self._continue())

        self._rows = []
        self.refresh()

    def refresh(self):
        if self.reference_type == "sale":
            from database.services.sales_service import SaleService
            self._rows = SaleService.list_activity_with_shortages(party_id=self.party_id)
        else:
            from database.services.purchase_service import PurchaseService
            self._rows = PurchaseService.list_activity_with_shortages(party_id=self.party_id)

        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            when = row.get("created_at")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            shortage = row.get("open_shortage")
            shortage_s = _money(shortage["amount"]) if shortage else "\u2014"

            cells = [
                row["reference"], when_s, row.get("product_summary", ""),
                _money(row["amount"]), _money(row.get("paid", 0.0)), shortage_s,
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setData(Qt.UserRole, row["id"])
                if c >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if shortage:
                    it.setBackground(QColor("#fff3cd"))
                self.table.setItem(r, c, it)

    def _continue(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Counter Shortage", "Select a row first.")
            return

        item = self.table.item(row, 0)
        reference_id = item.data(Qt.UserRole)

        from apps.product_management_app.features.profiling.dialogs.cash_shortage_dialog import CashShortageDialog
        dlg = CashShortageDialog(
            party_id=self.party_id, party_name=self.party_name,
            reference_type=self.reference_type, reference_id=reference_id,
            created_by_user_id=self.created_by_user_id, parent=self,
        )
        if dlg.exec_() == dlg.Accepted:
            self.refresh()  # stay open, showing the row now color-coded