# apps/product_management_app/features/debt_center/views/debt_center.py
"""
Debt Center (admin): My Creditors, My Debtors and Expenses behind one screen.

    hub  ──double-click──▶  creditors / debtors list ──double-click──▶  party worksheet (pay / receive)
                       └──▶  expenses
"""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from database.services.debt_center_service import DebtCenterService
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, money
from apps.product_management_app.features.debt_center.views.expenses_page import ExpensesPage
from apps.product_management_app.features.debt_center.views.party_list import DebtPartyListPage
from apps.product_management_app.features.debt_center.views.worksheet import DebtWorksheetPage


class _Tile(QFrame):
    """A big card that opens on double-click (or its Open button)."""
    opened = Signal()

    def __init__(self, title: str, blurb: str, tone: str):
        super().__init__()
        self.setObjectName("DebtTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame#DebtTile { background:#ffffff; border:1px solid #e5e7eb; border-radius:14px; "
            f"border-top:5px solid {tone}; }}"
            "QFrame#DebtTile:hover { border-color:#9ca3af; background:#fafafa; }"
            "QFrame#DebtTile QLabel { background:transparent; border:none; }")
        self.setMinimumHeight(190)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(4)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size:17px; font-weight:700; color:#111827;")
        self.blurb = QLabel(blurb)
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet("color:#6b7280; font-size:12px;")
        self.value = QLabel("-")
        self.value.setStyleSheet(f"font-size:28px; font-weight:800; color:{tone};")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#6b7280; font-size:12px;")
        self.btn = QPushButton("Open")
        self.btn.setProperty("role", "plain")
        self.btn.clicked.connect(self.opened.emit)
        for w in (self.title, self.blurb, self.value, self.sub):
            lay.addWidget(w)
        lay.addStretch(1)
        row = QHBoxLayout()
        hint = QLabel("Double-click to open")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(self.btn)
        lay.addLayout(row)

    def mouseDoubleClickEvent(self, e):                 # noqa: N802 (Qt override)
        self.opened.emit()
        super().mouseDoubleClickEvent(e)


class DebtCenterView(QWidget):
    HUB, CREDITORS, DEBTORS, EXPENSES, WORKSHEET = range(5)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # hub
        hub = QWidget()
        hub_holder = QVBoxLayout(hub)
        page = QWidget()
        hub_lay = make_scrollable_page(page, margins=(0, 0, 0, 0))
        hub_holder.setContentsMargins(0, 0, 0, 0)
        hub_holder.addWidget(page)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("Debt Center")
        t.setObjectName("Title")
        s = QLabel("Who we owe, who owes us, and what the business spends")
        s.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(t)
        titles.addWidget(s)
        hub_lay.addLayout(titles)
        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_payable = KpiCard("We owe (payables)", "0.00", "", "red")
        self.kpi_receivable = KpiCard("Owed to us (receivables)", "0.00", "", "green")
        self.kpi_net = KpiCard("Net position", "0.00", "", "blue")
        self.kpi_overdue = KpiCard("Overdue both ways", "0.00", "older than 30 days", "amber")
        for k in (self.kpi_payable, self.kpi_receivable, self.kpi_net, self.kpi_overdue):
            kp.addWidget(k, 1)
        hub_lay.addLayout(kp)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.tile_cred = _Tile("My Creditors", "Suppliers and parties we still owe for purchases.", "#b91c1c")
        self.tile_debt = _Tile("My Debtors", "Customers and parties who still owe us for sales.", "#047857")
        self.tile_exp = _Tile("Expenses", "Rent, wages, fuel, transport and every other business cost.", "#1d4ed8")
        for tl in (self.tile_cred, self.tile_debt, self.tile_exp):
            tiles.addWidget(tl, 1)
        hub_lay.addLayout(tiles)
        hub_lay.addStretch(1)
        self.stack.addWidget(hub)

        self.creditors = DebtPartyListPage("creditors")
        self.debtors = DebtPartyListPage("debtors")
        self.expenses = ExpensesPage()
        self.stack.addWidget(self.creditors)
        self.stack.addWidget(self.debtors)
        self.stack.addWidget(self.expenses)
        self.worksheet = None
        self._return_to = self.HUB

        self.tile_cred.opened.connect(lambda: self.open_list("creditors"))
        self.tile_debt.opened.connect(lambda: self.open_list("debtors"))
        self.tile_exp.opened.connect(self.open_expenses)
        for lst in (self.creditors, self.debtors):
            lst.back.connect(self.show_hub)
            lst.open_party.connect(self.open_worksheet)
        self.expenses.back.connect(self.show_hub)
        self.expenses.changed.connect(self.refresh_hub)
        self.refresh_hub()

    def refresh_hub(self) -> None:
        o = DebtCenterService.overview()
        self.kpi_payable.set(money(o["payable"]), f"{o['creditors']} creditor(s)")
        self.kpi_receivable.set(money(o["receivable"]), f"{o['debtors']} debtor(s)")
        self.kpi_net.set(money(abs(o["net"])), "owed to us on balance" if o["net"] > 0 else
                         "we owe on balance" if o["net"] < 0 else "all square")
        self.kpi_overdue.set(money(o["payable_overdue"] + o["receivable_overdue"]),
                             f"we owe {money(o['payable_overdue'])}  |  owed us {money(o['receivable_overdue'])}")
        self.tile_cred.value.setText(money(o["payable"]))
        self.tile_cred.sub.setText(f"{o['creditors']} creditor(s)  -  {money(o['payable_overdue'])} overdue")
        self.tile_debt.value.setText(money(o["receivable"]))
        self.tile_debt.sub.setText(f"{o['debtors']} debtor(s)  -  {money(o['receivable_overdue'])} overdue")
        from database.services.expense_service import ExpenseService
        sm = ExpenseService.summary()
        self.tile_exp.value.setText(money(sm["month"]))
        self.tile_exp.sub.setText("spent this month")

    def show_hub(self) -> None:
        self._drop_worksheet()
        self.refresh_hub()
        self.stack.setCurrentIndex(self.HUB)

    def open_list(self, side: str) -> None:
        page = self.creditors if side == "creditors" else self.debtors
        page.reload()
        self.stack.setCurrentWidget(page)

    def open_expenses(self) -> None:
        self.expenses.reload()
        self.stack.setCurrentWidget(self.expenses)

    def open_worksheet(self, party_id: int, side: str) -> None:
        self._drop_worksheet()
        self._return_to = self.CREDITORS if side == "creditors" else self.DEBTORS
        self.worksheet = DebtWorksheetPage(party_id, side)
        self.worksheet.back.connect(self._close_worksheet)
        self.stack.addWidget(self.worksheet)
        self.stack.setCurrentWidget(self.worksheet)

    def _close_worksheet(self) -> None:
        side = "creditors" if self._return_to == self.CREDITORS else "debtors"
        self._drop_worksheet()
        self.refresh_hub()
        self.open_list(side)

    def _drop_worksheet(self) -> None:
        if self.worksheet is not None:
            self.stack.removeWidget(self.worksheet)
            self.worksheet.deleteLater()
            self.worksheet = None
