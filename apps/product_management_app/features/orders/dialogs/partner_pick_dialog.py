# apps/product_management_app/features/orders/dialogs/partner_pick_dialog.py
# -*- coding: utf-8 -*-
"""
"Who is this order for?" - the first (and only) question when starting a new order.

Type a few letters to find the company (Sales Order) or supplier (Purchase Order), pick one and press Start order;
or add someone new right here. Nothing else to fill in before the order sheet opens.
"""
from __future__ import annotations

from typing import Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from database.services.order_sheet_service import OrderSheetService
from apps.product_management_app.features.orders.views.partners_page import PartnerDialog


class PartnerPickDialog(QDialog):
    def __init__(self, kind: str, parent=None, can_add: bool = True):
        super().__init__(parent)
        self.kind = kind
        self.partner_id: Optional[int] = None
        sales = kind == "sales"
        word = "company" if sales else "supplier"
        self.setWindowTitle("New Sales Order" if sales else "New Purchase Order")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        q = QLabel(f"<b>{'Which company are you selling to?' if sales else 'Which supplier are you buying from?'}</b>")
        q.setStyleSheet("font-size:15px;")
        lay.addWidget(q)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Type a few letters of the {word}'s name or phone number")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(34)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        lay.addWidget(self.list, 1)
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#6b7280;")
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        self.btn_new = QPushButton(f"+ Add a new {word}")
        self.btn_new.setVisible(can_add)
        row.addWidget(self.btn_new)
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_start = QPushButton("Start order")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.setDefault(True)
        self.btn_start.setEnabled(False)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_start)
        lay.addLayout(row)

        self.search.textChanged.connect(self._load)
        self.list.itemSelectionChanged.connect(lambda: self.btn_start.setEnabled(bool(self.list.selectedItems())))
        self.list.itemDoubleClicked.connect(lambda *_: self._start())
        self.search.returnPressed.connect(self._enter)
        self.btn_new.clicked.connect(self._add)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_start.clicked.connect(self._start)
        self._load()

    def _load(self, *_a, select_id: int = 0) -> None:
        rows = OrderSheetService.partners(self.kind, self.search.text().strip())
        self.list.clear()
        for r in rows:
            owed = f"      still owed ₦{r['outstanding']:,.2f}" if r.get("outstanding", 0) > 0.005 else ""
            it = QListWidgetItem(f"{r['name']}    {r.get('phone') or ''}{owed}".rstrip())
            it.setData(Qt.UserRole, int(r["id"]))
            self.list.addItem(it)
            if select_id and int(r["id"]) == select_id:
                self.list.setCurrentItem(it)
        if self.list.count() and not self.list.selectedItems():
            self.list.setCurrentRow(0)
        word = "company" if self.kind == "sales" else "supplier"
        self.hint.setText("" if self.list.count() else f"No {word} found - press “+ Add a new {word}”.")

    def _enter(self) -> None:
        if self.list.selectedItems():
            self._start()

    def _add(self) -> None:
        dlg = PartnerDialog(self.kind, self)
        if dlg.exec_() == dlg.Accepted and dlg.saved_id:
            self.search.clear()
            self._load(select_id=int(dlg.saved_id))
            if self.list.selectedItems():
                self._start()

    def _start(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        self.partner_id = int(items[0].data(Qt.UserRole))
        self.accept()
