# apps/product_management_app/features/suppliers/views/supplier_form.py
# -*- coding: utf-8 -*-
"""
Add Supplier.

Writes directly through SupplierProfileService (SQLAlchemy), matching every
other screen. This USED to POST to /api/suppliers via ApiClient/httpx - a
path nothing else in the app uses and that appears to have no real server
behind it. Its "Supplier Type" and "Company Ref" fields are dropped here
because neither exists as a column on Party; keeping them would mean typing
data into fields that quietly went nowhere.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox
)

from database.services.supplier_profile_service import SupplierProfileService


class SupplierFormDialog(QDialog):
    def __init__(self, parent=None, supplier: dict | None = None):
        super().__init__(parent)
        self._editing_id = int(supplier["id"]) if supplier else None
        self.setWindowTitle("Edit Supplier" if supplier else "Add Supplier")
        self.setMinimumWidth(420)
        self.created_supplier = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        self.name.setText(str(supplier.get("name", "")) if supplier else "")
        layout.addWidget(self.name)

        layout.addWidget(QLabel("Phone (optional)"))
        self.phone = QLineEdit()
        self.phone.setText(str(supplier.get("phone", "") or "") if supplier else "")
        layout.addWidget(self.phone)

        layout.addWidget(QLabel("Address (optional)"))
        self.address = QLineEdit()
        self.address.setText(str(supplier.get("address", "") or "") if supplier else "")
        layout.addWidget(self.address)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.on_save)
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def on_save(self):
        name = self.name.text().strip()
        phone = self.phone.text().strip()
        address = self.address.text().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return

        try:
            if self._editing_id is not None:
                SupplierProfileService.update_supplier(self._editing_id, name, phone, address)
                sid = self._editing_id
            else:
                sid = SupplierProfileService.get_or_lazy_create_supplier(name, phone, address)
        except Exception as e:
            QMessageBox.critical(self, "Supplier", f"Could not save supplier:\n{e}")
            return

        self.created_supplier = {"id": sid, "name": name, "phone": phone}
        self.accept()