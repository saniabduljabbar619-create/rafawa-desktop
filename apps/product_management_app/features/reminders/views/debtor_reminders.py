# apps/product_management_app/features/reminders/views/debtor_reminders.py
# -*- coding: utf-8 -*-
"""
Who still owes us: customers with an Advance OUT not yet used up / paid back, and unpaid or part-paid sales -
each line with when it was given, how long ago, what was paid and what is left.

    DebtorRemindersPanel   the list (also the sidebar page "Debt Reminders")
    DailyReminderDialog    the same list in a window, shown once a day by the scheduler
"""
from __future__ import annotations

import csv
import logging

from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor
from PySide2.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.reminder_service import ReminderService

log = logging.getLogger("rafawa.reminders")

HEADERS = ["Customer", "Phone", "What", "Ref", "Given on", "Days ago", "Total", "Paid", "Balance", "Status"]
_WHAT = {"advance_out": "Advance OUT", "sale": "Sale"}
_STATUS = {
    ("sale", "unpaid"): "Unpaid", ("sale", "partial"): "Part paid",
    ("advance_out", "unused"): "Not used yet", ("advance_out", "partial"): "Partly used",
}
_AMBER, _RED = QColor("#FFF3D6"), QColor("#FDE2E2")
STALE_DAYS, OVERDUE_DAYS = 14, 30


def _money(v) -> str:
    return f"{float(v or 0):,.2f}"


class DebtorRemindersPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {"groups": []}
        lay = QVBoxLayout(self)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        row = QHBoxLayout()
        self.kind_cb = QComboBox()
        self.kind_cb.addItem("All", "all")
        self.kind_cb.addItem("Advance OUT only", "advance_out")
        self.kind_cb.addItem("Unpaid / part-paid sales only", "sale")
        row.addWidget(QLabel("Show:"))
        row.addWidget(self.kind_cb)
        row.addWidget(QLabel("Waiting at least"))
        self.days_sp = QSpinBox()
        self.days_sp.setRange(0, 3650)
        self.days_sp.setSuffix(" days")
        row.addWidget(self.days_sp)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name / phone / ref...")
        row.addWidget(self.search, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_balance = QPushButton("Print Balance...")
        self.btn_balance.setToolTip("Print what is left on the selected line (Hausa or English)")
        self.btn_export = QPushButton("Export CSV")
        for b in (self.btn_refresh, self.btn_balance, self.btn_export):
            row.addWidget(b)
        lay.addLayout(row)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(False)
        lay.addWidget(self.table, 1)

        self.legend = QLabel(f"Amber: waiting {STALE_DAYS}+ days   |   Red: waiting {OVERDUE_DAYS}+ days")
        self.legend.setStyleSheet("color:#6b7280;")
        lay.addWidget(self.legend)

        self.btn_refresh.clicked.connect(self.reload)
        self.btn_balance.clicked.connect(self._print_balance)
        self.btn_export.clicked.connect(self._export)
        self.kind_cb.currentIndexChanged.connect(self._fill)
        self.days_sp.valueChanged.connect(self._fill)
        self.search.textChanged.connect(self._fill)
        self.reload()

    # ------------------------------------------------------------------
    def reload(self):
        try:
            self._data = ReminderService.collect()
        except Exception as e:
            log.exception("Could not load the debtor reminders")
            QMessageBox.warning(self, "Debt Reminders", f"Could not load the list:\n{e}")
            self._data = {"groups": [], "parties": 0, "total": 0, "advance_out_total": 0, "sales_total": 0}
        self._fill()

    def data(self) -> dict:
        return self._data

    def _visible_rows(self):
        kind = self.kind_cb.currentData()
        min_days = self.days_sp.value()
        needle = self.search.text().strip().lower()
        rows = []
        for g in self._data.get("groups", []):
            for it in g["items"]:
                if kind != "all" and it["type"] != kind:
                    continue
                if it["days"] < min_days:
                    continue
                hay = f"{g['name']} {g['phone']} {it['ref']}".lower()
                if needle and needle not in hay:
                    continue
                rows.append((g, it))
        return rows

    def _fill(self):
        d = self._data
        self.summary.setText(
            f"<b>{d.get('parties', 0)}</b> customer(s) still owe us <b>\u20a6{_money(d.get('total'))}</b> - "
            f"Advance OUT left \u20a6{_money(d.get('advance_out_total'))}, "
            f"unpaid / part-paid sales \u20a6{_money(d.get('sales_total'))}.")
        rows = self._visible_rows()
        self.table.setRowCount(len(rows))
        for r, (g, it) in enumerate(rows):
            given = it["given_at"].strftime("%Y-%m-%d %H:%M") if it.get("given_at") else ""
            cells = [g["name"], g["phone"], _WHAT.get(it["type"], it["type"]), it["ref"], given,
                     str(it["days"]), _money(it["total"]), _money(it["paid"]), _money(it["balance"]),
                     _STATUS.get((it["type"], it["status"]), it["status"])]
            shade = _RED if it["days"] >= OVERDUE_DAYS else _AMBER if it["days"] >= STALE_DAYS else None
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if c in (5, 6, 7, 8):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if shade is not None:
                    cell.setBackground(QBrush(shade))
                    cell.setForeground(QBrush(QColor("#111827")))
                if c == 0:
                    cell.setData(Qt.UserRole, {"type": it["type"], "doc_id": it["doc_id"],
                                               "party_id": g["party_id"], "walkin_name": g["walkin_name"]})
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    # ------------------------------------------------------------------
    def _selected_meta(self):
        r = self.table.currentRow()
        item = self.table.item(r, 0) if r >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def _print_balance(self):
        meta = self._selected_meta()
        if not meta:
            QMessageBox.information(self, "Print Balance", "Select a line first.")
            return
        from apps.product_management_app.features.receipts_v2 import open_balance_slip
        open_balance_slip(self, "advance" if meta["type"] == "advance_out" else "sale", meta["doc_id"])

    def _export(self):
        rows = self._visible_rows()
        if not rows:
            QMessageBox.information(self, "Export", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export debt reminders", "debt_reminders.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(HEADERS)
                for g, it in rows:
                    w.writerow([g["name"], g["phone"], _WHAT.get(it["type"], it["type"]), it["ref"],
                                it["given_at"].strftime("%Y-%m-%d %H:%M") if it.get("given_at") else "",
                                it["days"], f"{it['total']:.2f}", f"{it['paid']:.2f}", f"{it['balance']:.2f}",
                                _STATUS.get((it["type"], it["status"]), it["status"])])
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Could not save the file:\n{e}")
            return
        QMessageBox.information(self, "Export", f"Saved to:\n{path}")


class DebtorRemindersView(QWidget):
    """The sidebar page: a title and the live list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        title = QLabel("Debt Reminders - who still owes us")
        title.setStyleSheet("font-size:18px; font-weight:800;")
        lay.addWidget(title)
        lay.addWidget(DebtorRemindersPanel(self), 1)


class DailyReminderDialog(QDialog):
    """Shown once a day. After exec_(): `.choice` is "done" (until tomorrow) or "later" (ask again in 2 hours)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = "done"
        self.setWindowTitle("Daily reminder - who still owes us")
        self.resize(1100, 560)
        lay = QVBoxLayout(self)
        self.panel = DebtorRemindersPanel(self)
        lay.addWidget(self.panel, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        later = QPushButton("Remind me in 2 hours")
        done = QPushButton("Done for today")
        done.setObjectName("PrimaryButton")
        row.addWidget(later)
        row.addWidget(done)
        lay.addLayout(row)
        later.clicked.connect(self._later)
        done.clicked.connect(self.accept)

    def _later(self):
        self.choice = "later"
        self.accept()
