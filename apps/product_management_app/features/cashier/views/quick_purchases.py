# apps/product_management_app/features/cashier/views/quick_purchases.py
"""Quick Purchases: every shortfall being sourced - draft, pending (with the supplier), completed or cancelled."""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.quick_purchase_order_service import QuickPurchaseOrderService as Q
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.cashier.dialogs.quick_purchase_order_dialog import (
    QuickPurchaseOrderDialog, RouteDialog,
)

FILTERS = [("all", "All"), ("draft", "Draft"), ("pending", "Pending"), ("completed", "Completed"),
           ("cancelled", "Cancelled")]
STATE_TEXT = {"open": "waiting", "received": "received", "dropped": "given up"}
COLS = ["No.", "Status", "For", "Products", "Supplier(s)", "Total", "Created"]


class QuickPurchasesView(QWidget):
    def __init__(self):
        super().__init__()
        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Quick Purchases")
        title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        root.addLayout(titles)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_draft = KpiCard("Draft", "0", "planned, not sent", "amber")
        self.kpi_pending = KpiCard("Pending", "0", "waiting for the supplier", "blue")
        self.kpi_done = KpiCard("Completed", "0", "received into stock", "green")
        for k in (self.kpi_draft, self.kpi_pending, self.kpi_done):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.filter_seg = Segmented(FILTERS)
        bar.addWidget(self.filter_seg)
        bar.addStretch(1)
        self.btn_new = QPushButton("+ New Quick Purchase")
        self.btn_new.setObjectName("PrimaryButton")
        self.btn_refresh = QPushButton("Refresh")
        bar.addWidget(self.btn_new)
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        act = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_send = QPushButton("Send to supplier")
        self.btn_receive = QPushButton("Goods received")
        self.btn_receive.setObjectName("PrimaryButton")
        self.btn_route = QPushButton("Route to another supplier")
        self.btn_stock = QPushButton("Sell what's in stock")
        self.btn_cancel = QPushButton("Cancel order")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setProperty("role", "danger")
        for b in (self.btn_edit, self.btn_send, self.btn_receive, self.btn_route, self.btn_stock, self.btn_cancel,
                  self.btn_delete):
            act.addWidget(b)
        act.addStretch(1)
        root.addLayout(act)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 2)
        self.detail = QTableWidget(0, 7)
        self.detail.setHorizontalHeaderLabels(["Product", "Quantity", "Cost each", "Amount", "Asked from", "Line",
                                               "Routing history"])
        self.detail.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.detail.setEditTriggers(QTableWidget.NoEditTriggers)
        self.detail.setSelectionBehavior(QTableWidget.SelectRows)
        self.detail.verticalHeader().setVisible(False)
        root.addWidget(self.detail, 2)

        self.filter_seg.changed.connect(lambda _k: self.refresh())
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_new.clicked.connect(self.new)
        self.btn_edit.clicked.connect(self.edit)
        self.btn_send.clicked.connect(self.send)
        self.btn_receive.clicked.connect(self.receive)
        self.btn_route.clicked.connect(self.route)
        self.btn_stock.clicked.connect(self.sell_available)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_delete.clicked.connect(self.delete)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda *_a: self.edit())
        self._rows = {}
        self.refresh()

    # ------------------------------------------------------------------ data
    def refresh(self):
        keep = self._current_id()
        try:
            allrows = Q.list_orders()
        except Exception as e:
            QMessageBox.warning(self, "Quick Purchases", str(e))
            return
        n = lambda s: sum(1 for o in allrows if o["status"] == s)           # noqa: E731
        self.kpi_draft.set(str(n("draft")))
        self.kpi_pending.set(str(n("pending")))
        self.kpi_done.set(str(n("completed")))
        key = self.filter_seg.key()
        rows = allrows if key == "all" else [o for o in allrows if o["status"] == key]
        self.subtitle.setText(f"{len(rows)} of {len(allrows)} quick purchases")
        self._rows = {int(o["id"]): o for o in rows}
        self.table.setRowCount(len(rows))
        for i, o in enumerate(rows):
            live = [ln for ln in o["lines"] if ln["state"] != "dropped"]
            suppliers = sorted({ln["supplier"] for ln in live if ln["supplier"]})
            vals = [o["order_no"], o["status"].title(), o["customer_name"] or "-",
                    ", ".join(ln["product"] for ln in live) or "-", ", ".join(suppliers) or "-",
                    f"{o['total']:,.2f}", o["created_at"].strftime("%d/%m/%Y %H:%M") if o["created_at"] else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, int(o["id"]))
                if c == 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
            if keep == int(o["id"]):
                self.table.selectRow(i)
        self._selection()

    def _current_id(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return int(it.data(Qt.UserRole)) if it else None

    def _order(self):
        oid = self._current_id()
        return (oid, self._rows.get(oid)) if oid else (None, None)

    def _selection(self):
        _oid, o = self._order()
        st = o["status"] if o else ""
        waiting = bool(o) and any(ln["state"] == "open" for ln in o["lines"])
        got = bool(o) and any(ln["state"] == "received" for ln in o["lines"])
        self.btn_edit.setEnabled(st == "draft")
        self.btn_send.setEnabled(st == "draft")
        self.btn_receive.setEnabled(st == "pending" and waiting)
        self.btn_route.setEnabled(st == "pending" and waiting)
        self.btn_stock.setEnabled(st in ("draft", "pending") and waiting)
        self.btn_cancel.setEnabled(st in ("draft", "pending") and not got)
        self.btn_delete.setEnabled(bool(o) and st != "completed" and not got)
        self.detail.setRowCount(0)
        if not o:
            return
        self.detail.setRowCount(len(o["lines"]))
        for i, ln in enumerate(o["lines"]):
            hist = " > ".join(f"{r['supplier']} ({r['outcome']})" for r in ln["routes"])
            vals = [ln["product"], f"{ln['qty']:g} {ln['unit']}", f"{ln['unit_cost']:,.2f}", f"{ln['amount']:,.2f}",
                    ln["supplier"] or "-", STATE_TEXT.get(ln["state"], ln["state"]), hist]
            for c, v in enumerate(vals):
                self.detail.setItem(i, c, QTableWidgetItem(v))

    # ------------------------------------------------------------------ actions
    def _run(self, fn, title):
        try:
            return fn()
        except Exception as e:
            QMessageBox.warning(self, title, str(e))
            return None
        finally:
            self.refresh()

    def new(self):
        QuickPurchaseOrderDialog(self).exec_()
        self.refresh()

    def edit(self):
        oid, o = self._order()
        if not o or o["status"] != "draft":
            QMessageBox.information(self, "Edit", "Select a draft to edit - only drafts can be changed.")
            return
        QuickPurchaseOrderDialog(self, oid).exec_()
        self.refresh()

    def send(self):
        oid, o = self._order()
        if o:
            self._run(lambda: Q.send(oid), "Send")

    def receive(self):
        oid, o = self._order()
        if not o:
            return
        waiting = [ln for ln in o["lines"] if ln["state"] == "open"]
        ok = QMessageBox.question(
            self, "Goods received",
            f"Record these as received and bought now?\n\n" + "\n".join(
                f"  {ln['product']}: {ln['qty']:g} {ln['unit']} from {ln['supplier']} @ {ln['unit_cost']:,.2f}"
                for ln in waiting) + "\n\nStock goes up and each supplier is owed for their goods.",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        res = self._run(lambda: Q.receive(oid), "Goods received")
        if res and res["errors"]:
            QMessageBox.warning(self, "Goods received", "Some lines could not be bought:\n" + "\n".join(res["errors"]))

    def route(self):
        oid, o = self._order()
        if not o:
            return
        waiting = [ln for ln in o["lines"] if ln["state"] == "open"]
        ids = self._selected_lines(o, waiting)
        if ids is None:
            return
        chosen = [ln for ln in waiting if ln["id"] in ids]
        dlg = RouteDialog(self, [ln["product"] for ln in chosen], chosen[0]["supplier"],
                          chosen[0]["supplier_id"] if len({ln["supplier_id"] for ln in chosen}) == 1 else None)
        if dlg.exec_() != dlg.Accepted:
            return
        v = dlg.values()
        self._run(lambda: Q.route(oid, ids, v["supplier_id"], v["note"], v["unit_cost"]), "Route")

    def _selected_lines(self, o, waiting):
        """The detail rows the user highlighted (only those still waiting); all waiting lines if none."""
        rows = {i.row() for i in self.detail.selectedItems()}
        picked = [o["lines"][r]["id"] for r in rows if r < len(o["lines"]) and o["lines"][r]["state"] == "open"]
        return picked or [ln["id"] for ln in waiting]

    def sell_available(self):
        oid, o = self._order()
        if not o:
            return
        ok = QMessageBox.question(
            self, "Sell what's in stock",
            "Give up on the goods that have not arrived and carry on with what is already in stock?\n\n"
            "Nothing is bought for the lines still waiting.", QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        res = self._run(lambda: Q.sell_available(oid), "Sell available")
        if res:
            QMessageBox.information(self, "In stock now", res["in_stock"] or "Nothing further is needed.")

    def cancel(self):
        oid, o = self._order()
        if not o:
            return
        ok = QMessageBox.question(self, "Cancel", f"Cancel {o['order_no']}? Nothing has been bought, so stock and "
                                  "the books stay as they are.", QMessageBox.Yes | QMessageBox.No)
        if ok == QMessageBox.Yes:
            self._run(lambda: Q.cancel(oid, "Cancelled"), "Cancel")

    def delete(self):
        oid, o = self._order()
        if not o:
            return
        ok = QMessageBox.question(self, "Delete", f"Delete {o['order_no']} completely? This cannot be undone.",
                                  QMessageBox.Yes | QMessageBox.No)
        if ok == QMessageBox.Yes:
            self._run(lambda: Q.delete(oid), "Delete")
