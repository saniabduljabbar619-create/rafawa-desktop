# apps/product_management_app/features/debt_center/views/worksheet.py
"""Worksheet for one party: open documents, a payment panel, and the payment trail."""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.debt_center_service import DebtCenterService, OVERDUE_DAYS
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import KpiCard, Segmented, avatar, chip, money, style_table

METHODS = [("cash", "Cash"), ("transfer", "Bank transfer"), ("pos", "POS"), ("cheque", "Cheque")]
DOC_COLS = ["", "Document", "Date", "Age", "Total", "Settled", "Balance", "Status"]
PAY_COLS = ["Date", "Document", "Direction", "Method", "Amount", "Note"]
SIDE_TABS = [("creditors", "We owe them"), ("debtors", "They owe us")]


def _tone(status: str) -> str:
    return {"paid": "green", "partial": "amber", "unpaid": "red"}.get(status, "grey")


class DebtWorksheetPage(QWidget):
    back = Signal()
    changed = Signal()

    def __init__(self, party_id: int, side: str = "creditors"):
        super().__init__()
        self.party_id = int(party_id)
        self.side = side
        self.data = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.btn_back = QPushButton("<  Back")
        self.btn_back.setProperty("role", "plain")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        self.head_slot = QHBoxLayout()
        head.addLayout(self.head_slot)
        head.addStretch(1)
        self.seg = Segmented(SIDE_TABS)
        self.seg.set_key(side)
        self.seg.changed.connect(self._on_side)
        head.addWidget(self.seg)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_we = KpiCard("We owe them", "0.00", "open purchases", "red")
        self.kpi_they = KpiCard("They owe us", "0.00", "open sales", "green")
        self.kpi_net = KpiCard("Net position", "0.00", "", "blue")
        self.kpi_adv = KpiCard("Advances", "0.00", "", "amber")
        for k in (self.kpi_we, self.kpi_they, self.kpi_net, self.kpi_adv):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        self.lbl_docs = QLabel("")
        self.lbl_docs.setStyleSheet("font-weight:700; color:#111827;")
        left.addWidget(self.lbl_docs)
        self.docs = QTableWidget(0, len(DOC_COLS))
        self.docs.setHorizontalHeaderLabels(DOC_COLS)
        self.docs.setEditTriggers(QTableWidget.NoEditTriggers)
        self.docs.setSelectionMode(QAbstractItemView.NoSelection)
        style_table(self.docs, 40)
        hh = self.docs.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.docs.setColumnWidth(0, 34)
        left.addWidget(self.docs, 1)
        body.addLayout(left, 3)

        card = QFrame()
        card.setObjectName("PayCard")
        card.setStyleSheet("QFrame#PayCard { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; }"
                           "QFrame#PayCard QLabel { background:transparent; border:none; }")
        card.setMinimumWidth(300)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)
        self.lbl_pay_title = QLabel("")
        self.lbl_pay_title.setStyleSheet("font-size:15px; font-weight:700;")
        cl.addWidget(self.lbl_pay_title)
        self.lbl_selected = QLabel("")
        self.lbl_selected.setStyleSheet("color:#6b7280; font-size:12px;")
        cl.addWidget(self.lbl_selected)
        cl.addWidget(QLabel("Amount"))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1_000_000_000)
        self.amount.setDecimals(2)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setMinimumHeight(36)
        cl.addWidget(self.amount)
        self.btn_full = QPushButton("Fill full balance")
        self.btn_full.setProperty("role", "plain")
        cl.addWidget(self.btn_full)
        cl.addWidget(QLabel("Method"))
        self.method = QComboBox()
        for k, label in METHODS:
            self.method.addItem(label, k)
        cl.addWidget(self.method)
        cl.addWidget(QLabel("Note (optional)"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. part payment for June")
        cl.addWidget(self.note)
        self.lbl_after = QLabel("")
        self.lbl_after.setStyleSheet("color:#374151; font-size:12px; font-weight:600;")
        cl.addWidget(self.lbl_after)
        self.btn_pay = QPushButton("")
        self.btn_pay.setObjectName("PrimaryButton")
        self.btn_pay.setMinimumHeight(40)
        cl.addWidget(self.btn_pay)
        hint = QLabel("Tick specific documents to pay only those. With none ticked, the oldest "
                      "documents are paid first.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        cl.addWidget(hint)
        cl.addStretch(1)
        body.addWidget(card, 2)

        lab = QLabel("Payment history")
        lab.setStyleSheet("font-weight:700; color:#111827;")
        root.addWidget(lab)
        self.hist = QTableWidget(0, len(PAY_COLS))
        self.hist.setHorizontalHeaderLabels(PAY_COLS)
        self.hist.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hist.setSelectionMode(QAbstractItemView.NoSelection)
        style_table(self.hist, 34)
        self.hist.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.hist.setMaximumHeight(220)
        root.addWidget(self.hist)

        self.docs.itemChanged.connect(self._sync_amount)
        self.btn_full.clicked.connect(self._fill_full)
        self.amount.valueChanged.connect(self._update_after)
        self.btn_pay.clicked.connect(self._pay)
        self.reload()

    # ---------------------------------------------------------------- data
    def reload(self) -> None:
        self.data = DebtCenterService.worksheet(self.party_id, include_settled=False)
        d = self.data
        while self.head_slot.count():
            w = self.head_slot.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.head_slot.addWidget(avatar(d["name"], 44))
        box = QVBoxLayout()
        box.setSpacing(0)
        name = QLabel(d["name"])
        name.setStyleSheet("font-size:18px; font-weight:700;")
        sub = QLabel("  -  ".join(x for x in (d["phone"], d["address"]) if x) or "No contact details")
        sub.setStyleSheet("color:#6b7280; font-size:12px;")
        box.addWidget(name)
        box.addWidget(sub)
        holder = QWidget()
        holder.setLayout(box)
        self.head_slot.addWidget(holder)
        if d["is_supplier"]:
            self.head_slot.addWidget(chip("Supplier", "blue"))
        if d["is_customer"]:
            self.head_slot.addWidget(chip("Customer", "green"))

        self.kpi_we.set(money(d["we_owe"]), f"{len(d['payables'])} open purchase(s)")
        self.kpi_they.set(money(d["they_owe"]), f"{len(d['receivables'])} open sale(s)")
        net = d["net"]
        self.kpi_net.set(money(abs(net)), "they owe us on balance" if net > 0 else
                         "we owe them on balance" if net < 0 else "all square")
        self.kpi_adv.set(money(d["advance_out"] + d["advance_in"]),
                         f"we advanced {money(d['advance_out'])}  |  they deposited {money(d['advance_in'])}")
        self._render_docs()
        self._render_history()

    def _docs_for_side(self):
        return self.data.get("payables" if self.side == "creditors" else "receivables", [])

    def _render_docs(self) -> None:
        docs = self._docs_for_side()
        paying = self.side == "creditors"
        self.lbl_docs.setText(("Open purchases - what we still owe" if paying else
                               "Open sales - what they still owe us") + f"  ({len(docs)})")
        self.lbl_pay_title.setText("Pay this creditor" if paying else "Receive from this debtor")
        self.btn_pay.setText("Record payment" if paying else "Record receipt")
        self.docs.blockSignals(True)
        self.docs.setRowCount(len(docs))
        for i, d in enumerate(docs):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, d["doc_id"])
            chk.setData(Qt.UserRole + 1, d["balance"])
            self.docs.setItem(i, 0, chk)
            vals = [d["ref"], d["date"].strftime("%d %b %Y") if d["date"] else "",
                    f"{d['age_days']}d", money(d["total"]), money(d["settled"]), money(d["balance"]),
                    d["status"].title()]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                if c in (4, 5, 6):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 6:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if c == 3 and d["age_days"] > OVERDUE_DAYS:
                    it.setForeground(QColor("#b45309"))
                self.docs.setItem(i, c, it)
        self.docs.blockSignals(False)
        self._fill_full()

    def _render_history(self) -> None:
        pays = self.data.get("payments", [])
        self.hist.setRowCount(len(pays))
        for i, p in enumerate(pays):
            ref = ("PUR-" if p["kind"] == "purchase" else "SAL-") + str(p["doc_id"])
            vals = [p["date"].strftime("%d %b %Y %H:%M") if p["date"] else "", ref,
                    "Paid out" if p["direction"] == "out" else "Received", p["method"].title(),
                    money(p["amount"]), p["notes"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.hist.setItem(i, c, it)

    # ---------------------------------------------------------------- payment panel
    def _checked(self):
        out = []
        for r in range(self.docs.rowCount()):
            it = self.docs.item(r, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append((int(it.data(Qt.UserRole)), float(it.data(Qt.UserRole + 1))))
        return out

    def _target_total(self) -> float:
        checked = self._checked()
        if checked:
            return round(sum(b for _i, b in checked), 2)
        return round(sum(d["balance"] for d in self._docs_for_side()), 2)

    def _sync_amount(self, *_a) -> None:
        self._fill_full()

    def _fill_full(self) -> None:
        total = self._target_total()
        self.amount.setMaximum(max(total, 0.0))
        self.amount.setValue(total)
        n = len(self._checked())
        self.lbl_selected.setText(f"{n} document(s) ticked - {money(total)}" if n else
                                  f"All open documents - {money(total)}")
        self._update_after()

    def _update_after(self, *_a) -> None:
        left = max(self._target_total() - self.amount.value(), 0.0)
        self.lbl_after.setText(f"Still owed after this: {money(left)}")

    def _on_side(self, key: str) -> None:
        self.side = key
        self._render_docs()

    def _pay(self) -> None:
        amount = round(self.amount.value(), 2)
        if amount <= 0:
            QMessageBox.warning(self, "Debt Center", "Enter an amount greater than zero.")
            return
        paying = self.side == "creditors"
        who = self.data.get("name", "")
        verb = f"Pay {money(amount)} to {who}" if paying else f"Receive {money(amount)} from {who}"
        if QMessageBox.question(self, "Confirm", f"{verb}\nMethod: {self.method.currentText()}\n\nProceed?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            res = DebtCenterService.pay(self.party_id, self.side, amount, method=self.method.currentData(),
                                        note=self.note.text(), doc_ids=[i for i, _b in self._checked()] or None)
        except PermissionDenied as e:
            QMessageBox.warning(self, "Not allowed", str(e))
            return
        except ValueError as e:
            QMessageBox.warning(self, "Debt Center", str(e))
            return
        except Exception as e:                      # keep the screen alive on a database error
            QMessageBox.critical(self, "Debt Center", f"The payment was not recorded.\n\n{e}")
            return
        self.note.clear()
        self.reload()
        self.changed.emit()
        QMessageBox.information(
            self, "Recorded",
            f"{'Paid' if paying else 'Received'} {money(res['paid'])} across {len(res['allocations'])} "
            f"document(s).\nStill owed: {money(res['still_owed'])}")
