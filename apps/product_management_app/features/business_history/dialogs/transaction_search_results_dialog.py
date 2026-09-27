# apps/product_management_app/features/business_history/dialogs/transaction_search_results_dialog.py
# -*- coding: utf-8 -*-
"""
Global ID search results - opened from CashierHistoryListView when a
doc-number search (e.g. "SAL-123") matches one or more transactions,
across every user. Shows who created each hit and which party it
involves; double-clicking a row opens that transaction's real detail
dialog directly (same target as CashierBusinessHistoryDialog._open_detail).
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor


def _money(v) -> str:
    try:
        return f"₦{float(v or 0):,.2f}"
    except Exception:
        return "₦0.00"


_STATUS_LABELS = {"paid": "Paid", "partial": "Partial", "unpaid": "Unpaid"}
_STATUS_COLORS = {"paid": "#16a34a", "partial": "#d97706", "unpaid": "#dc2626"}
_KIND_COLORS = {"purchase": "#2563eb", "sale": "#7c3aed"}


class TransactionSearchResultsDialog(QDialog):
    def __init__(self, query: str, rows: list, parent=None):
        super().__init__(parent)
        self.rows = rows

        self.setWindowTitle(f"Search Results - \"{query}\"")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(900, 480)

        layout = QVBoxLayout(self)

        title = QLabel(f'Results for "{query}"')
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel("Double-click a row to open its full detail.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Reference", "Party", "User", "When", "Amount", "Status"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_detail)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel(f"{len(rows)} match(es).")
        self.count_label.setStyleSheet("color: #666;")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self._fill_table()

    def _fill_table(self):
        self.table.setRowCount(len(self.rows))
        for r, t in enumerate(self.rows):
            ref_item = QTableWidgetItem(t.get("doc_no", ""))
            kind_color = _KIND_COLORS.get(t.get("kind"))
            if kind_color:
                ref_item.setForeground(QColor(kind_color))
            ref_item.setData(Qt.UserRole, (t.get("kind"), t.get("id")))
            self.table.setItem(r, 0, ref_item)

            self.table.setItem(r, 1, QTableWidgetItem(t.get("party_name", "")))
            self.table.setItem(r, 2, QTableWidgetItem(t.get("created_by", "")))

            when = t.get("when")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            self.table.setItem(r, 3, QTableWidgetItem(when_s))

            amount_item = QTableWidgetItem(_money(t.get("amount")))
            amount_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 4, amount_item)

            status = t.get("payment_status", "")
            status_item = QTableWidgetItem(_STATUS_LABELS.get(status, status))
            color = _STATUS_COLORS.get(status)
            if color:
                status_item.setForeground(QColor(color))
            self.table.setItem(r, 5, status_item)

    def _open_detail(self, row: int, _col: int):
        item = self.table.item(row, 0)
        if item is None:
            return
        kind_id = item.data(Qt.UserRole)
        if not kind_id:
            return
        kind, doc_id = kind_id

        from apps.product_management_app.features.suppliers.views.supplier_activity_dialogs import (
            SupplierSaleDetailDialog, SupplierPurchaseDetailDialog,
        )
        try:
            if kind == "sale":
                dlg = SupplierSaleDetailDialog(doc_id, parent=self)
            else:
                dlg = SupplierPurchaseDetailDialog(doc_id, parent=self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Detail Failed", str(e))