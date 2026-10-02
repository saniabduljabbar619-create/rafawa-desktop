# apps/product_management_app/features/debt_center/views/debt_center.py
"""
Debt Center (admin) - a standalone book:

    hub  ──double-click──▶  My Creditors / My Debtors  ──double-click a person──▶  their sheet
                       └──▶  Expenses & Expenditures (two sheets)

It keeps its own records of any number of people and never reads or changes customers, suppliers, sales
or purchases. Look and feel follow alg-erp's Master Supplier sheets.
"""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from database.services.debt_book_service import DebtBookService
from database.services.expense_service import ExpenditureService, ExpenseService
from shared.uix.widgets.cards import money
from apps.product_management_app.features.debt_center.dialogs.person_dialog import PersonDialog
from apps.product_management_app.features.debt_center.sheet_style import GREEN, RED, STYLE, BLUE
from apps.product_management_app.features.debt_center.views.outflow_sheets import OutflowSheetsPage
from apps.product_management_app.features.debt_center.views.people_page import DebtPeoplePage, kpi
from apps.product_management_app.features.debt_center.views.person_sheet import PersonSheetPage


class _Tile(QFrame):
    """A big card that opens on double-click (or its Open button)."""
    opened = Signal()

    def __init__(self, title: str, blurb: str, color: str):
        super().__init__()
        self.setObjectName("dcTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self.setStyleSheet(f"QFrame#dcTile {{ border-top: 5px solid {color}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(4)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size:17px; font-weight:800; color:#1c2333;")
        self.blurb = QLabel(blurb)
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet("color:#4a5266; font-size:12px;")
        self.value = QLabel("-")
        self.value.setStyleSheet(f"font-size:28px; font-weight:800; color:{color};")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#4a5266; font-size:12px;")
        for w in (self.title, self.blurb, self.value, self.sub):
            lay.addWidget(w)
        lay.addStretch(1)
        row = QHBoxLayout()
        hint = QLabel("Double-click to open")
        hint.setStyleSheet("color:#8a8578; font-size:11px;")
        row.addWidget(hint)
        row.addStretch(1)
        self.btn = QPushButton("Open")
        self.btn.setObjectName("dcGhost")
        self.btn.clicked.connect(self.opened.emit)
        row.addWidget(self.btn)
        lay.addLayout(row)

    def mouseDoubleClickEvent(self, e):                 # noqa: N802 (Qt override)
        self.opened.emit()
        super().mouseDoubleClickEvent(e)


class DebtCenterView(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        hub = QWidget()
        hl = QVBoxLayout(hub)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        t = QLabel("Debt Center")
        t.setObjectName("dcTitle")
        s = QLabel("Your own book of who you owe, who owes you, and what the business spends - kept separately "
                   "from customers, suppliers, sales and purchases.")
        s.setObjectName("dcSub")
        s.setWordWrap(True)
        hl.addWidget(t)
        hl.addWidget(s)
        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_net = kpi("Net position", dark=True)
        self.k_cred = kpi("We owe (creditors)")
        self.k_debt = kpi("Owed to us (debtors)")
        self.k_month = kpi("Spent this month")
        for k in (self.k_net, self.k_cred, self.k_debt, self.k_month):
            kp.addWidget(k, 1)
        hl.addLayout(kp)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.tile_cred = _Tile("My Creditors", "People and businesses we owe money to. Add as many as you like.", RED)
        self.tile_debt = _Tile("My Debtors", "People and businesses who owe us money. Add as many as you like.", GREEN)
        self.tile_exp = _Tile("Expenses & Expenditures", "Running costs and larger outlays, on two separate sheets.", BLUE)
        for tl in (self.tile_cred, self.tile_debt, self.tile_exp):
            tiles.addWidget(tl, 1)
        hl.addLayout(tiles)
        hl.addStretch(1)
        self.stack.addWidget(hub)

        self.creditors = DebtPeoplePage("creditor")
        self.debtors = DebtPeoplePage("debtor")
        self.outflows = OutflowSheetsPage()
        for p in (self.creditors, self.debtors, self.outflows):
            self.stack.addWidget(p)
        self.sheet = None
        self._return_kind = "creditor"

        self.tile_cred.opened.connect(lambda: self.open_list("creditor"))
        self.tile_debt.opened.connect(lambda: self.open_list("debtor"))
        self.tile_exp.opened.connect(self.open_outflows)
        for lst in (self.creditors, self.debtors):
            lst.back.connect(self.show_hub)
            lst.open_person.connect(self.open_sheet)
            lst.changed.connect(self.refresh_hub)
        self.outflows.back.connect(self.show_hub)
        self.outflows.changed.connect(self.refresh_hub)
        self.refresh_hub()

    def refresh_hub(self) -> None:
        o = DebtBookService.overview()
        c, d = o["creditor"], o["debtor"]
        self.k_cred.val.setText(money(c["balance"]))
        self.k_cred.cap.setText(f"We owe (creditors)  -  {c['owing']} owing")
        self.k_debt.val.setText(money(d["balance"]))
        self.k_debt.cap.setText(f"Owed to us (debtors)  -  {d['owing']} owing")
        self.k_net.val.setText(money(abs(o["net"])))
        self.k_net.cap.setText("Net: owed to us on balance" if o["net"] > 0 else
                               "Net: we owe on balance" if o["net"] < 0 else "Net position: all square")
        em, xm = ExpenseService.summary()["month"], ExpenditureService.summary()["month"]
        self.k_month.val.setText(money(em + xm))
        self.k_month.cap.setText(f"expenses {money(em)}  |  expenditures {money(xm)}")
        self.tile_cred.value.setText(money(c["balance"]))
        self.tile_cred.sub.setText(f"{c['people']} people  -  {c['owing']} still owed")
        self.tile_debt.value.setText(money(d["balance"]))
        self.tile_debt.sub.setText(f"{d['people']} people  -  {d['owing']} still owing")
        self.tile_exp.value.setText(money(em + xm))
        self.tile_exp.sub.setText("spent this month")

    def show_hub(self) -> None:
        self._drop_sheet()
        self.refresh_hub()
        self.stack.setCurrentIndex(0)

    def open_list(self, kind: str) -> None:
        self._drop_sheet()
        page = self.creditors if kind == "creditor" else self.debtors
        page.reload()
        self.stack.setCurrentWidget(page)

    def open_outflows(self) -> None:
        self.outflows.reload()
        self.stack.setCurrentWidget(self.outflows)

    def open_sheet(self, person_id: int, kind: str) -> None:
        self._drop_sheet()
        self._return_kind = kind
        self.sheet = PersonSheetPage(person_id, kind)
        self.sheet.back.connect(self._close_sheet)
        self.sheet.changed.connect(self.refresh_hub)
        self.sheet.edit_person.connect(self._edit_person)
        self.stack.addWidget(self.sheet)
        self.stack.setCurrentWidget(self.sheet)

    def _edit_person(self) -> None:
        if self.sheet is None:
            return
        person = self.sheet.data["person"]
        if PersonDialog(self._return_kind, self, person=person).exec_():
            self.sheet.reload()

    def _close_sheet(self) -> None:
        kind = self._return_kind
        self.refresh_hub()
        self.open_list(kind)

    def _drop_sheet(self) -> None:
        if self.sheet is not None:
            self.stack.removeWidget(self.sheet)
            self.sheet.deleteLater()
            self.sheet = None
