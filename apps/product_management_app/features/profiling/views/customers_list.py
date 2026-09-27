from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QMenu
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from database.services.supplier_profile_service import SupplierProfileService
from shared.uix.widgets.dialogs import CustomerFormDialog
from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
from shared.uix.layout.scrollable import make_scrollable_page


class CustomersListView(QWidget):
    """
    Registered customers only now - Walk-ins moved out entirely to their own
    dedicated screen (walkins_list.py). They were never meant to live inside
    this screen in the first place; a walk-in has no permanent ledger
    account, so mixing them into the same tabbed view as real registered
    customers was the source of the original "why is this walk-in showing
    up where it shouldn't" confusion. Isolated now, at the navigation level.
    """
    def __init__(self, mode="admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()

        layout = make_scrollable_page(self, margins=(12, 12, 12, 12))

        title = QLabel("Customers")
        title.setObjectName("Title")
        layout.addWidget(title)

        # Toolbar
        top = QHBoxLayout()
        layout.addLayout(top)

        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Name / Phone / Email …")
        top.addWidget(self.search_in, 1)

        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Full Name", "Phone", "Email", "Status"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.table, 1)

        # Actions
        actions = QHBoxLayout()
        layout.addLayout(actions)

        self.btn_add = QPushButton("Add Customer")
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        actions.addWidget(self.btn_add)
        actions.addWidget(self.btn_edit)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)

        # Cashiers may create customers but not edit or delete them.
        if self.mode == "cashier":
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)

        self.btn_add.clicked.connect(self.add_customer)
        self.btn_edit.clicked.connect(self.edit_customer)
        self.btn_delete.clicked.connect(self.delete_customer)

        self.table.cellDoubleClicked.connect(lambda _r, _c: self.open_profile())
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        self._open_profiles = []
        self.refresh()

    def selected_customer_id(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        try:
            return int(self.table.item(r, 0).text())
        except Exception:
            return None

    def refresh(self):
        q = self.search_in.text().strip().lower()

        def match(c):
            if not q:
                return True
            hay = f"{c.full_name} {c.phone} {c.email}".lower()
            return q in hay

        items = [c for c in state.customers if match(c)]
        self.table.setRowCount(0)
        for c in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            cols = [str(c.id), c.full_name, c.phone, c.email, c.status]
            for i, val in enumerate(cols):
                it = QTableWidgetItem(val)
                if i == 0:
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, i, it)

    def add_customer(self):
        dlg = CustomerFormDialog(self, title="Add Customer")
        if dlg.exec_() == dlg.Accepted:
            payload = dlg.payload()

            err = CustomerFormDialog.validate(payload)
            if err:
                QMessageBox.critical(self, "Validation Error", err)
                return

            if state.customer_phone_exists(payload["phone"]):
                QMessageBox.critical(self, "Validation Error", "Phone already exists.")
                return

            state.create_customer(**payload)
            self.refresh()

    def edit_customer(self):
        cid = self.selected_customer_id()
        if not cid:
            QMessageBox.information(self, "Edit Customer", "Select a row first.")
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            QMessageBox.warning(self, "Edit Customer", "Customer not found.")
            return

        dlg = CustomerFormDialog(self, title="Edit Customer", initial={
            "full_name": c.full_name,
            "phone": c.phone,
            "email": c.email,
            "address": c.address,
            "status": c.status,
            "notes": c.notes,
        })
        if dlg.exec_() == dlg.Accepted:
            payload = dlg.payload()

            err = CustomerFormDialog.validate(payload)
            if err:
                QMessageBox.critical(self, "Validation Error", err)
                return

            if state.customer_phone_exists(payload["phone"], exclude_id=c.id):
                QMessageBox.critical(self, "Validation Error", "Phone already exists.")
                return

            state.update_customer(c.id, **payload)
            self.refresh()

    def delete_customer(self):
        cid = self.selected_customer_id()
        if not cid:
            QMessageBox.information(self, "Delete Customer", "Select a row first.")
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        ok = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete customer '{c.full_name}' (Phone: {c.phone})?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok == QMessageBox.Yes:
            state.delete_customer(cid)
            self.refresh()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        cid = self.selected_customer_id()
        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        menu = QMenu(self)
        act_convert = menu.addAction("Convert to Supplier")
        act_convert.setEnabled(not c.is_supplier)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == act_convert:
            self.convert_to_supplier(c)

    def convert_to_supplier(self, c):
        ok = QMessageBox.question(
            self,
            "Convert to Supplier",
            f"Convert '{c.full_name}' to a supplier? They will keep appearing "
            f"in the Customers list and will also start appearing in the "
            f"Suppliers list.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            SupplierProfileService.convert_to_supplier(c.id)
        except Exception as e:
            QMessageBox.critical(self, "Convert to Supplier", str(e))
            return

        state._load_customers_from_db()
        self.refresh()
        QMessageBox.information(self, "Converted", f"'{c.full_name}' is now also a supplier.")

    def open_profile(self):
        cid = self.selected_customer_id()
        if not cid:
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        profile = CustomerProfileView(c, mode=self.mode)
        profile.setWindowTitle(f"Customer Profile - {c.full_name}")
        profile.resize(900, 560)

        self._open_profiles.append(profile)
        profile.destroyed.connect(lambda: self._cleanup_profile(profile))
        profile.show()

    def _cleanup_profile(self, w):
        try:
            self._open_profiles = [x for x in self._open_profiles if x is not w]
        except Exception:
            pass