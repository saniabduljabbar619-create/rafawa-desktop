# apps/product_management_app/features/closings/morning_closing_dialog.py
# -*- coding: utf-8 -*-
"""
Morning Admin Review & Confirmation Dialog for Customer Closings.
Shows all active customers ready to close since their last closing period,
lets the Admin inspect individual breakdowns, select/deselect customers,
add closing notes, and confirm closing in one batch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from database.services.closing_service import ClosingService
from apps.product_management_app.features.closings.closing_detail_dialog import ClosingDetailDialog


def _money(val: float) -> str:
    return f"\u20a6{float(val or 0):,.2f}"


class MorningClosingDialog(QDialog):
    """
    Shown every morning to the Admin (or opened manually from Customer Closings).
    After exec_(): `.choice` is "confirmed" | "later" | "skip".
    """

    def __init__(
        self,
        user_id: int,
        previews: Optional[List[Dict[str, Any]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.user_id = int(user_id)
        self.choice = "skip"
        self.confirmed_count = 0
        self._previews = previews if previews is not None else ClosingService.preview_pending_closings()

        self.setWindowTitle("Morning Customer Closings Review & Confirmation")
        self.resize(1120, 620)
        self._build_ui()
        self._populate_table()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        hdr = QLabel(
            "<span style='font-size: 16px; font-weight: bold; color: #0f172a;'>"
            "Morning Customer Closings Review (Rufe Lissafi)</span><br>"
            "<span style='color: #475569; font-size: 12px;'>"
            "Review each active customer's unclosed period below. "
            "Check the customers to close, inspect breakdowns if needed, and click <b>Confirm &amp; Close Selected</b>."
            "</span>"
        )
        hdr.setWordWrap(True)
        lay.addWidget(hdr)

        # Search + selection tools
        bar = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Filter customers by name or phone...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.txt_search, 1)

        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton("Deselect All")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        bar.addWidget(btn_all)
        bar.addWidget(btn_none)
        lay.addLayout(bar)

        # Table
        cols = [
            "Close?", "Customer", "Phone", "Period",
            "Opening", "Purchases", "Sales", "Advances (Out/In)",
            "Payments (In/Out)", "Closing Balance", "Breakdown",
        ]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)

        # Notes + summary
        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("Closing Notes:"))
        self.txt_notes = QLineEdit("Morning confirmed closing")
        note_row.addWidget(self.txt_notes, 1)
        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("font-weight: bold; color: #0f766e;")
        note_row.addWidget(self.lbl_summary)
        lay.addLayout(note_row)

        # Footer buttons
        foot = QHBoxLayout()
        btn_skip = QPushButton("Skip for Today")
        btn_skip.clicked.connect(self._on_skip)
        btn_later = QPushButton("Remind me in 2 hours")
        btn_later.clicked.connect(self._on_later)
        foot.addWidget(btn_skip)
        foot.addWidget(btn_later)
        foot.addStretch(1)

        self.btn_confirm = QPushButton("Confirm && Close Selected")
        self.btn_confirm.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 9px 22px; border-radius: 5px;"
        )
        self.btn_confirm.clicked.connect(self._on_confirm)
        foot.addWidget(self.btn_confirm)
        lay.addLayout(foot)

    def _populate_table(self):
        self.table.setRowCount(len(self._previews))
        for r, calc in enumerate(self._previews):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, int(calc["party_id"]))
            self.table.setItem(r, 0, chk)

            self.table.setItem(r, 1, QTableWidgetItem(calc.get("party_name", "")))
            self.table.setItem(r, 2, QTableWidgetItem(calc.get("party_phone", "")))
            p_str = f"{str(calc.get('period_start', ''))[:10]} -> {str(calc.get('period_end', ''))[:10]}"
            self.table.setItem(r, 3, QTableWidgetItem(p_str))

            for col_idx, val in (
                (4, _money(calc.get("opening_balance", 0))),
                (5, _money(calc.get("total_purchases", 0))),
                (6, _money(calc.get("total_sales", 0))),
                (7, f"{_money(calc.get('total_advance_out', 0))} / {_money(calc.get('total_advance_in', 0))}"),
                (8, f"{_money(calc.get('total_payments_in', 0))} / {_money(calc.get('total_payments_out', 0))}"),
            ):
                it = QTableWidgetItem(val)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, col_idx, it)

            c_bal = float(calc.get("closing_balance") or 0)
            bal_it = QTableWidgetItem(_money(c_bal))
            bal_it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c_bal > 0.005:
                bal_it.setForeground(QColor("#dc2626"))
            elif c_bal < -0.005:
                bal_it.setForeground(QColor("#059669"))
            self.table.setItem(r, 9, bal_it)

            btn_det = QPushButton("View")
            btn_det.setStyleSheet("padding: 3px 10px; font-size: 11px;")
            btn_det.clicked.connect(lambda _c=False, c=calc: self._view_detail(c))
            self.table.setCellWidget(r, 10, btn_det)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.lbl_summary.setText(f"{len(self._previews)} customer(s) ready to close")

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for r, calc in enumerate(self._previews):
            hay = f"{calc.get('party_name', '')} {calc.get('party_phone', '')}".lower()
            self.table.setRowHidden(r, bool(needle and needle not in hay))

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                it = self.table.item(r, 0)
                if it:
                    it.setCheckState(state)

    def _view_detail(self, calc: Dict[str, Any]):
        data = dict(calc)
        data.setdefault("closing_no", "PREVIEW (Unclosed)")
        data.setdefault("notes", self.txt_notes.text().strip())
        data.setdefault("created_by", "Pending Confirmation")
        ClosingDetailDialog(data, parent=self).exec_()

    def _selected_party_ids(self) -> List[int]:
        ids: List[int] = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.checkState() == Qt.Checked:
                ids.append(int(it.data(Qt.UserRole)))
        return ids

    def _on_confirm(self):
        pids = self._selected_party_ids()
        if not pids:
            QMessageBox.information(self, "Morning Closings", "No customers selected.")
            return
        notes = self.txt_notes.text().strip() or "Morning confirmed closing"
        closed = ClosingService.confirm_selected_closings(pids, user_id=self.user_id, notes=notes)
        self.confirmed_count = len(closed)
        self.choice = "confirmed"
        QMessageBox.information(
            self,
            "Closings Confirmed",
            f"Successfully closed {len(closed)} customer account(s).",
        )
        self.accept()

    def _on_later(self):
        self.choice = "later"
        self.accept()

    def _on_skip(self):
        self.choice = "skip"
        self.accept()
