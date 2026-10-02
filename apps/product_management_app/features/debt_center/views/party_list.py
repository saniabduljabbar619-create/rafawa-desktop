# apps/product_management_app/features/debt_center/views/party_list.py
"""My Creditors / My Debtors: every party with an open balance, biggest first. Double-click = worksheet."""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.debt_center_service import DebtCenterService, OVERDUE_DAYS
from shared.uix.widgets.cards import KpiCard, Segmented, money, style_table

COLS = {
    "creditors": ["Creditor", "Phone", "Open bills", "Oldest", "We owe", "Overdue", "They also owe us"],
    "debtors": ["Debtor", "Phone", "Open sales", "Oldest", "Owes us", "Overdue", "We also owe them"],
}
TEXT = {
    "creditors": ("My Creditors", "People and businesses we still owe for purchases", "red"),
    "debtors": ("My Debtors", "People and businesses who still owe us for sales", "green"),
}
FILTERS = [("all", "All"), ("overdue", f"Overdue (>{OVERDUE_DAYS} days)"), ("both", "Owe both ways")]


def age_text(days: int) -> str:
    return "Today" if days == 0 else "1 day" if days == 1 else f"{days} days"


class DebtPartyListPage(QWidget):
    open_party = Signal(int, str)          # party_id, side
    back = Signal()

    def __init__(self, side: str):
        super().__init__()
        self.side = side
        self._rows = []
        title, sub, tone = TEXT[side]
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
        lab = QLabel(title)
        lab.setObjectName("Title")
        s = QLabel(sub)
        s.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(lab)
        titles.addWidget(s)
        head.addLayout(titles)
        head.addStretch(1)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_total = KpiCard("Total " + ("payable" if side == "creditors" else "receivable"), "0.00", "", tone)
        self.kpi_count = KpiCard("Parties", "0", "with an open balance", "blue")
        self.kpi_overdue = KpiCard("Overdue", "0.00", f"older than {OVERDUE_DAYS} days", "amber")
        for k in (self.kpi_total, self.kpi_count, self.kpi_overdue):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name or phone")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        bar.addWidget(self.search, 1)
        self.seg = Segmented(FILTERS)
        bar.addWidget(self.seg)
        self.btn_refresh = QPushButton("Refresh")
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLS[side]))
        self.table.setHorizontalHeaderLabels(COLS[side])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        style_table(self.table, 46)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open Worksheet")
        self.btn_open.setObjectName("PrimaryButton")
        foot.addWidget(self.btn_open)
        foot.addStretch(1)
        hint = QLabel("Double-click a row to open the worksheet and make a payment")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        foot.addWidget(hint)
        root.addLayout(foot)

        self.search.textChanged.connect(self._render)
        self.seg.changed.connect(self._render)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_open.clicked.connect(self._open_selected)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))

    def reload(self) -> None:
        self._rows = DebtCenterService.parties(self.side)
        self.kpi_total.set(money(sum(r["balance"] for r in self._rows)),
                           f"across {len(self._rows)} " + ("creditors" if self.side == "creditors" else "debtors"))
        self.kpi_count.set(str(len(self._rows)))
        self.kpi_overdue.set(money(sum(r["overdue"] for r in self._rows)))
        self._render()

    def _visible(self):
        term = self.search.text().strip().lower()
        mode = self.seg.key()
        out = []
        for r in self._rows:
            if term and term not in r["name"].lower() and term not in r["phone"].lower():
                continue
            if mode == "overdue" and r["overdue"] <= 0:
                continue
            if mode == "both" and r["opposite"] <= 0:
                continue
            out.append(r)
        return out

    def _render(self, *_a) -> None:
        rows = self._visible()
        self._shown = rows
        self.table.setRowCount(len(rows))
        red = "#b91c1c" if self.side == "creditors" else "#047857"
        for i, r in enumerate(rows):
            vals = [r["name"], r["phone"], str(r["docs"]), age_text(r["oldest_days"]), money(r["balance"]),
                    money(r["overdue"]) if r["overdue"] > 0 else "-",
                    money(r["opposite"]) if r["opposite"] > 0 else "-"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 4:
                    it.setForeground(QColor(red))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                it.setData(Qt.UserRole, r["party_id"])
                self.table.setItem(i, c, it)

    def _open_row(self, row: int) -> None:
        it = self.table.item(row, 0)
        if it is not None:
            self.open_party.emit(int(it.data(Qt.UserRole)), self.side)

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self._open_row(row)
