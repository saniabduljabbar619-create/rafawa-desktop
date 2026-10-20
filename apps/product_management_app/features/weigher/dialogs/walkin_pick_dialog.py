# apps/product_management_app/features/weigher/dialogs/walkin_pick_dialog.py
# -*- coding: utf-8 -*-
"""
Walk-in at the scale: pick someone already on file (by name, phone, WKN-/WT-WKN- ID or a weighing number) or add a
new person. Every walk-in is their own profile with their own ID - two people with the same name are two walk-ins.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from database.services.weigh_service import WeighService


class WalkinPickDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.walkin = None                      # {id, code, name, phone}
        self.setWindowTitle("Walk-in")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)

        root.addWidget(self._cap("FIND A WALK-IN ALREADY ON FILE"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Name, phone, WKN-00012, WT-WKN-00001 or a weighing number...")
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)
        self.found = QListWidget()
        self.found.setMinimumHeight(130)
        root.addWidget(self.found)
        self.btn_pick = QPushButton("Weigh for the selected walk-in")
        self.btn_pick.setObjectName("PrimaryButton")
        self.btn_pick.setEnabled(False)
        root.addWidget(self.btn_pick)

        root.addSpacing(6)
        root.addWidget(self._cap("OR ADD A NEW WALK-IN"))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name (one name is fine)")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Phone (optional)")
        row.addWidget(self.name, 2)
        row.addWidget(self.phone, 1)
        root.addLayout(row)
        self.btn_new = QPushButton("Add new walk-in and weigh")
        root.addWidget(self.btn_new)
        self.note = QLabel("A new walk-in gets their own ID (WT-WKN-...). The same name twice is two different people.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#6b7280; font-size:11px;")
        root.addWidget(self.note)

        self.search.textChanged.connect(self._search)
        self.found.itemSelectionChanged.connect(lambda: self.btn_pick.setEnabled(bool(self.found.selectedItems())))
        self.found.itemDoubleClicked.connect(lambda _i: self._pick())
        self.btn_pick.clicked.connect(self._pick)
        self.btn_new.clicked.connect(self._add)

    @staticmethod
    def _cap(text):
        lab = QLabel(text)
        lab.setObjectName("SectionLabel")
        return lab

    def _search(self, *_a):
        self.found.clear()
        try:
            rows = WeighService.search_walkins(self.search.text())
        except Exception as e:
            QMessageBox.warning(self, "Walk-in", str(e))
            rows = []
        for w in rows:
            it = QListWidgetItem(f"{w['code'] or ('#' + str(w['id']))}    {w['name']}    {w['phone']}".rstrip())
            it.setData(Qt.UserRole, w)
            self.found.addItem(it)

    def _pick(self):
        items = self.found.selectedItems()
        if items:
            self.walkin = items[0].data(Qt.UserRole)
            self.accept()

    def _add(self):
        try:
            self.walkin = WeighService.new_walkin(self.name.text(), self.phone.text())
        except Exception as e:
            QMessageBox.warning(self, "Walk-in", str(e))
            return
        self.accept()
