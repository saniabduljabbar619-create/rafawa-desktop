# apps/product_management_app/features/cashier/dialogs/party_search_picker_dialog.py
# -*- coding: utf-8 -*-
"""
A real, searchable table of Party rows - generic and reusable, since
"search customers" and "search suppliers" are the exact same shape,
just filtered by a different role flag (is_customer vs is_supplier).
Matches the search-first pattern already proven for Company/Supplier/
Driver pickers in Sales/Purchase Orders, applied here to the app's
regular, shared Party table instead of an isolated PO/SO-only entity.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PySide2.QtCore import Qt


class PartySearchPickerDialog(QDialog):
    """
    exec_() then read .selected_party_id / .selected_party_name.

    role_flag: "is_customer" or "is_supplier" - which Party column to
    filter on ("any" lists every registered party except walk-ins). title/search_placeholder let the caller phrase this for
    whichever role it's actually being used for.
    """
    def __init__(self, role_flag: str, title: str = "Select", parent=None):
        super().__init__(parent)
        assert role_flag in ("is_customer", "is_supplier", "any"), "role_flag must be is_customer, is_supplier or any"
        self.role_flag = role_flag
        self.selected_party_id = None
        self.selected_party_name = None

        self.setWindowTitle(title)
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(600, 480)

        layout = QVBoxLayout(self)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title_label)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Name or phone…")
        self.search_in.textChanged.connect(self._reload)
        search_row.addWidget(self.search_in, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Phone"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self._select())
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Select")
        self.btn_select.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_select)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_select.clicked.connect(self._select)

        self._rows = []
        self._reload()
        self.search_in.setFocus()

    def _reload(self, *_args):
        from database.session import get_session
        from database.models.party import Party

        term = (self.search_in.text() or "").strip()
        with get_session() as s:
            q = s.query(Party).filter(Party.is_active == True)  # noqa: E712
            if self.role_flag != "any":                  # "any" = every real party (customers AND suppliers)
                q = q.filter(getattr(Party, self.role_flag) == True)  # noqa: E712
            else:
                q = q.filter(Party.party_type != "walkin")
            if term:
                q = q.filter(
                    (Party.display_name.ilike(f"%{term}%")) |
                    (Party.phone.ilike(f"%{term}%"))
                )
            rows = q.order_by(Party.display_name.asc()).limit(100).all()

        self._rows = [{"id": p.id, "name": p.display_name, "phone": p.phone or ""} for p in rows]
        self.table.setRowCount(len(self._rows))
        for r, p in enumerate(self._rows):
            for c, text in enumerate([p["name"], p["phone"]]):
                it = QTableWidgetItem(str(text))
                it.setData(Qt.UserRole, p["id"])
                self.table.setItem(r, c, it)

    def _select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Select", "Select an entry from the list first.")
            return
        self.selected_party_id = self._rows[row]["id"]
        self.selected_party_name = self._rows[row]["name"]
        self.accept()