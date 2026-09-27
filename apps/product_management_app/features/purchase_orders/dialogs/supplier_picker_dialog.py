# apps/product_management_app/features/purchase_orders/dialogs/supplier_picker_dialog.py
# -*- coding: utf-8 -*-
"""
A real, searchable table of PO suppliers - the search-first, create-if-
not-found flow. A supplier is required to create a Purchase Order
(unlike the driver picker, there's no "skip" option here).
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QFormLayout
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


class SupplierPickerDialog(QDialog):
    """exec_() then read .selected_supplier_id / .selected_supplier_name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_supplier_id = None
        self.selected_supplier_name = None

        self.setWindowTitle("Select Supplier")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(660, 500)

        layout = QVBoxLayout(self)

        title = QLabel("Select Supplier")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(
            "Search first - this is the Purchase Order module's own "
            "supplier list, kept isolated from the rest of the app. "
            "Only create a new one if it genuinely doesn't exist yet."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Supplier name…")
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

        # ---- inline "new supplier" form, always visible below the table ----
        new_title = QLabel("+ New Supplier")
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

        self.btn_create_supplier = QPushButton("Create & Select")
        layout.addWidget(self.btn_create_supplier)

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
        self.btn_create_supplier.clicked.connect(self._create_supplier)

        self._rows = []
        self._reload()

    def _reload(self, *_args):
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
        self._rows = PurchaseOrderSupplierService.search(self.search_in.text())

        self.table.setRowCount(len(self._rows))
        for r, sup in enumerate(self._rows):
            cells = [sup["name"], sup["contact_person"], sup["phone"]]
            for col, text in enumerate(cells):
                it = QTableWidgetItem(str(text or ""))
                it.setData(Qt.UserRole, sup["id"])
                self.table.setItem(r, col, it)

    def _select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Select Supplier", "Select a supplier from the list first.")
            return
        self.selected_supplier_id = self._rows[row]["id"]
        self.selected_supplier_name = self._rows[row]["name"]
        self.accept()

    def _create_supplier(self):
        name = self.new_name_in.text().strip()
        if not name:
            QMessageBox.warning(self, "New Supplier", "Enter a supplier name first.")
            return

        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
        try:
            supplier_id = PurchaseOrderSupplierService.create(
                name=name, created_by_user_id=state.require_user_id(),
                address=self.new_address_in.text().strip(),
                phone=self.new_phone_in.text().strip(),
                contact_person=self.new_contact_in.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Create Failed", str(e))
            return

        self.selected_supplier_id = supplier_id
        self.selected_supplier_name = name
        self.accept()