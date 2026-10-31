# apps/product_management_app/features/weigher/views/weigh_tickets_admin.py
"""Weigh Tickets - the queue admin/cashier work from: Open Intake → verify → price → finalize → pay."""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from database.services.weigh_service import WeighService
from shared.uix.layout.flow import FlowLayout
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
from apps.product_management_app.features.weigher.views.ticket_table import (
    TicketFilterBar, edit_finalized_prices, fill_ticket_table, make_ticket_table, selected_ticket_id,
)

WHO = [("customers", "Customers and suppliers"), ("walkins", "Walk-ins")]
PAY_METHODS = ["cash", "transfer", "pos", "mobile"]
FILTERS = [("submitted", "Awaiting intake"), ("finalized", "Finalized"), ("open", "Being weighed"),
           ("cancelled", "Cancelled"), ("all", "All")]


class WeighTicketsAdminView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = mode
        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Weigh Tickets")
        title.setObjectName("Title")
        self.subtitle = QLabel("Weighed deliveries waiting for you to verify, price and pay")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        self.btn_open = QPushButton("Open Intake")
        self.btn_open.setObjectName("PrimaryButton")
        self.btn_open.setMinimumHeight(36)
        head.addWidget(self.btn_open)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_wait = KpiCard("Awaiting intake", "0", "submitted by weighers", "blue")
        self.kpi_open = KpiCard("Being weighed", "0", "not yet submitted", "amber")
        self.kpi_done = KpiCard("Finalized", "0", "turned into purchases", "green")
        for k in (self.kpi_wait, self.kpi_open, self.kpi_done):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = FlowLayout()
        self.filter_seg = Segmented(FILTERS)
        bar.addWidget(self.filter_seg)
        bar.addStretch(1)
        self.btn_print_purchase = QPushButton("Print Purchase Receipt")
        self.btn_print = QPushButton("Print Weighing Receipt")
        self.btn_print_value = QPushButton("Print Value Receipt")
        self.btn_print_pay = QPushButton("Print Payment Receipt")
        self.btn_move = QPushButton("Change customer")
        self.btn_edit_prices = QPushButton("Edit prices")
        self.btn_edit_prices.setToolTip("A finalized weighing: change the price of each product (or right-click the row)")
        self.btn_refresh = QPushButton("Refresh")
        for b in (self.btn_edit_prices, self.btn_move, self.btn_print_purchase, self.btn_print, self.btn_print_value, self.btn_print_pay, self.btn_refresh):
            bar.addWidget(b)
        root.addLayout(bar)

        self.filters = TicketFilterBar()
        root.addWidget(self.filters)

        # registered people and walk-ins are kept apart; walk-ins can be ticked and paid together
        who = FlowLayout()
        self.who_seg = Segmented(WHO)
        who.addWidget(self.who_seg)
        who.addStretch(1)
        self.chk_all = QCheckBox("Select all")
        self.cmb_method = QComboBox()
        self.cmb_method.addItems(PAY_METHODS)
        self.cmb_method.setToolTip("How the selected walk-ins are paid")
        self.lbl_picked = QLabel("")
        self.lbl_picked.setStyleSheet("font-weight:600; color:#374151;")
        self.btn_pay_walkins = QPushButton("Pay selected walk-ins")
        self.btn_pay_walkins.setObjectName("PrimaryButton")
        self.walkin_tools = [self.chk_all, self.lbl_picked, self.cmb_method, self.btn_pay_walkins]
        for w in self.walkin_tools:
            who.addWidget(w)
            w.setVisible(False)
        root.addLayout(who)

        self.table = make_ticket_table(show_amount=True, show_weigher=True)
        self.walkin_table = make_ticket_table(show_amount=True, show_weigher=True, checkable=True)
        self.walkin_table.setVisible(False)
        root.addWidget(self.table, 1)
        root.addWidget(self.walkin_table, 1)
        hint = QLabel("Double-click a ticket to open it  -  the weigher records bags and weights; "
                      "price, payment and finalizing are done here.")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        root.addWidget(hint)

        self.filter_seg.changed.connect(lambda _k: self.refresh())
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_print.clicked.connect(lambda: self.print_weigh("weighing"))
        self.btn_print_value.clicked.connect(lambda: self.print_weigh("value"))
        self.btn_print_pay.clicked.connect(lambda: self.print_weigh("payment"))
        self.btn_print_purchase.clicked.connect(self.print_purchase)
        self.btn_move.clicked.connect(self.move_selected)
        self.btn_edit_prices.clicked.connect(self.edit_prices)
        for tbl in (self.table, self.walkin_table):
            tbl.setContextMenuPolicy(Qt.CustomContextMenu)
            tbl.customContextMenuRequested.connect(self._row_menu)
            tbl.itemSelectionChanged.connect(self._buttons)
            tbl.cellDoubleClicked.connect(lambda _r, _c: self.open_selected())
        self.filters.changed.connect(self.refresh)
        self.who_seg.changed.connect(self._who_changed)
        self.chk_all.toggled.connect(self._tick_all)
        self.walkin_table.itemChanged.connect(self._picked)
        self.btn_pay_walkins.clicked.connect(self.pay_walkins)
        self.refresh()

    def _tbl(self):
        """The list on screen: customers and suppliers, or walk-ins."""
        return self.walkin_table if self.who_seg.key() == "walkins" else self.table

    def _who_changed(self, *_a):
        walkins = self.who_seg.key() == "walkins"
        self.table.setVisible(not walkins)
        self.walkin_table.setVisible(walkins)
        for w in self.walkin_tools:
            w.setVisible(walkins)
        if self._tbl().rowCount() and self._tbl().currentRow() < 0:
            self._tbl().selectRow(0)
        self._buttons()
        self._picked()

    def refresh(self):
        try:
            f = self.filters.values()
            allrows = WeighService.list_tickets(search=f["search"], date_from=f["date_from"], date_to=f["date_to"])
        except Exception as e:
            QMessageBox.warning(self, "Weigh Tickets", str(e))
            return
        count = lambda s: sum(1 for t in allrows if t["status"] == s)       # noqa: E731
        self.kpi_wait.set(str(count("submitted")))
        self.kpi_open.set(str(count("open")))
        self.kpi_done.set(str(count("finalized")))
        key = self.filter_seg.key()
        rows = allrows if key == "all" else [t for t in allrows if t["status"] == key]
        self._rows = {int(t["id"]): t for t in rows}             # the list already holds what the buttons need
        people = [t for t in rows if not t.get("is_walkin")]
        walkins = [t for t in rows if t.get("is_walkin")]
        self.who_seg.set_label("customers", f"Customers and suppliers ({len(people)})")
        waiting = sum(1 for t in walkins if t["status"] == "submitted")
        self.who_seg.set_label("walkins", f"Walk-ins ({len(walkins)})" + (f"  -  {waiting} to pay" if waiting else ""))
        for tbl, part in ((self.table, people), (self.walkin_table, walkins)):
            tbl.blockSignals(True)
            fill_ticket_table(tbl, part)
            if tbl is self._tbl() and tbl.rowCount():
                tbl.selectRow(0)
            tbl.blockSignals(False)
        self.chk_all.blockSignals(True)
        self.chk_all.setChecked(False)
        self.chk_all.blockSignals(False)
        self._buttons()
        self._picked()

    # ------------------------------------------------------------------ walk-ins: tick and pay together
    def _boxes(self):
        t = self.walkin_table
        for r in range(t.rowCount()):
            it = t.item(r, 0)
            if it is not None and it.flags() & Qt.ItemIsUserCheckable:
                yield it

    def checked_ids(self):
        return [int(it.data(Qt.UserRole)) for it in self._boxes() if it.checkState() == Qt.Checked]

    def _tick_all(self, on: bool) -> None:
        self.walkin_table.blockSignals(True)
        for it in self._boxes():
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.walkin_table.blockSignals(False)
        self._picked()

    def _picked(self, *_a) -> None:
        ids = self.checked_ids()
        total = sum(float(self._rows[i].get("total_amount") or 0) for i in ids if i in self._rows)
        self.lbl_picked.setText(f"{len(ids)} selected  -  ₦{total:,.2f}" if ids else "Tick the walk-ins to pay")
        self.btn_pay_walkins.setEnabled(bool(ids))

    def pay_walkins(self) -> None:
        """Finalize and pay every ticked walk-in weighing at its current price, in one go."""
        ids = self.checked_ids()
        if not ids:
            QMessageBox.information(self, "Pay walk-ins", "Tick the walk-in weighings to pay first.")
            return
        rows = [self._rows[i] for i in ids if i in self._rows]
        total = sum(float(r.get("total_amount") or 0) for r in rows)
        method = self.cmb_method.currentText()
        names = "\n".join(f"  {r['ticket_no']}  {r['party_name']}  ₦{float(r.get('total_amount') or 0):,.2f}" for r in rows[:12])
        more = f"\n  ... and {len(rows) - 12} more" if len(rows) > 12 else ""
        ok = QMessageBox.question(
            self, "Pay walk-ins",
            f"Pay {len(rows)} walk-in weighing(s), ₦{total:,.2f} in all, by {method}?\n\n{names}{more}\n\n"
            "Each one is finalized at its current price and paid in full.", QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            res = WeighService.pay_walkins(ids, method)
        except Exception as e:
            QMessageBox.warning(self, "Pay walk-ins", str(e))
            return
        msg = f"Paid {len(res['paid'])} walk-in weighing(s): ₦{res['total']:,.2f}."
        if res["failed"]:
            msg += "\n\nNot done:\n" + "\n".join(f"  {f['ticket_no']}: {f['error']}" for f in res["failed"])
            QMessageBox.warning(self, "Pay walk-ins", msg)
        else:
            QMessageBox.information(self, "Pay walk-ins", msg)
        self.refresh()

    def _selected(self):
        """(id, the list's row for it): status, purchase and party are all there - no database trip per click."""
        tid = selected_ticket_id(self._tbl())
        t = getattr(self, "_rows", {}).get(tid) if tid else None
        return (tid, t) if t else (None, None)

    def _buttons(self):
        _tid, t = self._selected()
        self.btn_print.setEnabled(bool(t and t["status"] != "open"))
        self.btn_print_value.setEnabled(bool(t and t["status"] != "open"))
        self.btn_print_pay.setEnabled(bool(t and t["status"] != "open"))
        self.btn_print_purchase.setEnabled(bool(t and t.get("purchase_id")))
        self.btn_edit_prices.setEnabled(bool(t and t["status"] == "finalized" and t.get("purchase_id")))
        from shared.auth.permissions import current_role
        self.btn_move.setEnabled(bool(t and (t["status"] in ("open", "submitted")
                                             or (t["status"] == "finalized" and current_role() in ("admin", "pm")))))

    def _row_menu(self, pos) -> None:
        tbl = self._tbl()
        row = tbl.rowAt(pos.y())
        if row < 0:
            return
        tbl.setCurrentCell(row, 0)
        _tid, t = self._selected()
        if not t:
            return
        menu = QMenu(self)
        menu.addAction("Open").triggered.connect(self.open_selected)
        if t["status"] == "finalized" and t.get("purchase_id"):
            menu.addAction("Edit prices…").triggered.connect(self.edit_prices)
        menu.exec_(tbl.viewport().mapToGlobal(pos))

    def edit_prices(self):
        """Change the price of each product on a finalized weighing's purchase."""
        _tid, t = self._selected()
        if not t:
            QMessageBox.information(self, "Edit prices", "Select a ticket first.")
            return
        if t["status"] != "finalized":
            QMessageBox.information(self, "Edit prices", "Open the ticket to price it - prices of a finalized one are edited here.")
            return
        if edit_finalized_prices(self, t):
            self.refresh()

    def move_selected(self):
        """Weighed for the wrong person: move the whole weighing to someone else."""
        tid, _t = self._selected()
        if not tid:
            QMessageBox.information(self, "Change customer", "Select a ticket first.")
            return
        from apps.product_management_app.features.weigher.dialogs.reassign_dialog import move_weighing
        if move_weighing(self, tid):
            self.refresh()

    def print_weigh(self, option: str):
        """The weigher's receipt (Option 1 weighing / Option 2 full value) - only when asked for."""
        tid, t = self._selected()
        if not t:
            QMessageBox.information(self, "Print Receipt", "Select a ticket first.")
            return
        try:
            t = WeighService.get_ticket(tid)                        # the slip needs every line and round
            from apps.product_management_app.features.receipts_v2 import get_shop, receipt_print, render_weigh_slip
            receipt_print.print_text(self, render_weigh_slip(t, get_shop(), option),
                                     window_title=f"Weighing Receipt - {t['ticket_no']}")
        except Exception as e:
            QMessageBox.warning(self, "Print Receipt", str(e))

    def print_purchase(self):
        """The purchase receipt made when the ticket was finalized - only when asked for."""
        _tid, t = self._selected()
        if not t or not t.get("purchase_id"):
            QMessageBox.information(self, "Print Receipt", "Select a finalized ticket first.")
            return
        try:
            from apps.product_management_app.features.receipts_v2 import MODE_FULL, PURCHASE, print_receipt
            print_receipt(self, PURCHASE, int(t["purchase_id"]), MODE_FULL)
        except Exception as e:
            QMessageBox.warning(self, "Print Receipt", str(e))

    def open_selected(self):
        tid = selected_ticket_id(self._tbl())
        if not tid:
            QMessageBox.information(self, "Open Intake", "Select a ticket first.")
            return
        dlg = WeighTicketReviewDialog(tid, parent=self)
        dlg.exec_()
        self.refresh()
