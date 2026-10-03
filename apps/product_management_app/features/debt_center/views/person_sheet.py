# apps/product_management_app/features/debt_center/views/person_sheet.py
"""One person's sheet: type each amount in the last row; the running balance does the sums."""
from __future__ import annotations

import csv

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from database.services.debt_book_service import DebtBookService
from shared.uix.widgets.cards import avatar, chip, money
from apps.product_management_app.features.debt_center.dialogs.payment_dialog import PaymentDialog
from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.debt_center.views.sheet_grid import SheetGrid, today_iso

WORDS = {
    "creditor": {"owed": "He gave us / we owe (₦)", "paid": "We paid him (₦)", "pay": "Record payment to him", "list": "My Creditors",
                 "bal": "We still owe him", "owed_cap": "Total he gave us", "paid_cap": "Total we paid him"},
    "debtor": {"owed": "We gave him / he owes (₦)", "paid": "He paid us (₦)", "pay": "Record payment from him", "list": "My Debtors",
               "bal": "He still owes us", "owed_cap": "Total we gave him", "paid_cap": "Total he paid us"},
}


class PersonSheetPage(QWidget):
    back = Signal()
    changed = Signal()
    edit_person = Signal()

    def __init__(self, person_id: int, kind: str):
        super().__init__()
        self.person_id, self.kind = int(person_id), kind
        self.words = WORDS[kind]
        self.data = None
        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.btn_back = QPushButton(f"<  {self.words['list']}")
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)
        self.head_slot = QHBoxLayout()
        head.addLayout(self.head_slot)
        head.addStretch(1)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_balance = kpi(self.words["bal"], dark=True)
        self.k_owed = kpi(self.words["owed_cap"])
        self.k_paid = kpi(self.words["paid_cap"])
        self.k_rows = kpi("Rows on the sheet")
        for k in (self.k_balance, self.k_owed, self.k_paid, self.k_rows):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add row")
        self.btn_add.setObjectName("msBig")
        self.btn_pay = QPushButton(self.words["pay"])
        self.btn_pay.setObjectName("msPay")
        self.btn_remove = QPushButton("Remove row")
        self.btn_remove.setObjectName("dcGhost")
        self.btn_restore = QPushButton("Bring row back")
        self.btn_restore.setObjectName("dcGhost")
        self.btn_edit = QPushButton("Edit details")
        self.btn_edit.setObjectName("dcGhost")
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setObjectName("dcGhost")
        for b in (self.btn_add, self.btn_pay, self.btn_remove, self.btn_restore):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_edit)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

        w = self.words
        self.grid = SheetGrid(
            columns=[
                {"key": "date", "label": "Date", "type": "date", "width": 100},
                {"key": "details", "label": "What it was for", "type": "text", "width": 220, "stretch": True},
                {"key": "owed", "label": w["owed"], "type": "money", "width": 175, "tone": "#a23b34"},
                {"key": "note", "label": "Note", "type": "text", "width": 150},
            ],
            computed=[{"key": "paid", "label": w["paid"].replace("(₦)", "so far (₦)"), "width": 150},
                      {"key": "left", "label": "Row still\nunpaid", "width": 130},
                      {"key": "running", "label": "Balance\nauto", "width": 150}],
            first_edit_key="details", placeholder_key="details",
            defaults={"date": today_iso(), "method": "Cash"},
            ready=self._ready, on_add=self._add, on_edit=self._edit, on_remove=self._remove,
            on_restore=lambda rid: DebtBookService.restore_entry(rid), reload=self.reload,
            hint="Type in the last row to add a debt - “What it was for” and the amount save it. To pay on a row, "
                 "click the row and press “Record payment” - every payment is saved separately on that row. "
                 "Right-click a row to remove it. This sheet is your own record; it does not change any "
                 "customer, supplier, sale or purchase.")
        root.addWidget(self.grid, 1)

        self.btn_add.clicked.connect(self.grid.add_row)
        self.btn_pay.clicked.connect(self._pay)
        self.btn_remove.clicked.connect(lambda: self.grid.remove())
        self.btn_restore.clicked.connect(self.grid.restore)
        self.btn_edit.clicked.connect(self.edit_person.emit)
        self.btn_export.clicked.connect(self._export)
        self.reload()

    # ---------------------------------------------------------------- grid callbacks
    def _ready(self, d) -> str:
        if not str(d.get("details", "")).strip():
            return "Now type what it was for to save the row."
        try:
            amt = float(str(d.get("owed") or 0).replace(",", ""))
        except ValueError:
            return "Amounts must be numbers."
        if amt <= 0:
            return "Now type the amount of the debt to save the row."
        return ""

    def _pay(self) -> None:
        r = self.grid.selected_row()
        if not r or r.get("id") is None:
            self.grid.say("Click the debt row you are paying on first.")
            return
        if r.get("removed"):
            self.grid.say("That row is crossed out - bring it back before paying on it.")
            return
        dlg = PaymentDialog(r["id"], self.person_id, self.words["paid"].replace(" (₦)", ""), self)
        dlg.exec_()
        if dlg.changed:
            self.reload()
            self.changed.emit()

    def _add(self, values) -> None:
        DebtBookService.add_entry(self.person_id, values)
        self.changed.emit()

    def _edit(self, row_id, key, raw) -> None:
        DebtBookService.edit_entry(row_id, {key: raw})
        self.changed.emit()

    def _remove(self, row_id, reason) -> None:
        DebtBookService.remove_entry(row_id, reason)
        self.changed.emit()

    # ---------------------------------------------------------------- data
    def reload(self) -> None:
        self.data = DebtBookService.sheet(self.person_id)
        p, f = self.data["person"], self.data["figures"]
        while self.head_slot.count():
            w = self.head_slot.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.head_slot.addWidget(avatar(p["name"], 44))
        box = QVBoxLayout()
        box.setSpacing(0)
        name = QLabel(p["name"])
        name.setStyleSheet("font-size:19px; font-weight:800; color:#1c2333;")
        sub = QLabel("  -  ".join(x for x in (p["phone"], p["address"]) if x) or "No contact details")
        sub.setObjectName("dcSub")
        box.addWidget(name)
        box.addWidget(sub)
        holder = QWidget()
        holder.setLayout(box)
        self.head_slot.addWidget(holder)
        for text, tone in ((("Creditor" if self.kind == "creditor" else "Debtor"),
                            ("red" if self.kind == "creditor" else "green")),) + ((("Archived", "grey"),) if p["archived"] else ()):
            c = chip(text, tone)
            c.setFixedHeight(22)
            self.head_slot.addWidget(c, 0, Qt.AlignVCenter)

        bal = f["balance"]
        self.k_balance.val.setText(money(abs(bal)))
        self.k_balance.cap.setText(self.words["bal"] if bal > 0.005 else
                                   ("Overpaid - " + ("we paid more than we owed" if self.kind == "creditor"
                                                     else "he paid more than he owed") if bal < -0.005 else "Settled"))
        self.k_owed.val.setText(money(f["owed"]))
        self.k_paid.val.setText(money(f["paid"]))
        self.k_rows.val.setText(str(p["entries"]))

        rows = []
        for r in self.data["rows"]:
            rows.append({
                "id": r["id"], "removed": r["removed"],
                "cells": {"date": r["date"].isoformat(), "details": r["details"], "owed": r["owed"] or "",
                          "note": r["note"]},
                "computed": {"paid": "" if r["removed"] or not r["paid"] else money(r["paid"]),
                             "left": "" if r["removed"] else money(r["row_balance"]),
                             "running": "" if r["removed"] else money(r["running"])},
                "tip": (f"Removed: {r['removed_reason']}" if r["removed"] and r["removed_reason"] else ""),
            })
        self.grid.set_rows(rows, {"owed": money(f["owed"]), "paid": money(f["paid"]),
                                  "left": money(f["balance"]), "running": money(f["balance"])})

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export sheet", f"{self.data['person']['name']}.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Date", "What it was for", self.words["owed"], self.words["paid"], "Row still unpaid", "Note",
                        "Balance", "Removed", "Payments (date: amount how)"])
            for r in self.data["rows"]:
                pays = "; ".join(f"{p['date']:%d/%m/%Y}: {p['amount']:.2f} {p['method']}".strip()
                                 for p in r["payments"] if not p["removed"])
                w.writerow([r["date"].isoformat(), r["details"], r["owed"], r["paid"], f"{r['row_balance']:.2f}",
                            r["note"], "" if r["removed"] else f"{r['running']:.2f}", "yes" if r["removed"] else "",
                            pays])
        QMessageBox.information(self, "Debt Center", f"Saved to\n{path}")
