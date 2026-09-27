# apps/product_management_app/features/admin_users/dialogs/party_totals_dialog.py
# -*- coding: utf-8 -*-
"""
"Who has this cashier sold to / bought from, and how much" - grouped by
party rather than the drawer's flat, per-transaction list. Defaults to
this user's own transactions (matching the drawer's own established
scoping); the "Across the whole business" toggle widens it to every
transaction system-wide, across every cashier/admin, for when the
operator wants the full business picture rather than just their own
shift.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit
)
from PySide2.QtCore import Qt


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class _SortableItem(QTableWidgetItem):
    """Sorts by a stored numeric key (Qt.UserRole) rather than the
    displayed text, so count/amount columns sort numerically, not
    lexicographically."""
    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if a is not None and b is not None:
            try:
                return a < b
            except Exception:
                pass
        return super().__lt__(other)


class PartyTotalsDialog(QDialog):
    def __init__(self, user_id: int, user_display_name: str = "", parent=None):
        super().__init__(parent)
        self.user_id = int(user_id)

        self.setWindowTitle("Total Purchases & Sales by Party")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(920, 600)

        layout = QVBoxLayout(self)

        title = QLabel("Total Purchases & Sales by Party")
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(title)

        subtitle = QLabel(
            f"Grouped by party - {user_display_name or 'this user'}'s own "
            f"transactions by default. Double-click a row to see the "
            f"individual sales and purchases behind it."
        )
        subtitle.setStyleSheet("color: #666;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        top = QHBoxLayout()
        layout.addLayout(top)

        self.scope_toggle = QCheckBox("Show across the whole business")
        self.scope_toggle.stateChanged.connect(self.refresh)
        top.addWidget(self.scope_toggle)

        top.addStretch(1)
        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Party name\u2026")
        self.search_in.textChanged.connect(self.refresh)
        top.addWidget(self.search_in, 1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Party", "Sales Count", "Total Sales", "Purchases Count", "Total Purchases"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_party)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)

        self.totals_label = QLabel("")
        self.totals_label.setStyleSheet("font-weight: 700; padding: 4px 0;")
        layout.addWidget(self.totals_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self._rows = []
        self.refresh()

    def _scope(self) -> str:
        return "all" if self.scope_toggle.isChecked() else "mine"

    def refresh(self):
        from database.services.drawer_service import DrawerService
        result = DrawerService.party_totals(user_id=self.user_id, scope=self._scope())

        q = self.search_in.text().strip().lower()
        rows = result["rows"]
        if q:
            rows = [r for r in rows if q in r["party_name"].lower()]
        self._rows = rows

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            numeric_values = [None, row["sales_count"], row["sales_total"],
                              row["purchases_count"], row["purchases_total"]]
            cells = [
                row["party_name"], str(row["sales_count"]), _money(row["sales_total"]),
                str(row["purchases_count"]), _money(row["purchases_total"]),
            ]
            for c, text in enumerate(cells):
                it = _SortableItem(str(text)) if numeric_values[c] is not None else QTableWidgetItem(str(text))
                if numeric_values[c] is not None:
                    it.setData(Qt.UserRole, numeric_values[c])
                # (party_id, party_name) travels with the FIRST column's
                # item specifically, read back on double-click - correct
                # even after the table has been sorted, unlike indexing a
                # separate, static Python list by row number.
                if c == 0:
                    it.setData(Qt.UserRole + 1, (row["party_id"], row["party_name"]))
                if c >= 1:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)

        self.totals_label.setText(
            f"Grand total - Sales: {_money(result['grand_sales_total'])}   |   "
            f"Purchases: {_money(result['grand_purchases_total'])}"
        )

    def _open_party(self, row, _col):
        item = self.table.item(row, 0)
        if item is None:
            return
        party_data = item.data(Qt.UserRole + 1)
        if not party_data:
            return
        party_id, party_name = party_data

        from apps.product_management_app.features.admin_users.dialogs.party_transactions_dialog import (
            PartyTransactionsDialog,
        )
        dlg = PartyTransactionsDialog(
            party_id=party_id, party_name=party_name,
            user_id=self.user_id, scope=self._scope(), parent=self,
        )
        dlg.exec_()