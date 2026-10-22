# apps/product_management_app/features/orders/views/partner_profile_page.py
"""A company's / supplier's profile: all their orders in one sheet-style list, and where a new order starts."""
from __future__ import annotations

from datetime import date

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QTabBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.order_money_service import OrderMoneyService
from database.services.order_sheet_service import OrderSheetService
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import avatar, chip, money
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid
from apps.product_management_app.features.orders.views.order_sheet_page import (
    EXPENSE_CHARGE, METHOD_OPTIONS, MONEY_WAYS, STATUS_TEXT, _num,
)

COLS = ["Order", "Status", "Created", "Dispatched", "Expected", "Value", "Payment", "Still to settle"]
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
        self.btn_back = QPushButton("<  Back to " + ("companies" if kind == "sales" else "suppliers"))
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
        self.k_out = kpi("They still owe us (orders)" if kind == "sales" else "We still owe them (orders)", dark=True)
        self.k_orders = kpi("Total orders")
        self.k_active = kpi("Open orders (not yet delivered)")
        self.k_value = kpi("Value of delivered orders")
        for k in (self.k_out, self.k_orders, self.k_active, self.k_value):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        # where we stand beyond the orders themselves (positive net: they owe us; negative: we owe them)
        st = QHBoxLayout()
        st.setSpacing(10)
        self.k_money = kpi("Other money received")
        self.k_recover = kpi("Expenses to get back")
        self.k_net = kpi("Overall position", dark=True)
        for k in (self.k_money, self.k_recover, self.k_net):
            st.addWidget(k, 1)
        root.addLayout(st)
        how = QLabel("Overall position = orders + other money + expenses we are owed. It tells you in one line who owes whom.")
        how.setObjectName("msHint")
        how.setWordWrap(True)
        root.addWidget(how)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setVisible(False)
        root.addWidget(self.msg)

        self.tabs = QTabBar()
        self.tabs.setObjectName("msSheets")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        for text in ("Orders", "Other money", "Expenses"):
            self.tabs.addTab(text)
        root.addWidget(self.tabs)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        orders_page = QWidget()
        ol = QVBoxLayout(orders_page)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        self.slot_money = QWidget()
        self.slot_money.setLayout(QVBoxLayout())
        self.slot_money.layout().setContentsMargins(0, 0, 0, 0)
        self.slot_exp = QWidget()
        self.slot_exp.setLayout(QVBoxLayout())
        self.slot_exp.layout().setContentsMargins(0, 0, 0, 0)
        for pg in (orders_page, self.slot_money, self.slot_exp):
            self.stack.addWidget(pg)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.money_grid = self.exp_grid = None

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
        ol.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.btn_open = QPushButton("Open order")
        self.btn_open.setObjectName("msBig")
        foot.addWidget(self.btn_open)
        self.btn_delete = QPushButton("Delete order")
        self.btn_delete.setProperty("role", "danger")
        self.btn_delete.setVisible(is_admin)
        foot.addWidget(self.btn_delete)
        foot.addStretch(1)
        h = QLabel("Double-click an order to open it.  Only draft or cancelled orders can be deleted.")
        h.setObjectName("msHint")
        foot.addWidget(h)
        ol.addLayout(foot)

        self.btn_new.clicked.connect(self.new_order.emit)
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_row(r))
        self.reload()

    def reload(self) -> None:
        self._rows = OrderSheetService.orders_of(self.kind, self.partner_id)
        done = [r for r in self._rows if r["status"] == "confirmed"]
        self.k_out.val.setText(money(sum(r["balance"] or 0 for r in self._rows)))
        self.k_orders.val.setText(str(len(self._rows)))
        self.k_active.val.setText(str(sum(1 for r in self._rows if r["status"] in ("draft", "in_transit"))))
        self.k_value.val.setText(money(sum(r["final"] or 0 for r in done)))
        self._render_statement()
        self._build_money_tab()
        self._build_expense_tab()
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

    def _delete_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._rows)):
            QMessageBox.information(self, "Delete order", "Select an order first.")
            return
        r = self._rows[row]
        ok = QMessageBox.question(self, "Delete order", f"Delete order {r['order_no']} for good?",
                                  QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            OrderSheetService.delete_order(self.kind, int(r["id"]))
        except Exception as e:
            QMessageBox.warning(self, "Delete order", str(e))
            return
        self.reload()

    # ------------------------------------------------------------------ the money statement
    def say(self, text: str, bad: bool = True) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self.msg.setStyleSheet(f"color:{'#a23b34' if bad else '#3f6b4a'}; font-size:12.5px; font-weight:600;")

    def _render_statement(self) -> None:
        st = OrderMoneyService.statement(self.kind, self.partner_id)
        self.k_money.val.setText(money(st["money_net"]))
        self.k_money.cap.setText("Other money they gave us" if st["money_net"] >= 0
                                 else "Other money we gave them")
        if st["money_net"] < 0:
            self.k_money.val.setText(money(-st["money_net"]))
        self.k_recover.val.setText(money(st["to_recover"]))
        self.k_recover.cap.setText("Expenses to get back" + (f"  (our own cost: {money(st['our_cost'])})"
                                                                      if st["our_cost"] else ""))
        net = st["net"]
        self.k_net.val.setText(money(abs(net)))
        self.k_net.cap.setText("Overall: THEY owe us" if net > 0.005 else
                               ("Overall: WE owe them" if net < -0.005 else "Overall: all square"))
        self.statement = st

    # ------------------------------------------------------------------ money sheet (all orders + on account)
    def _order_choices(self):
        live = [r for r in self._rows if r["status"] != "cancelled"]
        return {r["order_no"]: r["id"] for r in live}

    def _build_money_tab(self) -> None:
        ids = self._order_choices()
        cols = [
            {"key": "date", "label": "Date", "type": "date", "width": 110, "readonly": True},
            {"key": "order", "label": "Order", "type": "choice", "options": ["On account"] + list(ids), "width": 140},
            {"key": "direction", "label": "Direction", "type": "choice", "options": MONEY_WAYS, "width": 150},
            {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 150},
            {"key": "method", "label": "How", "type": "choice", "options": METHOD_OPTIONS, "width": 110},
            {"key": "note", "label": "Note", "type": "text", "width": 280, "stretch": True},
            {"key": "by", "label": "By", "type": "text", "width": 110, "readonly": True},
        ]
        entries = OrderMoneyService.list_money(self.kind, partner_id=self.partner_id)
        rows = []
        for m in entries:
            voided = m["status"] == "voided"
            rows.append({"id": m["id"], "removed": voided, "locked": True,
                         "cells": {"date": m["date"].isoformat() if m["date"] else "",
                                   "order": m["order_no"] or "On account",
                                   "direction": MONEY_WAYS[0] if m["direction"] == "in" else MONEY_WAYS[1],
                                   "amount": m["amount"], "method": m["method"].title(), "note": m["note"],
                                   "by": m["by"]},
                         "computed": {"state": "Applied to an order" if m["status"] == "applied" else ""},
                         "tip": ("Voided: " + m["void_reason"]) if voided else ""})
        live = [m for m in entries if m["status"] != "voided"]
        net = sum(m["amount"] if m["direction"] == "in" else -m["amount"] for m in live)
        grid = SheetGrid(
            columns=cols, computed=[{"key": "state", "label": "Status", "width": 170}], first_edit_key="amount",
            placeholder_key="amount",
            defaults={"date": date.today().isoformat(), "order": "On account", "direction": MONEY_WAYS[0],
                      "method": "Cash"},
            ready=lambda v: "" if (_safe_num(v.get("amount")) or 0) > 0 else "Now type the amount.",
            on_add=lambda v: self._add_money(v, ids), on_edit=None,
            on_remove=lambda mid, reason: OrderMoneyService.void_money(mid, reason), reload=self.reload,
            hint="Money that is NOT an order price: an advance, a loan, a refund. Choose 'On account' or one order. "
                 "Not part of the cash drawer. To cancel a row, right-click it (give a reason).",
            allow_add=True, notify=self.say)
        grid.set_rows(rows, {"amount": f"net {money(net)}"})
        self.btn_apply = QPushButton("Use the selected row as a payment for an order…")
        self.btn_apply.clicked.connect(lambda: self._apply_money(ids))
        self._swap(self.slot_money, self._with_buttons(grid, self.btn_apply))
        self.money_grid = grid

    def _add_money(self, v, ids) -> None:
        way = "in" if (v.get("direction") or MONEY_WAYS[0]) == MONEY_WAYS[0] else "out"
        order = ids.get(v.get("order") or "On account")
        OrderMoneyService.add_money(self.kind, self.partner_id, way, _num(v.get("amount")), order_id=order,
                                    method=(v.get("method") or "Cash").lower(), note=v.get("note", ""))

    def _apply_money(self, ids) -> None:
        tbl = self.money_grid.table
        r = tbl.currentRow()
        if r < 0 and tbl.selectedItems():
            r = tbl.selectedItems()[0].row()
        row = self.money_grid.rows[r] if 0 <= r < len(self.money_grid.rows) else None
        if row is None or row.get("removed"):
            self.say("Click the money row you want to apply first.")
            return
        names = list(ids)
        if not names:
            self.say("There is no order to apply it to.")
            return
        pick, ok = QInputDialog.getItem(self, "Apply to an order", "Count it as a payment on which order?", names, 0,
                                        False)
        if not ok:
            return
        try:
            OrderMoneyService.apply_to_order(row["id"], ids[pick])
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("Done - it now counts as a payment on that order.", bad=False)
        self.reload()

    # ------------------------------------------------------------------ expenses of every order
    def _build_expense_tab(self) -> None:
        cols = [
            {"key": "date", "label": "Date", "type": "date", "width": 110, "readonly": True},
            {"key": "order", "label": "Order", "type": "text", "width": 120, "readonly": True},
            {"key": "category", "label": "What for", "type": "text", "width": 150, "readonly": True},
            {"key": "description", "label": "Details", "type": "text", "width": 240, "readonly": True, "stretch": True},
            {"key": "amount", "label": "Amount (₦)", "type": "money", "width": 140, "readonly": True},
            {"key": "charge", "label": "Charge", "type": "text", "width": 160, "readonly": True},
        ]
        exps = OrderMoneyService.list_expenses(self.kind, partner_id=self.partner_id)
        rows = []
        for e in exps:
            voided = e["status"] == "voided"
            rows.append({"id": e["id"], "removed": voided, "locked": True,
                         "cells": {"date": e["date"].isoformat() if e["date"] else "", "order": e["order_no"],
                                   "category": e["category"], "description": e["description"], "amount": e["amount"],
                                   "charge": EXPENSE_CHARGE[0] if e["recoverable"] else EXPENSE_CHARGE[1]},
                         "computed": {"back": money(e["recovered"]) if e["recoverable"] and not voided else "",
                                      "left": money(e["left"]) if e["recoverable"] and not voided else
                                      ("" if voided else "our cost"),
                                      "state": e["state"].title()},
                         "tip": ("Voided: " + e["void_reason"]) if voided else e["no"]})
        live = [e for e in exps if e["status"] != "voided"]
        totals = {"amount": money(sum(e["amount"] for e in live)), "back": money(sum(e["recovered"] for e in live)),
                  "left": money(sum(e["left"] for e in live))}
        grid = SheetGrid(
            columns=cols, computed=[{"key": "back", "label": "Paid back", "width": 120},
                                    {"key": "left", "label": "Still owed", "width": 120},
                                    {"key": "state", "label": "State", "width": 140}],
            first_edit_key="amount", allow_add=False, on_remove=lambda eid, reason: OrderMoneyService.void_expense(eid, reason),
            reload=self.reload, notify=self.say,
            hint="All expenses on this "+("company" if self.kind == "sales" else "supplier")+"'s orders. To add one, open the order and use its Expenses tab. "
                 "When they pay some back, press the button above - it clears the oldest first.")
        grid.set_rows(rows, totals)
        self.btn_repay = QPushButton("They paid some back…")
        self.btn_repay.clicked.connect(self._repay)
        self._swap(self.slot_exp, self._with_buttons(grid, self.btn_repay))
        self.exp_grid = grid

    def _repay(self) -> None:
        from apps.product_management_app.features.orders.dialogs.repayment_dialog import RepaymentDialog
        owed = self.statement["to_recover"] if getattr(self, "statement", None) else 0.0
        if owed <= 0.005:
            self.say("Nothing is owed back to us - no expense is waiting to be repaid.", bad=False)
            return
        dlg = RepaymentDialog(owed, self, who=self.partner.get("name", "They"))
        if dlg.exec_() != dlg.Accepted or not dlg.values:
            return
        try:
            OrderMoneyService.record_recovery(dlg.values["amount"], kind=self.kind, partner_id=self.partner_id,
                                              method=dlg.values["method"], note=dlg.values["note"])
        except (ValueError, PermissionDenied) as e:
            self.say(str(e))
            return
        self.say("Repayment recorded.", bad=False)
        self.reload()

    # ------------------------------------------------------------------ small helpers
    @staticmethod
    def _with_buttons(grid: SheetGrid, *buttons: QPushButton) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        grid.setMinimumHeight(260)
        lay.addWidget(grid, 1)
        return holder

    @staticmethod
    def _swap(slot: QWidget, widget: QWidget) -> None:
        lay = slot.layout()
        while lay.count():
            old = lay.takeAt(0).widget()
            if old is not None:
                old.hide()
                old.setParent(None)
                old.deleteLater()
        lay.addWidget(widget)


def _safe_num(raw):
    try:
        return _num(raw)
    except ValueError:
        return None
