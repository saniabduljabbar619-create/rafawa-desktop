# apps/product_management_app/features/weigher/dialogs/register_party_dialog.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from database.services.weigh_service import WeighService
from shared.uix.widgets.cards import Segmented


class RegisterPartyDialog(QDialog):
    """Register a new customer or supplier. After accept(), `.party` holds the saved party."""

    def __init__(self, parent=None, kind: str = "supplier"):
        super().__init__(parent)
        self.setWindowTitle("Register Customer / Supplier")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.party = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        title = QLabel("Register new")
        title.setObjectName("Title")
        root.addWidget(title)

        self.kind = Segmented([("supplier", "Supplier"), ("customer", "Customer")])
        self.kind.set_key(kind if kind in ("supplier", "customer") else "supplier")
        root.addWidget(self.kind)

        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Full name")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Phone number (used to avoid duplicates)")
        self.address = QLineEdit()
        self.address.setPlaceholderText("Address / village (optional)")
        for w in (self.name, self.phone, self.address):
            w.setMinimumHeight(34)
        form.addRow("Name *", self.name)
        form.addRow("Phone *", self.phone)
        form.addRow("Address", self.address)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Register")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        root.addLayout(row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)

    def _save(self):
        try:
            self.party = WeighService.register_party(
                self.kind.key(), self.name.text(), self.phone.text(), self.address.text())
        except Exception as e:
            QMessageBox.warning(self, "Register", str(e))
            return
        self.accept()
