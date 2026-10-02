# apps/product_management_app/features/debt_center/views/people_page.py
"""My Creditors / My Debtors: everyone in the book with their totals. Double-click opens their sheet."""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.debt_book_service import DebtBookService
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import avatar, money
from apps.product_management_app.features.debt_center.dialogs.person_dialog import PersonDialog
from apps.product_management_app.features.debt_center.sheet_style import FAINT, GREEN, MUTED, RED, STYLE

COLS = ["", "Name", "Phone", "Rows", "Last entry", "Total", "Paid", "Balance"]
TEXT = {
    "creditor": ("My Creditors", "People and businesses we owe money to", "Total we owe", RED),
    "debtor": ("My Debtors", "People and businesses who owe us money", "Total owed to us", GREEN),
}


def kpi(caption: str, dark: bool = False):
    box = QFrame()
    box.setObjectName("msKpiDark" if dark else "msKpi")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 10, 16, 10)
    lay.setSpacing(0)
    val = QLabel("-")
    val.setObjectName("msKpiVal")
    if dark:
        val.setStyleSheet("color:#ffffff; font-size:26px;")
    cap = QLabel(caption)
    cap.setObjectName("msKpiCap")
    lay.addWidget(val)
    lay.addWidget(cap)
    box.val, box.cap = val, cap
    return box


class DebtPeoplePage(QWidget):
    open_person = Signal(int, str)          # person_id, kind
    back = Signal()
    changed = Signal()

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self._rows = []
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        title, sub, cap, _tone = TEXT[kind]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.btn_back = QPushButton("<  Debt Center")
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        t = QLabel(title)
        t.setObjectName("dcTitle")
        s = QLabel(sub + " - recorded here by hand, separate from customers and suppliers.")
        s.setObjectName("dcSub")
        tv.addWidget(t)
        tv.addWidget(s)
        head.addLayout(tv)
        head.addStretch(1)
        self.btn_add = QPushButton("+ Add " + ("creditor" if kind == "creditor" else "debtor"))
        self.btn_add.setObjectName("msBig")
        head.addWidget(self.btn_add)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_balance = kpi(cap, dark=True)
        self.k_people = kpi("People in the list")
        self.k_total = kpi("Total recorded")
        self.k_paid = kpi("Paid so far")
        for k in (self.k_balance, self.k_people, self.k_total, self.k_paid):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("dcSearch")
        self.search.setPlaceholderText("Search name or phone")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search, 1)
        self.btn_archived = QPushButton("Show archived")
        self.btn_archived.setCheckable(True)
        self.btn_archived.setObjectName("dcGhost")
        bar.addWidget(self.btn_archived)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("dcList")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 64)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open sheet")
        self.btn_open.setObjectName("msBig")
        self.btn_edit = QPushButton("Edit details")
        self.btn_edit.setObjectName("dcGhost")
        self.btn_archive = QPushButton("Archive")
        self.btn_archive.setObjectName("dcGhost")
        for b in (self.btn_open, self.btn_edit, self.btn_archive):
            foot.addWidget(b)
        foot.addStretch(1)
        h = QLabel("Double-click a name to open their sheet")
        h.setObjectName("msHint")
        foot.addWidget(h)
        root.addLayout(foot)

        self.btn_add.clicked.connect(self._add)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_archive.clicked.connect(self._archive)
        self.search.textChanged.connect(lambda *_: self.reload())
        self.btn_archived.toggled.connect(lambda *_: self.reload())
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))

    def reload(self) -> None:
        self._rows = DebtBookService.people(self.kind, self.search.text(), self.btn_archived.isChecked())
        live = [r for r in self._rows if not r["archived"]]
        bal = sum(r["balance"] for r in live if r["balance"] > 0)
        self.k_balance.val.setText(money(bal))
        self.k_balance.cap.setText(TEXT[self.kind][2] + f"  ({sum(1 for r in live if r['balance'] > 0.005)} owing)")
        self.k_people.val.setText(str(len(live)))
        self.k_total.val.setText(money(sum(r["owed"] for r in live)))
        self.k_paid.val.setText(money(sum(r["paid"] for r in live)))
        self.table.setRowCount(len(self._rows))
        tone = TEXT[self.kind][3]
        for i, r in enumerate(self._rows):
            self.table.setCellWidget(i, 0, self._avatar(r["name"]))
            last = r["last"].strftime("%d/%m/%Y") if r["last"] else "-"
            vals = [r["name"] + ("  (archived)" if r["archived"] else ""), r["phone"], str(r["entries"]), last,
                    money(r["owed"]), money(r["paid"]), money(r["balance"])]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c >= 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 7:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(tone if r["balance"] > 0.005 else MUTED))
                if r["archived"]:
                    it.setForeground(QColor(FAINT))
                self.table.setItem(i, c, it)

    @staticmethod
    def _avatar(name):
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.addWidget(avatar(name, 34))
        return holder

    def _current(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Debt Center", "Select a person first.")
            return None
        return self._rows[row]

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            self.open_person.emit(self._rows[row]["id"], self.kind)

    def _open_selected(self) -> None:
        r = self._current()
        if r:
            self.open_person.emit(r["id"], self.kind)

    def _add(self) -> None:
        dlg = PersonDialog(self.kind, self)
        if dlg.exec_() == dlg.Accepted:
            self.reload()
            self.changed.emit()
            if dlg.saved_id:
                self.open_person.emit(dlg.saved_id, self.kind)

    def _edit(self) -> None:
        r = self._current()
        if r and PersonDialog(self.kind, self, person=r).exec_():
            self.reload()
            self.changed.emit()

    def _archive(self) -> None:
        r = self._current()
        if r is None:
            return
        try:
            DebtBookService.set_archived(r["id"], not r["archived"])
        except (ValueError, PermissionDenied) as e:
            QMessageBox.warning(self, "Debt Center", str(e))
            return
        self.reload()
        self.changed.emit()


