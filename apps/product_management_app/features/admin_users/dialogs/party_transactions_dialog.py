# apps/product_management_app/features/admin_users/dialogs/party_transactions_dialog.py
# -*- coding: utf-8 -*-
"""
The individual sales and purchases behind one party's row in the Party
Totals dialog - reached by double-clicking that row. Double-clicking a
transaction here in turn opens the existing, already-built full-detail
dialog (SupplierSaleDetailDialog / SupplierPurchaseDetailDialog) rather
than duplicating that work.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide2.QtCore import Qt


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


_STATUS_LABELS = {"paid": "Paid", "partial": "Partial", "unpaid": "Unpaid"}
_STATUS_COLORS = {"paid": "#16a34a", "partial": "#d97706", "unpaid": "#dc2626"}


class _SortableItem(QTableWidgetItem):
    """Sorts by a stored numeric/date key (Qt.UserRole) rather than the
    displayed text - so Amount and When sort correctly (numerically,
    chronologically), not lexicographically (which would put ₦9,000
    after ₦10,000)."""
    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if a is not None and b is not None:
            try:
                return a < b
            except Exception:
                pass
        return super().__lt__(other)


class PartyTransactionsDialog(QDialog):
    def __init__(self, party_id: int, party_name: str, user_id: int,
                scope: str = "mine", parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.user_id = int(user_id)
        self.scope = scope

        self.setWindowTitle(f"Transactions - {party_name}")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(940, 560)

        layout = QVBoxLayout(self)

        title = QLabel(party_name)
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel("Double-click a row to open its full detail. Click a column header to sort.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        top = QHBoxLayout()
        top.addWidget(QLabel("Payment status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("All", None)
        self.status_filter.addItem("Paid", "paid")
        self.status_filter.addItem("Partial", "partial")
        self.status_filter.addItem("Unpaid", "unpaid")
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        top.addWidget(self.status_filter)
        top.addStretch(1)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["When", "Type", "Reference", "Product", "Amount", "Status", "Created By"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(self._open_detail)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self._all_rows = []      # every row fetched, unfiltered
        self.refresh()

    def refresh(self):
        from database.services.drawer_service import DrawerService
        self._all_rows = DrawerService.party_transactions(
            party_id=self.party_id, user_id=self.user_id, scope=self.scope,
        )
        self._apply_filter()

    def _apply_filter(self):
        status = self.status_filter.currentData()
        rows = self._all_rows
        if status:
            rows = [r for r in rows if r.get("payment_status") == status]
        self._fill_table(rows)

    def _fill_table(self, rows):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, t in enumerate(rows):
            when = t.get("when")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            kind_label = "Sale" if t["kind"] == "sale" else "Purchase"
            status = t.get("payment_status", "")
            status_label = _STATUS_LABELS.get(status, status)

            when_item = _SortableItem(when_s)
            when_item.setData(Qt.UserRole, when.timestamp() if when else 0)
            # (kind, id) travels WITH this item through any sort Qt performs,
            # unlike indexing a separate, static Python list by row number.
            when_item.setData(Qt.UserRole + 1, (t["kind"], t["id"]))
            self.table.setItem(r, 0, when_item)

            self.table.setItem(r, 1, QTableWidgetItem(kind_label))
            self.table.setItem(r, 2, QTableWidgetItem(t["doc_no"]))
            self.table.setItem(r, 3, QTableWidgetItem(t.get("product_summary", "")))

            amount_item = _SortableItem(_money(t["amount"]))
            amount_item.setData(Qt.UserRole, float(t["amount"] or 0))
            amount_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 4, amount_item)

            status_item = QTableWidgetItem(status_label)
            color = _STATUS_COLORS.get(status)
            if color:
                from PySide2.QtGui import QColor
                status_item.setForeground(QColor(color))
            self.table.setItem(r, 5, status_item)

            self.table.setItem(r, 6, QTableWidgetItem(t["created_by"]))
        self.table.setSortingEnabled(True)

    def _open_detail(self, row, _col):
        item = self.table.item(row, 0)
        if item is None:
            return
        kind_id = item.data(Qt.UserRole + 1)
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