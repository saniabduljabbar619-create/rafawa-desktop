# apps/product_management_app/features/debt_center/dialogs/payment_dialog.py
from __future__ import annotations

from datetime import date

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.debt_book_service import DebtBookService, METHODS
from shared.uix.widgets.cards import money


class PaymentDialog(QDialog):
    """Payments on ONE debt row. Each is saved on its own with the Save button, so paying again later
    adds to the earlier payments instead of replacing them."""

    def __init__(self, row_id: int, person_id: int, pay_word: str, parent=None):
        super().__init__(parent)
        self.row_id, self.person_id, self.changed = int(row_id), int(person_id), False
        self.setWindowTitle("Payments on this row")
        self.setMinimumSize(560, 460)
        lay = QVBoxLayout(self)
        self.head = QLabel("")
        self.head.setStyleSheet("font-size:14px; font-weight:700; color:#1c2333;")
        self.head.setWordWrap(True)
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#4a5266;")
        lay.addWidget(self.head)
        lay.addWidget(self.sub)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Amount (₦)", "How", "Note"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_remove = QPushButton("Remove selected payment")
        self.btn_restore = QPushButton("Bring it back")
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_restore)
        row.addStretch(1)
        lay.addLayout(row)

        form = QFormLayout()
        self.date = QLineEdit(date.today().strftime("%d/%m/%Y"))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText(f"{pay_word} (₦)")
        self.method = QComboBox()
        for m in METHODS:
            self.method.addItem(m.title(), m)
        self.note = QLineEdit()
        for label, w in (("Date", self.date), ("Amount", self.amount), ("How", self.method), ("Note", self.note)):
            form.addRow(label, w)
        lay.addLayout(form)
        self.msg = QLabel("")
        self.msg.setStyleSheet("color:#a23b34; font-weight:600;")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_save = QPushButton("Save payment")
        self.btn_save.setObjectName("msBig")
        self.btn_close = QPushButton("Close")
        foot.addWidget(self.btn_close)
        foot.addWidget(self.btn_save)
        lay.addLayout(foot)

        self.btn_save.clicked.connect(self.save)
        self.btn_close.clicked.connect(self.accept)
        self.btn_remove.clicked.connect(self.remove)
        self.btn_restore.clicked.connect(self.restore)
        self.reload()

    def _row(self):
        for r in DebtBookService.sheet(self.person_id)["rows"]:
            if r["id"] == self.row_id:
                return r
        raise ValueError("Row not found.")

    def reload(self) -> None:
        r = self._row()
        self.rec = r
        self.head.setText(f"{r['details']}  -  {r['date']:%d/%m/%Y}")
        self.sub.setText(f"Amount {money(r['owed'])}   |   paid so far {money(r['paid'])}   |   "
                         f"still unpaid {money(max(r['row_balance'], 0))}")
        self.table.setRowCount(len(r["payments"]))
        for i, p in enumerate(r["payments"]):
            vals = [p["date"].strftime("%d/%m/%Y"), money(p["amount"]), p["method"].title(), p["note"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, p["id"])
                it.setData(Qt.UserRole + 1, p["removed"])
                if p["removed"]:
                    f = it.font()
                    f.setStrikeOut(True)
                    it.setFont(f)
                    it.setForeground(Qt.gray)
                if j == 1:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.amount.setText("")
        self.amount.setFocus()

    def _selected(self):
        i = self.table.currentRow()
        if i < 0:
            return None, False
        it = self.table.item(i, 0)
        return it.data(Qt.UserRole), bool(it.data(Qt.UserRole + 1))

    def _try(self, fn) -> bool:
        try:
            fn()
        except ValueError as e:
            self.msg.setText(str(e))
            return False
        except Exception as e:
            self.msg.setText(f"Not saved: {e}")
            return False
        self.msg.setText("")
        self.changed = True
        self.reload()
        return True

    def save(self) -> None:
        self._try(lambda: DebtBookService.pay_entry(self.row_id, {
            "amount": self.amount.text(), "date": self.date.text(), "method": self.method.currentData(),
            "note": self.note.text()}))

    def remove(self) -> None:
        pid, gone = self._selected()
        if pid is None or gone:
            self.msg.setText("Select a payment that is still counted.")
            return
        reason, ok = QInputDialog.getText(self, "Remove payment", "Why is it being removed?")
        if ok:
            self._try(lambda: DebtBookService.remove_payment(pid, reason))

    def restore(self) -> None:
        pid, gone = self._selected()
        if pid is None or not gone:
            self.msg.setText("Select a crossed-out payment.")
            return
        self._try(lambda: DebtBookService.restore_payment(pid))


def warn(parent, text: str) -> None:
    QMessageBox.warning(parent, "Debt Center", text)
