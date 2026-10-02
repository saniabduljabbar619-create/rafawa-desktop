# apps/product_management_app/features/orders/views/partner_profile_page.py
"""A company's / supplier's profile: all their orders in one sheet-style list, and where a new order starts."""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from database.services.order_sheet_service import OrderSheetService
from shared.uix.widgets.cards import avatar, chip, money
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.orders.views.order_sheet_page import STATUS_TEXT

COLS = ["Order", "Status", "Created", "On the road", "Expected", "Value", "Payment", "Balance"]
_COLOR = {"draft": "#8a8578", "in_transit": "#2f6f9f", "confirmed": "#3f6b4a", "cancelled": "#a23b34"}


def _day(dt) -> str:
    return dt.strftime("%d/%m/%Y") if dt else "-"


class PartnerProfilePage(QWidget):
    back = Signal()
    open_order = Signal(int)
    new_order = Signal()

    def __init__(self, kind: str, partner_id: int, is_admin: bool = True):
        super().__init__()
        self.kind, self.partner_id, self.is_admin = kind, int(partner_id), is_admin
        self._rows = []
        self.partner = OrderSheetService.partner(kind, partner_id) or {"name": "?", "phone": "", "address": "",
                                                                         "contact_person": ""}
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.btn_back = QPushButton("<  " + ("Companies" if kind == "sales" else "Suppliers"))
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        head.addWidget(avatar(self.partner["name"], 44))
        box = QVBoxLayout()
        box.setSpacing(0)
        name = QLabel(self.partner["name"])
        name.setStyleSheet("font-size:19px; font-weight:800; color:#1c2333;")
        bits = [self.partner.get("contact_person") and f"Contact: {self.partner['contact_person']}",
                self.partner.get("phone"), self.partner.get("address")]
        sub = QLabel("  -  ".join(b for b in bits if b) or "No contact details")
        sub.setObjectName("dcSub")
        box.addWidget(name)
        box.addWidget(sub)
        holder = QWidget()
        holder.setLayout(box)
        head.addWidget(holder)
        c = chip("Sales Orders" if kind == "sales" else "Purchase Orders", "blue" if kind == "sales" else "amber")
        c.setFixedHeight(22)
        head.addWidget(c, 0, Qt.AlignVCenter)
        head.addStretch(1)
        self.btn_new = QPushButton("+ New " + ("Sales" if kind == "sales" else "Purchase") + " Order")
        self.btn_new.setObjectName("msBig")
        self.btn_new.setVisible(is_admin)
        head.addWidget(self.btn_new)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_out = kpi("Outstanding" if kind == "sales" else "We still owe", dark=True)
        self.k_orders = kpi("Orders")
        self.k_active = kpi("Active (draft / on the road)")
        self.k_value = kpi("Confirmed value")
        for k in (self.k_out, self.k_orders, self.k_active, self.k_value):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("dcList")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open order")
        self.btn_open.setObjectName("msBig")
        foot.addWidget(self.btn_open)
        foot.addStretch(1)
        h = QLabel("Double-click an order to open its sheet")
        h.setObjectName("msHint")
        foot.addWidget(h)
        root.addLayout(foot)

        self.btn_new.clicked.connect(self.new_order.emit)
        self.btn_open.clicked.connect(self._open_selected)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))
        self.reload()

    def reload(self) -> None:
        self._rows = OrderSheetService.orders_of(self.kind, self.partner_id)
        done = [r for r in self._rows if r["status"] == "confirmed"]
        self.k_out.val.setText(money(sum(r["balance"] or 0 for r in self._rows)))
        self.k_orders.val.setText(str(len(self._rows)))
        self.k_active.val.setText(str(sum(1 for r in self._rows if r["status"] in ("draft", "in_transit"))))
        self.k_value.val.setText(money(sum(r["final"] or 0 for r in done)))
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            pay = (r["payment_status"] or "-").replace("_", " ").title()
            vals = [r["order_no"], STATUS_TEXT.get(r["status"], r["status"]), _day(r["created_at"]),
                    _day(r["dispatched_at"]), _day(r["expected"]),
                    money(r["final"]) if r["final"] is not None else "-", pay,
                    money(r["balance"]) if r["balance"] is not None else "-"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if c >= 5 and c != 6:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 1:
                    it.setForeground(QColor(_COLOR.get(r["status"], "#1c2333")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, c, it)

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            self.open_order.emit(self._rows[row]["id"])

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Orders", "Select an order first.")
            return
        self._open_row(row)


