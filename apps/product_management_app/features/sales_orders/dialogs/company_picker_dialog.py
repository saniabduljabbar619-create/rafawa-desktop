# apps/product_management_app/features/sales_orders/dialogs/company_picker_dialog.py
# -*- coding: utf-8 -*-
"""
A real, searchable table of companies - the search-first, create-if-
not-found flow. A company is required to create a Sales Order (unlike
the driver picker, there's no "skip" option here).
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QFormLayout
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


class CompanyPickerDialog(QDialog):
    """exec_() then read .selected_company_id / .selected_company_name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_company_id = None
        self.selected_company_name = None

        self.setWindowTitle("Select Company")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(660, 500)

        layout = QVBoxLayout(self)

        title = QLabel("Select Company")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(
            "Search first - a company is never a customer, so it won't "
            "appear anywhere else in the app. Only create a new one if it "
            "genuinely doesn't exist yet."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Company name…")
        self.search_in.textChanged.connect(self._reload)
        search_row.addWidget(self.search_in, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Contact Person", "Phone"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self._select())
        layout.addWidget(self.table, 1)

        # ---- inline "new company" form, always visible below the table ----
        new_title = QLabel("+ New Company")
        new_title.setStyleSheet("font-weight: 700; padding-top: 6px;")
        layout.addWidget(new_title)

        form = QFormLayout()
        self.new_name_in = QLineEdit()
        self.new_address_in = QLineEdit()
        self.new_phone_in = QLineEdit()
        self.new_contact_in = QLineEdit()
        form.addRow("Name:", self.new_name_in)
        form.addRow("Address:", self.new_address_in)
        form.addRow("Phone:", self.new_phone_in)
        form.addRow("Contact Person:", self.new_contact_in)
        layout.addLayout(form)

        self.btn_create_company = QPushButton("Create & Select")
        layout.addWidget(self.btn_create_company)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Select")
        self.btn_select.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_select)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_select.clicked.connect(self._select)
        self.btn_create_company.clicked.connect(self._create_company)

        self._rows = []
        self._reload()

    def _reload(self, *_args):
        from database.services.company_service import CompanyService
        self._rows = CompanyService.search(self.search_in.text())

        self.table.setRowCount(len(self._rows))
        for r, c in enumerate(self._rows):
            cells = [c["name"], c["contact_person"], c["phone"]]
            for col, text in enumerate(cells):
                it = QTableWidgetItem(str(text or ""))
                it.setData(Qt.UserRole, c["id"])
                self.table.setItem(r, col, it)

    def _select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Select Company", "Select a company from the list first.")
            return
        self.selected_company_id = self._rows[row]["id"]
        self.selected_company_name = self._rows[row]["name"]
        self.accept()

    def _create_company(self):
        name = self.new_name_in.text().strip()
        if not name:
            QMessageBox.warning(self, "New Company", "Enter a company name first.")
            return

        from database.services.company_service import CompanyService
        try:
            company_id = CompanyService.create(
                name=name, created_by_user_id=state.require_user_id(),
                address=self.new_address_in.text().strip(),
                phone=self.new_phone_in.text().strip(),
                contact_person=self.new_contact_in.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Create Failed", str(e))
            return

        self.selected_company_id = company_id
        self.selected_company_name = name
        self.accept()