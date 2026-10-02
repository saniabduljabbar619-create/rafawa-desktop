# apps/product_management_app/features/debt_center/views/expenses_page.py
"""Expenses & Expenditure: what the business spends, by period and category, with a trail."""
from __future__ import annotations

from datetime import date, timedelta

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor, QPainter
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.expense_service import ExpenseService
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import KpiCard, Segmented, chip, money, style_table
from apps.product_management_app.features.debt_center.dialogs.expense_dialog import ExpenseDialog

PERIODS = [("today", "Today"), ("week", "This week"), ("month", "This month"), ("year", "This year"),
           ("all", "All time")]
COLS = ["No.", "Date", "Category", "Description", "Paid to", "Method", "Amount"]
_BAR_COLORS = ["#0f766e", "#1d4ed8", "#7c3aed", "#b45309", "#be123c", "#0369a1", "#4d7c0f", "#475569"]


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


class _Bars(QWidget):
    """Tiny bar chart: (label, value) pairs, drawn with QPainter so there is no chart dependency."""

    def __init__(self, color: str = "#0f766e"):
        super().__init__()
        self.items = []
        self.color = QColor(color)
        self.setMinimumHeight(150)

    def set_items(self, items):
        self.items = list(items)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.items:
            p.setPen(QColor("#9ca3af"))
            p.drawText(self.rect(), Qt.AlignCenter, "No data")
            return
        top = max(v for _l, v in self.items) or 1.0
        n = len(self.items)
        w, h = self.width(), self.height() - 36
        slot = w / n
        bw = min(slot * 0.55, 54)
        for i, (label, val) in enumerate(self.items):
            bh = (val / top) * (h - 14)
            x = i * slot + (slot - bw) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(self.color)
            p.drawRoundedRect(x, 16 + (h - 14) - bh, bw, max(bh, 1), 4, 4)
            p.setPen(QColor("#374151"))
            p.drawText(int(i * slot), 0, int(slot), 14, Qt.AlignCenter, f"{val:,.0f}" if val else "")
            p.setPen(QColor("#6b7280"))
            p.drawText(int(i * slot), self.height() - 20, int(slot), 18, Qt.AlignCenter, label)


class ExpensesPage(QWidget):
    back = Signal()
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._rows = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.btn_back = QPushButton("<  Debt Center")
        self.btn_back.setProperty("role", "plain")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("Expenses & Expenditure")
        t.setObjectName("Title")
        s = QLabel("Everything the business spends that is not stock")
        s.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles)
        head.addStretch(1)
        self.btn_export = QPushButton("Export CSV")
        head.addWidget(self.btn_export)
        self.btn_add = QPushButton("+  New Expense")
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.setMinimumHeight(36)
        head.addWidget(self.btn_add)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_today = KpiCard("Today", "0.00", "", "blue")
        self.kpi_week = KpiCard("This week", "0.00", "", "amber")
        self.kpi_month = KpiCard("This month", "0.00", "", "red")
        self.kpi_filtered = KpiCard("Selected period", "0.00", "", "green")
        for k in (self.kpi_today, self.kpi_week, self.kpi_month, self.kpi_filtered):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        charts = QHBoxLayout()
        charts.setSpacing(12)
        self.trend = _Bars("#0f766e")
        self.cat_box = QVBoxLayout()
        self.cat_box.setSpacing(6)
        for title, inner in (("Last 6 months", self.trend), ("Where the money goes", None)):
            card = QFrame()
            card.setObjectName("ChartCard")
            card.setStyleSheet("QFrame#ChartCard { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; }"
                               "QFrame#ChartCard QLabel { background:transparent; border:none; }")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            lab = QLabel(title.upper())
            lab.setStyleSheet("color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px;")
            cl.addWidget(lab)
            if inner is not None:
                cl.addWidget(inner)
            else:
                self.cat_holder = QWidget()
                self.cat_holder.setLayout(self.cat_box)
                cl.addWidget(self.cat_holder)
                cl.addStretch(1)
            charts.addWidget(card, 1)
        root.addLayout(charts)

        bar = QHBoxLayout()
        self.seg = Segmented(PERIODS)
        self.seg.set_key("month")
        bar.addWidget(self.seg)
        self.category = QComboBox()
        self.category.setMinimumWidth(170)
        bar.addWidget(self.category)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search description, payee or reference")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        bar.addWidget(self.search, 1)
        self.chk_void = QPushButton("Show voided")
        self.chk_void.setCheckable(True)
        self.chk_void.setProperty("role", "plain")
        bar.addWidget(self.chk_void)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        style_table(self.table, 40)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(260)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_void = QPushButton("Void")
        self.btn_void.setProperty("role", "danger")
        self.lbl_total = QLabel("")
        self.lbl_total.setStyleSheet("font-weight:700; color:#111827;")
        foot.addWidget(self.btn_edit)
        foot.addWidget(self.btn_void)
        foot.addStretch(1)
        foot.addWidget(self.lbl_total)
        root.addLayout(foot)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_void.clicked.connect(self._void)
        self.btn_export.clicked.connect(self._export)
        self.table.cellDoubleClicked.connect(lambda *_: self._edit())
        self.seg.changed.connect(lambda *_: self.reload())
        self.category.currentIndexChanged.connect(lambda *_: self.reload())
        self.search.textChanged.connect(lambda *_: self.reload())
        self.chk_void.toggled.connect(lambda *_: self.reload())
        self._fill_categories()

    def _fill_categories(self) -> None:
        keep = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", "")
        for c in ExpenseService.categories():
            self.category.addItem(c, c)
        i = self.category.findData(keep or "")
        self.category.setCurrentIndex(max(i, 0))
        self.category.blockSignals(False)

    def _filter(self):
        start, end = period_range(self.seg.key())
        return start, end, self.category.currentData() or "", self.search.text().strip()

    def reload(self) -> None:
        start, end, cat, term = self._filter()
        self._rows = ExpenseService.list_expenses(start, end, cat, term, include_voided=self.chk_void.isChecked())
        sm = ExpenseService.summary(start, end, cat, term)
        self.kpi_today.set(money(sm["today"]))
        self.kpi_week.set(money(sm["week"]))
        ch = sm["month_change"]
        self.kpi_month.set(money(sm["month"]), "no spending last month" if ch is None else
                           f"{'up' if ch >= 0 else 'down'} {abs(ch) * 100:.0f}% on last month")
        self.kpi_filtered.set(money(sm["total"]), f"{sm['count']} expense(s)")
        self.trend.set_items([(m["month"].split()[0], m["amount"]) for m in sm["trend"]])
        self._render_categories(sm["by_category"])
        self._render_table()
        self.lbl_total.setText(f"Total: {money(sm['total'])}")

    def _render_categories(self, cats) -> None:
        while self.cat_box.count():
            w = self.cat_box.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not cats:
            lab = QLabel("No expenses in this period.")
            lab.setStyleSheet("color:#9ca3af;")
            self.cat_box.addWidget(lab)
            return
        for i, c in enumerate(cats[:6]):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            name = QLabel(c["category"])
            name.setMinimumWidth(130)
            bar = QFrame()
            bar.setFixedHeight(10)
            bar.setStyleSheet(f"background:{_BAR_COLORS[i % len(_BAR_COLORS)]}; border-radius:5px;")
            bar.setFixedWidth(max(int(c["share"] * 220), 4))
            amt = QLabel(f"{money(c['amount'])}  ({c['share'] * 100:.0f}%)")
            amt.setStyleSheet("color:#6b7280; font-size:11px;")
            h.addWidget(name)
            h.addWidget(bar)
            h.addWidget(amt)
            h.addStretch(1)
            self.cat_box.addWidget(row)

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            voided = r["status"] == "voided"
            vals = [r["expense_no"], r["date"].strftime("%d %b %Y"), r["category"],
                    r["description"] + ("  [VOIDED: " + r["void_reason"] + "]" if voided else ""),
                    r["paid_to"], r["method"].title(), money(r["amount"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c == 6:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if voided:
                    it.setForeground(QColor("#9ca3af"))
                    f = it.font()
                    f.setStrikeOut(True)
                    it.setFont(f)
                self.table.setItem(i, c, it)

    def _current(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Expenses", "Select an expense first.")
            return None
        return self._rows[row]

    def _add(self) -> None:
        if ExpenseDialog(self).exec_():
            self._fill_categories()
            self.reload()
            self.changed.emit()

    def _edit(self) -> None:
        r = self._current()
        if r is None:
            return
        if r["status"] == "voided":
            QMessageBox.information(self, "Expenses", "A voided expense cannot be edited.")
            return
        if ExpenseDialog(self, expense=r).exec_():
            self._fill_categories()
            self.reload()
            self.changed.emit()

    def _void(self) -> None:
        r = self._current()
        if r is None or r["status"] == "voided":
            return
        reason, ok = QInputDialog.getText(self, "Void expense",
                                          f"Why void {r['expense_no']} ({money(r['amount'])})?")
        if not ok:
            return
        try:
            ExpenseService.void(r["id"], reason)
        except (ValueError, PermissionDenied) as e:
            QMessageBox.warning(self, "Expenses", str(e))
            return
        self.reload()
        self.changed.emit()

    def _export(self) -> None:
        import csv
        path, _ = QFileDialog.getSaveFileName(self, "Export expenses", "expenses.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["No.", "Date", "Category", "Description", "Paid to", "Method", "Reference", "Amount", "Status"])
            for r in self._rows:
                w.writerow([r["expense_no"], r["date"].isoformat(), r["category"], r["description"], r["paid_to"],
                            r["method"], r["reference"], f"{r['amount']:.2f}", r["status"]])
        QMessageBox.information(self, "Expenses", f"Saved {len(self._rows)} row(s) to\n{path}")
