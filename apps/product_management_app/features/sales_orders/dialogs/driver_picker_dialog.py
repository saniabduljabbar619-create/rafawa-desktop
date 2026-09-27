# apps/product_management_app/features/sales_orders/dialogs/driver_picker_dialog.py
# -*- coding: utf-8 -*-
"""
A real, searchable table of drivers - not a lightweight dropdown, since
driver data is genuine, reusable, and already has its own table. Used
specifically at dispatch time, the real moment a driver gets involved.
Optional: dispatch can proceed without picking one (buyer pickup,
company vehicle) via "Dispatch Without Driver".
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QFormLayout
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


class DriverPickerDialog(QDialog):
    """exec_() then read .selected_driver_id / .selected_driver_name (both None if skipped)."""

    def __init__(self, parent=None, skip_label: str = "Dispatch Without Driver",
                hint_text: str = "Optional - not every dispatch has a third-party driver."):
        super().__init__(parent)
        self.selected_driver_id = None
        self.selected_driver_name = None

        self.setWindowTitle("Select Driver")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(620, 480)

        layout = QVBoxLayout(self)

        title = QLabel("Select Driver")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(hint_text)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Name / phone / vehicle number…")
        self.search_in.textChanged.connect(self._reload)
        search_row.addWidget(self.search_in, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Phone", "Vehicle Number"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self._select())
        layout.addWidget(self.table, 1)

        # ---- inline "new driver" form, collapsed by default ----
        self.new_driver_box = QVBoxLayout()
        new_driver_title = QLabel("+ New Driver")
        new_driver_title.setStyleSheet("font-weight: 700; padding-top: 6px;")
        self.new_driver_box.addWidget(new_driver_title)

        form = QFormLayout()
        self.new_name_in = QLineEdit()
        self.new_phone_in = QLineEdit()
        self.new_vehicle_in = QLineEdit()
        form.addRow("Name:", self.new_name_in)
        form.addRow("Phone:", self.new_phone_in)
        form.addRow("Vehicle Number:", self.new_vehicle_in)
        self.new_driver_box.addLayout(form)

        self.btn_create_driver = QPushButton("Create & Select")
        self.new_driver_box.addWidget(self.btn_create_driver)
        layout.addLayout(self.new_driver_box)

        footer = QHBoxLayout()
        self.btn_skip = QPushButton(skip_label)
        footer.addWidget(self.btn_skip)
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_select = QPushButton("Select")
        self.btn_select.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_select)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_select.clicked.connect(self._select)
        self.btn_skip.clicked.connect(self._skip)
        self.btn_create_driver.clicked.connect(self._create_driver)

        self._rows = []
        self._reload()

    def _reload(self, *_args):
        from database.services.driver_service import DriverService
        self._rows = DriverService.list_active(search=self.search_in.text())

        self.table.setRowCount(len(self._rows))
        for r, d in enumerate(self._rows):
            cells = [d["name"], d["phone"], d["vehicle_number"]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text or ""))
                it.setData(Qt.UserRole, d["id"])
                self.table.setItem(r, c, it)

    def _select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Select Driver", "Select a driver first, or use 'Dispatch Without Driver'.")
            return
        self.selected_driver_id = self._rows[row]["id"]
        self.selected_driver_name = self._rows[row]["name"]
        self.accept()

    def _skip(self):
        self.selected_driver_id = None
        self.selected_driver_name = None
        self.accept()

    def _create_driver(self):
        name = self.new_name_in.text().strip()
        if not name:
            QMessageBox.warning(self, "New Driver", "Enter a name first.")
            return

        from database.services.driver_service import DriverService
        try:
            driver_id = DriverService.create(
                name=name, created_by_user_id=state.require_user_id(),
                phone=self.new_phone_in.text().strip(),
                vehicle_number=self.new_vehicle_in.text().strip(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Create Failed", str(e))
            return

        self.selected_driver_id = driver_id
        self.selected_driver_name = name
        self.accept()