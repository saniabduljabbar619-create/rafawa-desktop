# apps/product_management_app/features/cashier/views/quick_purchases.py
"""Quick Purchases: every shortfall being sourced - draft, pending (with the supplier), completed or cancelled."""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.models import Party
from database.services.quick_purchase_order_service import QuickPurchaseOrderService as Q
from database.session import get_session
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.cashier.dialogs.sale_checkout_dialog import SaleCheckoutDialog
from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog
from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import SalePaymentDialog
from apps.product_management_app.features.cashier.dialogs.quick_purchase_order_dialog import (
    LineEditDialog, QuickPurchaseOrderDialog, RouteDialog,
)

FILTERS = [("all", "All"), ("draft", "Draft"), ("pending", "Pending"), ("completed", "Completed"),
           ("cancelled", "Cancelled")]
STATE_TEXT = {"open": "waiting", "received": "received", "dropped": "given up"}
NEEDS_SUPPLIER = "needs a supplier"
COLS = ["No.", "Status", "For", "Products", "Supplier(s)", "Total", "Created"]


class QuickPurchasesView(QWidget):
    def __init__(self, load_callback=None):
        """load_callback(order): given when opened from the Sales Console - brings the order's cart back."""
        super().__init__()
        self._load_callback = load_callback
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
        self.btn_line = QPushButton("Edit line")
        self.btn_drop = QPushButton("Drop line")
        self.btn_confirm = QPushButton("Confirm  \u2192")
        self.btn_confirm.setObjectName("PrimaryButton")
        self.btn_stock = QPushButton("Sell what's in stock")
        self.btn_cancel = QPushButton("Cancel order")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setProperty("role", "danger")
        self.btn_load = QPushButton("Load cart into Sales")
        self.btn_load.setObjectName("PrimaryButton")
        self.btn_load.setVisible(load_callback is not None)
        for b in (self.btn_edit, self.btn_send, self.btn_line, self.btn_drop, self.btn_route, self.btn_receive,
                  self.btn_stock, self.btn_cancel, self.btn_delete, self.btn_load):
            act.addWidget(b)
        act.addStretch(1)
        act.addWidget(self.btn_confirm)
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
        self.btn_load.clicked.connect(self.load_cart)
        self.btn_line.clicked.connect(self.edit_line)
        self.btn_drop.clicked.connect(self.drop_line)
        self.btn_confirm.clicked.connect(self.confirm)
        self.detail.cellDoubleClicked.connect(lambda *_a: self.edit_line())
        self.detail.itemSelectionChanged.connect(self._line_buttons)
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
            vals = [o["order_no"], o["status"].title() + (" - sold" if o["sale_id"] else ""),
                    o["customer_name"] or "-",
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
        self.btn_edit.setEnabled(st in ("draft", "pending"))
        self.btn_send.setEnabled(st == "draft")
        self.btn_receive.setEnabled(st == "pending" and waiting)
        self.btn_route.setEnabled(st == "pending" and waiting)
        self.btn_stock.setEnabled(st in ("draft", "pending") and waiting)
        self.btn_cancel.setEnabled(st in ("draft", "pending") and not got)
        self.btn_delete.setEnabled(bool(o) and st != "completed" and not got)
        live = bool(o and o["cart"]) and st in ("draft", "pending") and any(c.get("qp") for c in o["cart"])
        done = bool(o and o["cart"]) and st in ("completed", "cancelled") and not o["sale_id"]
        self.btn_load.setEnabled(live or done)
        self.btn_confirm.setEnabled(bool(o) and not o["sale_id"] and (
            st in ("draft", "pending") or (st == "completed" and bool(o["cart"]))))
        self.detail.setRowCount(0)
        if not o:
            self._line_buttons()
            return
        self.detail.setRowCount(len(o["lines"]))
        for i, ln in enumerate(o["lines"]):
            hist = " > ".join(f"{r['supplier']} ({r['outcome']})" for r in ln["routes"])
            waiting = ln["state"] == "open"
            vals = [ln["product"], f"{ln['qty']:g} {ln['unit']}", f"{ln['unit_cost']:,.2f}", f"{ln['amount']:,.2f}",
                    ln["supplier"] or "-", NEEDS_SUPPLIER if waiting and not ln["assigned"]
                    else STATE_TEXT.get(ln["state"], ln["state"]), hist]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if waiting and not ln["assigned"]:
                    it.setBackground(QColor("#fff7ed"))
                self.detail.setItem(i, c, it)
        self._line_buttons()

    def _line(self):
        """(order id, order, the highlighted line if it is still waiting for goods)."""
        oid, o = self._order()
        r = self.detail.currentRow()
        ln = o["lines"][r] if o and 0 <= r < len(o["lines"]) else None
        return oid, o, (ln if ln and ln["state"] == "open" else None)

    def _line_buttons(self):
        _oid, o, ln = self._line()
        ok = bool(o and ln and o["status"] in ("draft", "pending"))
        self.btn_line.setEnabled(ok)
        self.btn_drop.setEnabled(ok)

    # ------------------------------------------------------------------ actions
    def edit_line(self):
        oid, o, ln = self._line()
        if not ln or o["status"] not in ("draft", "pending"):
            QMessageBox.information(self, "Edit line", "Select a line that is still waiting for goods.")
            return
        dlg = LineEditDialog(self, ln)
        if dlg.exec_() != dlg.Accepted:
            return
        v = dlg.values()
        self._run(lambda: Q.edit_line(oid, ln["id"], qty=v["qty"], unit_cost=v["unit_cost"],
                                      supplier_id=v["supplier_id"] if v["supplier_id"] is not None else 0,
                                      note=v["note"]), "Edit line")

    def drop_line(self):
        oid, o, ln = self._line()
        if not ln:
            QMessageBox.information(self, "Drop line", "Select a line that is still waiting for goods.")
            return
        ok = QMessageBox.question(self, "Drop line", f"Give up {ln['qty']:g} {ln['unit']} of {ln['product']}? "
                                  "Nothing is bought for it.", QMessageBox.Yes | QMessageBox.No)
        if ok == QMessageBox.Yes:
            self._run(lambda: Q.drop_line(oid, ln["id"], "Given up"), "Drop line")

    def confirm(self):
        """
        Finish the order through the SAME checkout the Sales Console uses: the Checkout dialog (selling price,
        discount, paid now), then the Sale Payment screen for the customer, then the supplier payment screen.
        """
        from apps.product_management_app.app_state import state
        oid, o = self._order()
        if not o:
            return
        try:
            plan = Q.plan_confirm(oid)
        except Exception as e:
            QMessageBox.warning(self, "Confirm", str(e))
            return
        user_id = state.require_user_id()
        buyer, walkin, label = o["buyer_party_id"], "", o["customer_name"] or "Walk-in"
        if plan["has_sale"] and not buyer:
            walkin = o["customer_name"] or "Walk-in Customer"
            try:
                buyer = state.get_or_lazy_create_walkin(walkin, "")
            except Exception as e:
                QMessageBox.warning(self, "Confirm", f"Could not file the sale under {walkin}:\n{e}")
                return
        prices, discounts, paid, method, note = {}, {}, 0.0, "cash", ""
        if plan["has_sale"]:
            lines = []
            for sl in plan["sale_lines"]:
                if sl["sell_qty"] <= 0:
                    continue
                fert = str(sl["product_type"]).lower().startswith("fert")
                bags = int(sl["sell_qty"]) if fert else max(1, round(sl["bags"] * sl["sell_qty"] / sl["wanted"]))
                lines.append({"product_id": sl["product_id"], "product_type": sl["product_type"], "name": sl["name"],
                              "rate": sl["price"], "bags": bags, "kg": 0.0 if fert else sl["sell_qty"],
                              "subtotal": sl["price"] * sl["sell_qty"]})
            dlg = SaleCheckoutDialog(self, customer_id=buyer if buyer else None, customer_label=label,
                                     walkin_name=walkin, total=sum(x["subtotal"] for x in lines), lines=lines)
            if dlg.exec_() != dlg.Accepted:
                return
            pay = dlg.payload()
            prices, discounts = pay["rate_overrides"], pay["discount_overrides"]
            paid, method, note = float(pay.get("amount_paid") or 0.0), pay.get("method") or "cash", pay.get("note", "")
        res = self._run(lambda: Q.confirm(
            oid, prices, customer_paid=paid, customer_method=method, buyer_party_id=buyer, walkin_name=walkin,
            discounts=discounts, note=note), "Confirm")
        if not res:
            return
        msg = [f"Quick Purchase {o['order_no']} completed."]
        if res["sale_id"]:
            msg.append(f"Sale {res['reference']}: {res['sale_total']:,.2f} - paid {res['customer_paid']:,.2f}, "
                       f"still owes {res['customer_owes']:,.2f}.")
        msg += res["warnings"]
        QMessageBox.information(self, "Quick Purchase completed", "\n".join(msg))

        if res["sale_id"] and buyer:                      # customer: exactly the console's payment steps
            with get_session() as s:
                party = s.get(Party, int(buyer))
            if res["customer_owes"] > 0.005:
                prompt_and_apply_advance(self, party_id=int(buyer), entity_type="sale", entity_id=int(res["sale_id"]),
                                         balance=float(res["customer_owes"]), user_id=user_id,
                                         party_name=(party.display_name if party else ""))
            SalePaymentDialog(party=party, user_id=user_id, parent=self, print_slip=False).exec_()
        if res["owed_to_suppliers"] > 0.005 and QMessageBox.question(          # suppliers: pay now, or leave owed
                self, "Suppliers", f"We owe the suppliers {res['owed_to_suppliers']:,.2f}. Pay them now?\n\n"
                "(Anything left unpaid stays in the Payments center.)",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            with get_session() as s:
                for sid in sorted({x["supplier_id"] for x in res["supplier_purchases"]}):
                    PurchasePaymentDialog(party=s.get(Party, int(sid)), user_id=user_id, parent=self,
                                          print_slip=False).exec_()
        self.refresh()

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
        if not o or o["status"] not in ("draft", "pending"):
            QMessageBox.information(self, "Edit", "Select a draft or pending Quick Purchase to edit.")
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

    def load_cart(self):
        _oid, o = self._order()
        if o and self._load_callback:
            self._load_callback(o)

    def delete(self):
        oid, o = self._order()
        if not o:
            return
        ok = QMessageBox.question(self, "Delete", f"Delete {o['order_no']} completely? This cannot be undone.",
                                  QMessageBox.Yes | QMessageBox.No)
        if ok == QMessageBox.Yes:
            self._run(lambda: Q.delete(oid), "Delete")
