# apps/product_management_app/features/weigher/dialogs/weigh_ticket_review_dialog.py
"""
Open Intake - where admin/cashier take over a ticket the weigher submitted:

    Verify & edit the rounds → Confirm price per kg → amount is calculated → Finalize Intake
    → Process payment → Print the payment receipt

The weigher has no say in payment or finalization; this dialog is where those happen.

The same dialog, opened with mode="weigher" from the weigher's Finalize List, is the weigher's EDIT screen:
correct the rounds, change the prices, save - or remove the ticket. No finalize, no payment. A weigher can
do this for their own tickets until the cashier finalizes them.
"""
from __future__ import annotations

from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.weigh_service import WeighService, avg_kg_per_bag
from shared.auth.session import require_user_id
from shared.uix.widgets.cards import avatar, chip, style_table

LINE_COLS = ["Product", "Area / Grade", "Bags", "Weight (kg)", "Price per kg", "Amount"]


def _fmt_bags(v) -> str:
    v = float(v or 0)
    return f"{int(v)}" if v == int(v) else f"{v:g}"


class WeighTicketReviewDialog(QDialog):
    def __init__(self, ticket_id: int, parent=None, mode: str = "intake"):
        super().__init__(parent)
        self.ticket_id = int(ticket_id)
        self.mode = "weigher" if mode == "weigher" else "intake"
        self.result_purchase_id = 0
        self._busy = False
        self._price_spins = {}
        self.setWindowTitle("Edit Weighing" if self.mode == "weigher" else "Open Intake")
        self.setModal(True)
        self.resize(980, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # header
        head = QFrame()
        head.setObjectName("Card")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 10, 14, 10)
        self.avatar_holder = QVBoxLayout()
        hl.addLayout(self.avatar_holder)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.lbl_party = QLabel("")
        self.lbl_party.setStyleSheet("font-size:16px; font-weight:700;")
        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet("color:#6b7280; font-size:11px;")
        col.addWidget(self.lbl_party)
        col.addWidget(self.lbl_meta)
        hl.addLayout(col, 1)
        self.status_holder = QHBoxLayout()
        hl.addLayout(self.status_holder)
        root.addWidget(head)

        cap = QLabel("PRODUCTS - set the price per kg" if self.mode == "weigher" else "PRODUCTS - confirm the price per kg")
        cap.setObjectName("SectionLabel")
        root.addWidget(cap)
        self.lines_table = QTableWidget(0, len(LINE_COLS))
        self.lines_table.setHorizontalHeaderLabels(LINE_COLS)
        self.lines_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.lines_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.lines_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        style_table(self.lines_table, 44)
        self.lines_table.setMaximumHeight(210)
        root.addWidget(self.lines_table)

        cap2 = QLabel("ROUNDS - verify and correct (edit a cell, or add / remove a round)")
        cap2.setObjectName("SectionLabel")
        root.addWidget(cap2)
        self.rounds_table = QTableWidget(0, 4)
        self.rounds_table.setHorizontalHeaderLabels(["Round", "Bags", "Weight (kg)", "Avg / bag (kg)"])
        self.rounds_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rounds_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rounds_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        style_table(self.rounds_table, 36)
        root.addWidget(self.rounds_table, 1)

        add = QHBoxLayout()
        self.add_bags = QLineEdit()
        self.add_bags.setPlaceholderText("Bags")
        self.add_bags.setMaximumWidth(90)
        self.add_kg = QLineEdit()
        self.add_kg.setPlaceholderText("Weight kg")
        self.add_kg.setMaximumWidth(130)
        self.btn_add_round = QPushButton("+ Add Round")
        self.btn_remove_round = QPushButton("Remove Round")
        self.btn_remove_round.setProperty("role", "danger")
        add.addWidget(self.add_bags)
        add.addWidget(self.add_kg)
        add.addWidget(self.btn_add_round)
        add.addStretch(1)
        add.addWidget(self.btn_remove_round)
        root.addLayout(add)

        # footer: totals + actions
        foot = QHBoxLayout()
        self.lbl_totals = QLabel("")
        self.lbl_totals.setStyleSheet("font-size:13px; color:#374151;")
        foot.addWidget(self.lbl_totals, 1)
        amt = QVBoxLayout()
        amt.setSpacing(0)
        acap = QLabel("TOTAL AMOUNT")
        acap.setStyleSheet("color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px;")
        acap.setAlignment(Qt.AlignRight)
        self.lbl_amount = QLabel("₦0.00")
        self.lbl_amount.setStyleSheet("font-size:24px; font-weight:800; color:#0f766e;")
        self.lbl_amount.setAlignment(Qt.AlignRight)
        amt.addWidget(acap)
        amt.addWidget(self.lbl_amount)
        foot.addLayout(amt)
        root.addLayout(foot)

        btns = QHBoxLayout()
        self.btn_reject = QPushButton("Remove Ticket" if self.mode == "weigher" else "Cancel Ticket")
        self.btn_reject.setProperty("role", "danger")
        self.btn_print = QPushButton("Print Weighing Receipt")
        self.btn_print_value = QPushButton("Print Value Receipt")
        self.btn_close = QPushButton("Close")
        self.btn_finalize = QPushButton("Save Changes" if self.mode == "weigher" else "Finalize Intake  →  Payment")
        self.btn_finalize.setObjectName("PrimaryButton")
        self.btn_finalize.setMinimumHeight(42)
        btns.addWidget(self.btn_reject)
        btns.addWidget(self.btn_print)
        btns.addWidget(self.btn_print_value)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        btns.addWidget(self.btn_finalize)
        root.addLayout(btns)

        self.lines_table.itemSelectionChanged.connect(self._render_rounds)
        self.rounds_table.itemChanged.connect(self._on_round_edited)
        self.btn_add_round.clicked.connect(self._add_round)
        self.btn_remove_round.clicked.connect(self._remove_round)
        self.btn_finalize.clicked.connect(self.save_changes if self.mode == "weigher" else self.finalize)
        self.btn_reject.clicked.connect(self._cancel_ticket)
        self.btn_print.clicked.connect(lambda: self._print_slip("weighing"))
        self.btn_print_value.clicked.connect(lambda: self._print_slip("value"))
        self.btn_close.clicked.connect(self.reject)

        self._active_line = 0
        self.reload()

    # ------------------------------------------------------------------ data
    @property
    def editable(self) -> bool:
        status = getattr(self, "_t", {}).get("status")
        # intake: the cashier/admin work on a submitted ticket; weigher: their own, until it is finalized
        return status in (("open", "submitted") if self.mode == "weigher" else ("submitted",))

    def reload(self, keep_line: int = 0):
        try:
            self._t = WeighService.get_ticket(self.ticket_id)
        except Exception as e:
            QMessageBox.warning(self, "Open Intake", str(e))
            self.reject()
            return
        t = self._t
        self._active_line = keep_line or self._active_line
        while self.avatar_holder.count():
            w = self.avatar_holder.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.avatar_holder.addWidget(avatar(t["party_name"], 40))
        self.lbl_party.setText(f"{t['party_name']}  ({t['party_role']})")
        when = t["submitted_at"] or t["created_at"]
        bits = [t["ticket_no"], f"weighed by {t['weigher_name']}",
                when.strftime("%Y-%m-%d %H:%M") if when else "", t["party_phone"]]
        self.lbl_meta.setText("   -   ".join(b for b in bits if b))
        while self.status_holder.count():
            w = self.status_holder.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        tone = {"open": "amber", "submitted": "blue", "finalized": "green", "cancelled": "grey"}[t["status"]]
        label = {"open": "Still being weighed", "submitted": "Awaiting intake", "finalized": "Finalized",
                 "cancelled": "Cancelled"}[t["status"]]
        self.status_holder.addWidget(chip(label, tone))

        typed = {lid: spin.value() for lid, spin in self._price_spins.items()}
        self._price_spins = {}
        self.lines_table.blockSignals(True)
        self.lines_table.setRowCount(0)
        for ln in t["lines"]:
            r = self.lines_table.rowCount()
            self.lines_table.insertRow(r)
            tag = " / ".join(x for x in (ln["area"], ln["grade"] and f"Grade {ln['grade']}") if x)
            for c, v in enumerate((ln["product_name"], tag, _fmt_bags(ln["bags"]), f"{ln['kg']:,.2f}")):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, int(ln["id"]))
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.lines_table.setItem(r, c, it)
            spin = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(0, 10_000_000)
            spin.setPrefix("₦")
            spin.setValue(typed.get(int(ln["id"])) or float(ln["unit_price"] or 0))
            spin.setEnabled(self.editable)
            spin.valueChanged.connect(self._recalc_amounts)
            self.lines_table.setCellWidget(r, 4, spin)
            self._price_spins[int(ln["id"])] = spin
            amt = QTableWidgetItem("")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.lines_table.setItem(r, 5, amt)
            if int(ln["id"]) == self._active_line:
                self.lines_table.selectRow(r)
        if self.lines_table.currentRow() < 0 and self.lines_table.rowCount():
            self.lines_table.selectRow(0)
        self.lines_table.blockSignals(False)
        cur = self.lines_table.currentRow()
        it = self.lines_table.item(cur, 0) if cur >= 0 else None
        self._active_line = int(it.data(Qt.UserRole)) if it else 0
        self._render_rounds()
        self._recalc_amounts()
        self._apply_mode()

    def _apply_mode(self):
        ed = self.editable
        for w in (self.btn_add_round, self.btn_remove_round, self.add_bags, self.add_kg, self.btn_finalize,
                  self.btn_reject):
            w.setEnabled(ed)
        self.btn_print.setEnabled(self._t["status"] != "open")
        self.btn_close.setText("Close")
        self.btn_finalize.setVisible(ed if self.mode == "weigher" else self._t["status"] != "finalized")
        self.rounds_table.setEditTriggers(QTableWidget.AllEditTriggers if ed else QTableWidget.NoEditTriggers)

    def _line(self, line_id: int):
        return next((ln for ln in self._t["lines"] if int(ln["id"]) == int(line_id)), None)

    def _render_rounds(self):
        cur = self.lines_table.currentRow()
        it = self.lines_table.item(cur, 0) if cur >= 0 else None
        self._active_line = int(it.data(Qt.UserRole)) if it else 0
        ln = self._line(self._active_line)
        self.rounds_table.blockSignals(True)
        self.rounds_table.setRowCount(0)
        for r in (ln["rounds"] if ln else []):
            row = self.rounds_table.rowCount()
            self.rounds_table.insertRow(row)
            avg = avg_kg_per_bag(r["weight_kg"], r["bags"])
            for c, v in enumerate((str(r["round_no"]), _fmt_bags(r["bags"]), f"{r['weight_kg']:.2f}",
                                   "" if avg is None else f"{avg:,.2f}")):
                cell = QTableWidgetItem(v)
                cell.setData(Qt.UserRole, int(r["id"]))
                cell.setTextAlignment(Qt.AlignCenter if c == 0 else Qt.AlignRight | Qt.AlignVCenter)
                if c in (0, 3) or not self.editable:
                    cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if r.get("edited") and c:
                    cell.setForeground(QColor("#b45309"))
                self.rounds_table.setItem(row, c, cell)
        self.rounds_table.blockSignals(False)

    def _recalc_amounts(self, *_a):
        total = 0.0
        for r in range(self.lines_table.rowCount()):
            it = self.lines_table.item(r, 0)
            ln = self._line(int(it.data(Qt.UserRole)))
            price = self._price_spins[int(ln["id"])].value()
            amount = round(ln["kg"] * price, 2)
            total += amount
            self.lines_table.item(r, 5).setText(f"₦{amount:,.2f}" if price > 0 else "-")
        t = self._t
        self.lbl_totals.setText(f"{_fmt_bags(t['total_bags'])} bags   -   {t['total_kg']:,.2f} kg")
        self.lbl_amount.setText(f"₦{total:,.2f}")
        self._amount = total

    # ------------------------------------------------------------------ corrections
    def _on_round_edited(self, item):
        if not self.editable or item.column() in (0, 3):
            return
        rid = int(item.data(Qt.UserRole))
        row = item.row()
        try:
            bags = float((self.rounds_table.item(row, 1).text() or "0").replace(",", ""))
            kg = float((self.rounds_table.item(row, 2).text() or "0").replace(",", ""))
            WeighService.update_round(rid, bags=bags, weight_kg=kg)
        except Exception as e:
            QMessageBox.warning(self, "Round", str(e))
        QTimer.singleShot(0, lambda: self.reload(self._active_line))     # not inside the edit's own signal

    def _add_round(self):
        if not self._active_line:
            return
        try:
            bags = float((self.add_bags.text() or "0").replace(",", ""))
            kg = float((self.add_kg.text() or "0").replace(",", ""))
            WeighService.record_round(self._active_line, bags, kg)
        except Exception as e:
            QMessageBox.warning(self, "Round", str(e))
            return
        self.add_bags.clear()
        self.add_kg.clear()
        self.reload(self._active_line)

    def _remove_round(self):
        row = self.rounds_table.currentRow()
        it = self.rounds_table.item(row, 0) if row >= 0 else None
        if it is None:
            QMessageBox.information(self, "Round", "Select the round to remove.")
            return
        try:
            WeighService.remove_round(int(it.data(Qt.UserRole)))
        except Exception as e:
            QMessageBox.warning(self, "Round", str(e))
            return
        self.reload(self._active_line)

    # ------------------------------------------------------------------ weigher: save the corrections
    def save_changes(self):
        prices = {lid: spin.value() for lid, spin in self._price_spins.items()}
        missing = [self._line(lid)["product_name"] for lid, p in prices.items() if p <= 0]
        if missing:
            QMessageBox.warning(self, "Price", "Enter the price per kg for: " + ", ".join(missing))
            return
        try:
            WeighService.set_ticket_prices(self.ticket_id, prices)
        except Exception as e:
            QMessageBox.warning(self, "Save Changes", str(e))
            self.reload(self._active_line)
            return
        self.accept()

    # ------------------------------------------------------------------ finalize + payment
    def finalize(self):
        prices = {lid: spin.value() for lid, spin in self._price_spins.items()}
        missing = [self._line(lid)["product_name"] for lid, p in prices.items() if p <= 0]
        if missing:
            QMessageBox.warning(self, "Confirm Price", "Confirm the price per kg for: " + ", ".join(missing))
            return
        ok = QMessageBox.question(
            self, "Finalize Intake",
            f"Finalize {self._t['ticket_no']} for ₦{self._amount:,.2f}?\n\n"
            "This books the stock and what is owed. Payment follows next.",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            res = WeighService.finalize_ticket(self.ticket_id, prices)
        except Exception as e:
            QMessageBox.warning(self, "Finalize Intake", str(e))
            self.reload(self._active_line)
            return
        self.result_purchase_id = int(res["purchase_id"])
        try:
            self._process_payment(res)
        except Exception as e:
            QMessageBox.warning(
                self, "Payment",
                f"The intake is finalized (purchase #{res['purchase_id']}) but the payment step failed:\n{e}\n\n"
                "Take payment from Payments.")
        self.accept()

    def _process_payment(self, res):
        """Same payment flow as a normal cashier purchase: offer advances, take payment (receipts print on request)."""
        from database.models.party import Party
        from database.session import get_session
        from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
        from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog

        uid = require_user_id()
        walkin = (self._t.get("walkin_name") or "").strip()      # a walk-in: no account, so no advances to apply
        with get_session() as s:
            party = s.get(Party, int(res["party_id"]))
        if not walkin:
            prompt_and_apply_advance(
                self, party_id=int(res["party_id"]), entity_type="purchase", entity_id=int(res["purchase_id"]),
                balance=float(res["total_amount"]), user_id=uid, party_name=(party.display_name if party else ""))
        PurchasePaymentDialog(party=party, user_id=uid, parent=self, print_slip=False,
                              walkin_name=walkin or None).exec_()

    def _cancel_ticket(self):
        weigher = self.mode == "weigher"
        ok = QMessageBox.question(
            self, "Remove Ticket" if weigher else "Cancel Ticket",
            f"Remove {self._t['ticket_no']}? It is cancelled and kept in the history; nothing will be booked."
            if weigher else f"Cancel {self._t['ticket_no']}? Nothing will be booked.",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            WeighService.cancel_ticket(self.ticket_id, "Removed by the weigher" if weigher else "Rejected at intake")
        except Exception as e:
            QMessageBox.warning(self, "Cancel Ticket", str(e))
            return
        self.accept()

    def _print_slip(self, option: str = "weighing"):
        try:
            from apps.product_management_app.features.receipts_v2 import get_shop, receipt_print, render_weigh_slip
            receipt_print.print_text(self, render_weigh_slip(self._t, get_shop(), option),
                                     window_title=f"Weighing Receipt - {self._t['ticket_no']}")
        except Exception as e:
            QMessageBox.warning(self, "Print", str(e))
