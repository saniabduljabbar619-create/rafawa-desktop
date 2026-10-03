# apps/product_management_app/features/debt_center/views/payments_sheet.py
"""The Payments sheet: pick a record, type the amount, press Save - each payment becomes its own row."""
from __future__ import annotations

from datetime import date

from PySide2.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from database.services.debt_book_service import DebtBookService, METHODS
from shared.uix.widgets.cards import money
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid


class PaymentsSheet(QWidget):
    """Tracks every payment on this person's records. `saved` is called after any change."""

    def __init__(self, person_id: int, pay_word: str, saved):
        super().__init__()
        self.person_id, self.saved = int(person_id), saved
        self.records = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(6)
        self.record = QComboBox()
        self.record.setMinimumWidth(420)
        self.when = QLineEdit(date.today().strftime("%d/%m/%Y"))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText(f"{pay_word} (₦)")
        self.method = QComboBox()
        for m in METHODS:
            self.method.addItem(m.title(), m)
        self.note = QLineEdit()
        for label, w in (("Pay on record", self.record), ("Date", self.when), ("Amount", self.amount),
                         ("How", self.method), ("Note", self.note)):
            form.addRow(label, w)
        root.addLayout(form)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Save payment")
        self.btn_save.setObjectName("msBig")
        self.btn_remove = QPushButton("Remove payment")
        self.btn_remove.setObjectName("dcGhost")
        self.btn_restore = QPushButton("Bring payment back")
        self.btn_restore.setObjectName("dcGhost")
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        for b in (self.btn_save, self.btn_remove, self.btn_restore):
            row.addWidget(b)
        row.addWidget(self.msg, 1)
        root.addLayout(row)

        self.grid = SheetGrid(
            columns=[
                {"key": "date", "label": "Date", "type": "text", "width": 100, "readonly": True},
                {"key": "record", "label": "Paid on record", "type": "text", "width": 300, "stretch": True,
                 "readonly": True},
                {"key": "amount", "label": pay_word + " (₦)", "type": "money", "width": 160, "tone": "#3f6b4a",
                 "readonly": True},
                {"key": "method", "label": "How", "type": "text", "width": 95, "readonly": True},
                {"key": "note", "label": "Note", "type": "text", "width": 180, "readonly": True},
            ],
            computed=[{"key": "after", "label": "Record still\nunpaid", "width": 150}],
            allow_add=False, on_remove=self._remove, on_restore=lambda pid: DebtBookService.restore_payment(pid),
            reload=self.saved,
            notify=lambda text, bad: self._say(text, bad),
            hint="Every payment is its own row here. To pay the rest of a record later, pick it above and Save again. "
                 "Right-click a payment to remove it.")
        root.addWidget(self.grid, 1)

        self.btn_save.clicked.connect(self.save)
        self.btn_remove.clicked.connect(lambda: self.grid.remove())
        self.btn_restore.clicked.connect(self.grid.restore)

    def _say(self, text: str, bad: bool = True) -> None:
        self.msg.setText(text)
        self.msg.setStyleSheet(f"color:{'#a23b34' if bad else '#3f6b4a'}; font-weight:600;")

    def _remove(self, pid, reason) -> None:
        DebtBookService.remove_payment(pid, reason)

    def select_record(self, row_id) -> None:
        i = self.record.findData(row_id)
        if i >= 0:
            self.record.setCurrentIndex(i)
        self.amount.setFocus()

    def load(self, rows) -> None:
        """rows: the sheet rows from DebtBookService.sheet()."""
        keep = self.record.currentData()
        self.records = rows
        self.record.blockSignals(True)
        self.record.clear()
        flat = []
        for n, r in enumerate(rows, start=1):
            label = f"{n}. {r['details']} ({r['date']:%d/%m/%Y})"
            if not r["removed"] and r["row_balance"] > 0.005:
                self.record.addItem(f"{label} - still unpaid {money(r['row_balance'])}", r["id"])
            left = r["owed"]
            for p in r["payments"]:
                if not p["removed"] and not r["removed"]:
                    left -= p["amount"]
                flat.append((p["date"], p["id"], r, label, p, left))
        self.record.blockSignals(False)
        i = self.record.findData(keep)
        if i >= 0:
            self.record.setCurrentIndex(i)
        self.btn_save.setEnabled(self.record.count() > 0)
        flat.sort(key=lambda t: (t[0], t[1]))
        grid_rows, total = [], 0.0
        for d, pid, r, label, p, left in flat:
            gone = p["removed"] or r["removed"]
            if not gone:
                total += p["amount"]
            grid_rows.append({
                "id": pid, "removed": gone,
                "cells": {"date": d.strftime("%d/%m/%Y"), "record": label.split(". ", 1)[1], "amount": p["amount"],
                          "method": p["method"].title(), "note": p["note"]},
                "computed": {"after": "" if gone else money(max(left, 0))},
                "tip": f"Removed: {p['removed_reason']}" if p["removed"] and p["removed_reason"] else ""})
        self.grid.set_rows(grid_rows, {"amount": money(total)})

    def save(self) -> None:
        rid = self.record.currentData()
        if rid is None:
            self._say("Choose the record you are paying on.")
            return
        try:
            DebtBookService.pay_entry(rid, {"amount": self.amount.text(), "date": self.when.text(),
                                            "method": self.method.currentData(), "note": self.note.text()})
        except ValueError as e:
            self._say(str(e))
            return
        except Exception as e:
            self._say(f"Not saved: {e}")
            return
        self.amount.clear()
        self.note.clear()
        self.saved()
        self._say("Payment saved.", bad=False)
