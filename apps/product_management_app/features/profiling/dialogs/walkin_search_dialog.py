# apps/product_management_app/features/profiling/dialogs/walkin_search_dialog.py
# -*- coding: utf-8 -*-
"""
Opened from the Walk-ins screen's single "Walk-in space" row. Walk-ins are
no longer browsed as a full list up front - the operator types a name (or
phone) here, picks the match, and only that one person's row ever lands
back on the Walk-ins screen.

Deliberately a live-filter-as-you-type table rather than a hunt-and-scroll
list: with 50+ shops feeding into one shared walk-in pool, the roster can
get long, and search is the faster path to one known name.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state, Customer
from shared.config.constants import WALKIN_PARTY_ID


def weighings_text(w) -> str:
    """The weighing numbers behind a typed walk-in name, newest last: 'WT-00012, WT-00015 (+2 more)'."""
    refs = list(getattr(w, "refs", None) or [])
    if not refs:
        return ""
    shown = ", ".join(refs[-2:])
    return shown + (f" (+{len(refs) - 2} more)" if len(refs) > 2 else "")


def find_walkins(text: str):
    """Walk-in accounts plus every typed walk-in name (weighings, sales, purchases) matching the text."""
    q = (text or "").strip().lower()
    if not q:
        return []              # nothing typed yet - don't dump the whole roster
    out = [w for w in state.walkins if q in f"{w.full_name} {w.phone}".lower()]
    try:
        from database.services.walkin_service import WalkinService
        named = WalkinService.list_named(q)
    except Exception:
        named = []
    for d in named:
        w = Customer(id=WALKIN_PARTY_ID, full_name=d["name"], phone="", email="",
                     address="Walk-in Client", status="active", notes="")
        w.walkin_name = d["name"]
        w.pending = d["pending"]
        w.refs = d.get("refs", [])
        out.append(w)
    return out


class WalkinSearchDialog(QDialog):
    """
    exec_() as usual; on QDialog.Accepted, `self.selected_walkin` holds the
    chosen walk-in object (a Customer-shaped record from state.walkins).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_walkin = None

        self.setWindowTitle("Find Walk-in Trader")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(560, 460)

        layout = QVBoxLayout(self)

        title = QLabel("Search for a walk-in trader")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel("Type a name, phone number or weighing ID (e.g. 12 or WT-00012), then pick the match.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("e.g. Abduljabbar Sani")
        self.search_in.setFocus()
        search_row.addWidget(self.search_in, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Full Name", "Phone / Weighings", "Status"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Open")
        self.btn_select.setObjectName("PrimaryButton")
        self.btn_select.setEnabled(False)
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_select)
        layout.addLayout(footer)

        self.search_in.textChanged.connect(self._refresh)
        self.table.itemSelectionChanged.connect(
            lambda: self.btn_select.setEnabled(self.table.currentRow() >= 0)
        )
        self.table.cellDoubleClicked.connect(lambda *_: self._confirm())
        self.btn_select.clicked.connect(self._confirm)
        self.btn_cancel.clicked.connect(self.reject)

        self._matches = []
        self._refresh()

    def _refresh(self):
        self._matches = find_walkins(self.search_in.text())
        self.table.setRowCount(0)
        for w in self._matches:
            row = self.table.rowCount()
            self.table.insertRow(row)
            pend = getattr(w, "pending", 0)
            cells = [w.full_name, w.phone or weighings_text(w), f"{pend} waiting for cashier" if pend else w.status]
            for c, val in enumerate(cells):
                self.table.setItem(row, c, QTableWidgetItem(str(val or "")))
        self.btn_select.setEnabled(False)

    def _confirm(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._matches):
            QMessageBox.information(self, "Find Walk-in Trader", "Select a match first.")
            return
        self.selected_walkin = self._matches[row]
        self.accept()