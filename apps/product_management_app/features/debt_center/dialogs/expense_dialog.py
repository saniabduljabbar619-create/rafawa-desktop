# apps/product_management_app/features/debt_center/dialogs/expense_dialog.py
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide2.QtCore import QDate
from PySide2.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLineEdit,
    QMessageBox, QPlainTextEdit, QVBoxLayout,
)

from database.services.expense_service import ExpenseService, METHODS


class ExpenseDialog(QDialog):
    """Add a new expense, or edit one (pass `expense`)."""

    def __init__(self, parent=None, expense: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.expense = expense
        self.setWindowTitle("Edit expense" if expense else "New expense")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.date.setDisplayFormat("dd MMM yyyy")
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(ExpenseService.categories())
        self.description = QLineEdit()
        self.description.setPlaceholderText("What was it for?")
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1_000_000_000)
        self.amount.setDecimals(2)
        self.amount.setGroupSeparatorShown(True)
        self.paid_to = QLineEdit()
        self.paid_to.setPlaceholderText("Who received the money (optional)")
        self.method = QComboBox()
        for m in METHODS:
            self.method.addItem(m.title(), m)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Receipt / transfer number (optional)")
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(60)

        for label, w in (("Date", self.date), ("Category", self.category), ("Description", self.description),
                         ("Amount", self.amount), ("Paid to", self.paid_to), ("Method", self.method),
                         ("Reference", self.reference), ("Notes", self.notes)):
            form.addRow(label, w)
        lay.addLayout(form)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        if expense:
            d = expense["date"]
            self.date.setDate(QDate(d.year, d.month, d.day))
            self.category.setCurrentText(expense["category"])
            self.description.setText(expense["description"])
            self.amount.setValue(expense["amount"])
            self.paid_to.setText(expense["paid_to"])
            i = self.method.findData(expense["method"])
            self.method.setCurrentIndex(max(i, 0))
            self.reference.setText(expense["reference"])
            self.notes.setPlainText(expense["notes"])

    def values(self) -> Dict[str, Any]:
        return {
            "date": self.date.date().toPython(), "category": self.category.currentText(),
            "description": self.description.text(), "amount": self.amount.value(),
            "paid_to": self.paid_to.text(), "method": self.method.currentData(),
            "reference": self.reference.text(), "notes": self.notes.toPlainText(),
        }

    def _save(self) -> None:
        try:
            if self.expense:
                ExpenseService.update(self.expense["id"], self.values())
            else:
                ExpenseService.add(self.values())
        except ValueError as e:
            QMessageBox.warning(self, "Expense", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Expense", f"Not saved.\n\n{e}")
            return
        self.accept()
