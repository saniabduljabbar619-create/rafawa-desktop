# apps/product_management_app/features/debt_center/dialogs/person_dialog.py
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide2.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout,
)

from database.services.debt_book_service import DebtBookService


class PersonDialog(QDialog):
    """Add a creditor/debtor, or edit one (pass `person`). Nothing here touches customers or suppliers."""

    def __init__(self, kind: str, parent=None, person: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.kind, self.person = kind, person
        word = "creditor" if kind == "creditor" else "debtor"
        self.setWindowTitle(f"Edit {word}" if person else f"Add a {word}")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        sub = QLabel("Someone we owe money to." if kind == "creditor" else "Someone who owes us money.")
        sub.setStyleSheet("color:#4a5266;")
        lay.addWidget(sub)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Full name or business")
        self.phone = QLineEdit()
        self.address = QLineEdit()
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)
        for label, w in (("Name", self.name), ("Phone", self.phone), ("Address", self.address), ("Note", self.note)):
            form.addRow(label, w)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        if person:
            self.name.setText(person["name"])
            self.phone.setText(person["phone"])
            self.address.setText(person["address"])
            self.note.setPlainText(person["note"])
        self.saved_id: Optional[int] = None

    def _save(self) -> None:
        try:
            if self.person:
                DebtBookService.update_person(self.person["id"], self.name.text(), self.phone.text(),
                                              self.address.text(), self.note.toPlainText())
                self.saved_id = self.person["id"]
            else:
                self.saved_id = DebtBookService.add_person(self.kind, self.name.text(), self.phone.text(),
                                                           self.address.text(), self.note.toPlainText())
        except ValueError as e:
            QMessageBox.warning(self, "Debt Center", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Debt Center", f"Not saved.\n\n{e}")
            return
        self.accept()
