# apps/product_management_app/features/debt_center/views/outflow_sheets.py
"""
Expenses and Expenditures as two sheets (tabs along the bottom, like alg-erp):

    Expenses       day-to-day running costs   (own table, EXP- numbers)
    Expenditures   larger outlays             (own table, EXD- numbers)
"""
from __future__ import annotations

import csv
from datetime import date, timedelta

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from database.services.expense_service import ExpenditureService, ExpenseService, METHODS
from shared.uix.widgets.cards import Segmented, money
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid, today_iso

PERIODS = [("today", "Today"), ("week", "This week"), ("month", "This month"), ("year", "This year"),
           ("all", "All time")]
SHEETS = [("expenses", ExpenseService, "Expenses", "day-to-day running costs (rent, wages, fuel, electricity ...)"),
          ("expenditures", ExpenditureService, "Expenditures", "larger outlays (equipment, vehicles, building work ...)")]
METHOD_OPTIONS = [m.title() if m != "pos" else "POS" for m in METHODS]


def period_range(key: str, today: date = None):
    today = today or date.today()
    if key == "today":
        return today, today
    if key == "week":
        return today - timedelta(days=today.weekday()), today
    if key == "month":
        return today.replace(day=1), today
    if key == "year":
        return today.replace(month=1, day=1), today
    return None, None


class OutflowSheetsPage(QWidget):
    back = Signal()
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        self.index = 0
        self._rows = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.btn_back = QPushButton("<  Debt Center")
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t = QLabel("Expenses & Expenditures")
        t.setObjectName("dcTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("dcSub")
        tv.addWidget(t)
        tv.addWidget(self.subtitle)
        head.addLayout(tv)
        head.addStretch(1)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_period = kpi("In the chosen period", dark=True)
        self.k_today = kpi("Today")
        self.k_month = kpi("This month")
        self.k_count = kpi("Rows")
        for k in (self.k_period, self.k_today, self.k_month, self.k_count):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add row")
        self.btn_add.setObjectName("msBig")
        self.btn_remove = QPushButton("Remove row")
        self.btn_remove.setObjectName("dcGhost")
        self.btn_restore = QPushButton("Bring row back")
        self.btn_restore.setObjectName("dcGhost")
        for b in (self.btn_add, self.btn_remove, self.btn_restore):
            bar.addWidget(b)
        bar.addStretch(1)
        self.period = Segmented(PERIODS)
        self.period.set_key("month")
        bar.addWidget(self.period)
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setObjectName("dcGhost")
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

        self.grid = SheetGrid(
            columns=[
                {"key": "date", "label": "Date", "type": "date", "width": 100},
                {"key": "description", "label": "What it was for", "type": "text", "width": 220, "stretch": True},
                {"key": "category", "label": "Category", "type": "choice", "editable": True, "options": [],
                 "width": 150},
                {"key": "paid_to", "label": "Paid to", "type": "text", "width": 120},
                {"key": "method", "label": "How", "type": "choice", "options": METHOD_OPTIONS, "width": 95},
                {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 130, "tone": "#1c2333"},
                {"key": "notes", "label": "Note", "type": "text", "width": 140},
            ],
            computed=[{"key": "running", "label": "Running total\nauto", "width": 160}],
            first_edit_key="description", placeholder_key="description",
            defaults={"date": today_iso(), "method": "Cash"},
            ready=self._ready, on_add=self._add, on_edit=self._edit, on_remove=self._remove,
            on_restore=lambda rid: self.service.restore(rid), reload=self.reload,
            hint="Type in the last row to add one - “What it was for” and “Amount” save it (the date starts as today, "
                 "“How” as Cash). Click a cell to change it; every change is kept in the audit trail. Right-click a "
                 "row to remove it - it stays, crossed out, and stops counting.")
        root.addWidget(self.grid, 1)

        tabs = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setObjectName("msSheets")
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setElideMode(Qt.ElideNone)
        for _k, _svc, label, _d in SHEETS:
            self.tabs.addTab(label)
        tabs.addWidget(self.tabs)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self.tabs.currentChanged.connect(self._on_tab)
        self.period.changed.connect(lambda *_: self.reload())
        self.btn_add.clicked.connect(self.grid.add_row)
        self.btn_remove.clicked.connect(lambda: self.grid.remove())
        self.btn_restore.clicked.connect(self.grid.restore)
        self.btn_export.clicked.connect(self._export)
        self._on_tab(0)

    # ------------------------------------------------------------------ state
    @property
    def service(self):
        return SHEETS[self.index][1]

    @property
    def label(self) -> str:
        return SHEETS[self.index][2]

    def _on_tab(self, i: int) -> None:
        self.index = max(i, 0)
        self.subtitle.setText(f"{self.label}: {SHEETS[self.index][3]}. Kept separately from the other sheet.")
        self.reload()

    # ------------------------------------------------------------------ grid callbacks
    def _ready(self, d) -> str:
        if not str(d.get("description", "")).strip():
            return "Now type what it was for to save the row."
        try:
            amt = float(str(d.get("amount") or 0).replace(",", ""))
        except ValueError:
            return "The amount must be a number."
        return "" if amt > 0 else "Now type the amount (above zero) to save the row."

    def _add(self, values) -> None:
        self.service.add({**values, "method": (values.get("method") or "Cash").lower()})
        self.changed.emit()

    def _row_by_id(self, rid):
        return next((r for r in self._rows if r["id"] == rid), None)

    def _edit(self, row_id, key, raw) -> None:
        r = self._row_by_id(row_id)
        if r is None:
            raise ValueError("That row no longer exists.")
        data = {"date": r["date"], "category": r["category"], "description": r["description"], "paid_to": r["paid_to"],
                "amount": r["amount"], "method": r["method"], "reference": r["reference"], "notes": r["notes"]}
        data[key] = raw.lower() if key == "method" else raw
        self.service.update(row_id, data)
        self.changed.emit()

    def _remove(self, row_id, reason) -> None:
        self.service.void(row_id, reason or "Removed from the sheet")
        self.changed.emit()

    # ------------------------------------------------------------------ data
    def reload(self) -> None:
        svc = self.service
        start, end = period_range(self.period.key())
        self._rows = sorted(svc.list_expenses(start, end, include_voided=True), key=lambda r: (r["date"], r["id"]))
        self.grid.columns[2]["options"] = svc.categories()
        run = 0.0
        rows = []
        for r in self._rows:
            removed = r["status"] == "voided"
            if not removed:
                run += r["amount"]
            rows.append({
                "id": r["id"], "removed": removed,
                "cells": {"date": r["date"].isoformat(), "description": r["description"], "category": r["category"],
                          "paid_to": r["paid_to"], "method": r["method"].upper() if r["method"] == "pos" else r["method"].title(),
                          "amount": r["amount"], "notes": r["notes"]},
                "computed": {"running": "" if removed else money(run)},
                "tip": (f"{r['expense_no']} - removed: {r['void_reason']}" if removed else r["expense_no"]),
            })
        sm = svc.summary(start, end)
        self.grid.set_rows(rows, {"amount": money(sm["total"]), "running": money(sm["total"])})
        label = dict(PERIODS)[self.period.key()]
        self.k_period.val.setText(money(sm["total"]))
        self.k_period.cap.setText(f"{self.label} - {label.lower()}")
        self.k_today.val.setText(money(sm["today"]))
        self.k_month.val.setText(money(sm["month"]))
        self.k_count.val.setText(str(sm["count"]))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export sheet", f"{self.label.lower()}.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["No.", "Date", "What it was for", "Category", "Paid to", "How", "Amount", "Note", "Removed"])
            for r in self._rows:
                w.writerow([r["expense_no"], r["date"].isoformat(), r["description"], r["category"], r["paid_to"],
                            r["method"], f"{r['amount']:.2f}", r["notes"], "yes" if r["status"] == "voided" else ""])
        QMessageBox.information(self, "Debt Center", f"Saved to\n{path}")
