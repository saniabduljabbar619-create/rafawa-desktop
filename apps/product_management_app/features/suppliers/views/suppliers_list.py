# apps/product_management_app/features/suppliers/views/suppliers_list.py
# -*- coding: utf-8 -*-
"""
Suppliers list.

Reads and writes directly through SupplierProfileService (SQLAlchemy),
matching every other screen in the app. This USED to go through
suppliers/api.py (ApiClient -> httpx -> /api/suppliers), which nothing else
in the app relies on and which appears to have no server behind it - its own
fields (supplier_type, company_ref) do not even exist as columns on Party.
That's why the list previously showed nothing / errored, and why suppliers
created elsewhere (e.g. Quick Purchase's lazy-create) never appeared here:
two disconnected data paths reading two different pictures of "suppliers".
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QMessageBox, QDialog, QMenu
)
from PySide2.QtCore import Qt

from database.services.supplier_profile_service import SupplierProfileService
from apps.product_management_app.features.suppliers.views.supplier_form import SupplierFormDialog
from apps.product_management_app.features.suppliers.views.supplier_detail import SupplierProfileView


class SuppliersListPage(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("Suppliers")
        title.setObjectName("Title")
        title_row.addWidget(title)

        title_row.addStretch(1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_add = QPushButton("Add Supplier")
        self.btn_edit = QPushButton("Edit")
        self.btn_toggle = QPushButton("Toggle Active")
        self.btn_delete = QPushButton("Delete")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_add.clicked.connect(self.open_add)
        self.btn_edit.clicked.connect(self.open_edit)
        self.btn_toggle.clicked.connect(self.toggle_active)
        self.btn_delete.clicked.connect(self.delete_selected)

        title_row.addWidget(self.btn_refresh)
        title_row.addWidget(self.btn_add)
        title_row.addWidget(self.btn_edit)
        title_row.addWidget(self.btn_toggle)
        title_row.addWidget(self.btn_delete)

        layout.addLayout(title_row)

        # Search (client-side filter)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Name / phone / address")
        self.search.textChanged.connect(self.apply_filter)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        # Table - only real Party columns. (supplier_type / company_ref from
        # the old api-layer shape had nowhere real to live and were dropped.)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Phone", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.cellDoubleClicked.connect(self._open_profile)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table, 1)

        self._rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self.refresh()

    def refresh(self):
        try:
            data = SupplierProfileService.list_all()
        except Exception as e:
            QMessageBox.critical(self, "Suppliers", f"Could not load suppliers:\n{e}")
            return

        self._rows = data
        self.apply_filter()

    def apply_filter(self):
        q = (self.search.text() or "").strip().lower()
        rows = self._rows

        if q:
            def match(r: dict) -> bool:
                return (
                    str(r.get("name", "")).lower().find(q) >= 0
                    or str(r.get("phone", "")).lower().find(q) >= 0
                    or str(r.get("address", "")).lower().find(q) >= 0
                )
            rows = [r for r in rows if match(r)]

        self._visible_rows = rows
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(r.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(r.get("name", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(str(r.get("phone", "")) or ""))
            self.table.setItem(i, 3, QTableWidgetItem(str(r.get("status", ""))))

            for col in range(4):
                item = self.table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if col == 1 else Qt.AlignCenter))

    def _open_profile(self, row: int, _col: int):
        if row < 0 or row >= len(self._visible_rows):
            return
        sid = int(self._visible_rows[row].get("id") or 0)
        if sid <= 0:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Supplier Profile")
        dlg.resize(980, 720)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        try:
            lay.addWidget(SupplierProfileView(sid, parent=dlg))
        except Exception as e:
            QMessageBox.critical(self, "Supplier", str(e))
            return
        dlg.exec_()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        sup = self._selected_supplier()
        if not sup:
            return

        menu = QMenu(self)
        act_remove = menu.addAction("Remove from Supplier List")

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == act_remove:
            self.remove_from_supplier_list(sup)

    def remove_from_supplier_list(self, sup: dict):
        ok = QMessageBox.question(
            self,
            "Remove from Supplier List",
            f"Remove '{sup.get('name', '')}' from the Suppliers list? Their "
            f"purchase and payment history is kept, and they can be "
            f"converted back to a supplier later. This does not affect "
            f"their standing as a customer, if any.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            SupplierProfileService.remove_from_supplier_list(int(sup["id"]))
        except Exception as e:
            QMessageBox.critical(self, "Remove from Supplier List", str(e))
            return

        try:
            from apps.product_management_app.app_state import state
            state._load_customers_from_db()
        except Exception:
            pass

        self.refresh()

    def _selected_supplier(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._visible_rows):
            return None
        return self._visible_rows[row]

    def open_add(self):
        dlg = SupplierFormDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def open_edit(self):
        sup = self._selected_supplier()
        if not sup:
            QMessageBox.warning(self, "Select", "Select a supplier first.")
            return
        dlg = SupplierFormDialog(self, supplier=sup)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def toggle_active(self):
        sup = self._selected_supplier()
        if not sup:
            QMessageBox.warning(self, "Select", "Select a supplier first.")
            return

        cur = str(sup.get("status", "")).strip().lower() in ("active", "yes", "true")
        new_val = not cur
        try:
            SupplierProfileService.set_active(int(sup["id"]), new_val)
        except Exception as e:
            QMessageBox.critical(self, "Update Failed", str(e))
            return
        self.refresh()

    def delete_selected(self):
        sup = self._selected_supplier()
        if not sup:
            QMessageBox.warning(self, "Select", "Select a supplier first.")
            return

        confirm = QMessageBox.question(
            self, "Delete Supplier",
            f"Permanently delete \"{sup.get('name', '')}\"? This cannot be undone.\n\n"
            "If this supplier has ever appeared on any purchase, sale, "
            "payment, advance, or return, you'll be asked to confirm a "
            "second time before anything with real history is removed.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            SupplierProfileService.delete_supplier(int(sup["id"]))
        except Exception as e:
            # Blocked because real history exists - the error message
            # already names exactly what (purchases, payments, etc. and
            # how many of each). Escalate to a second, more explicit
            # warning rather than silently trying the destructive path.
            escalate = QMessageBox.warning(
                self, "This Supplier Has Real History",
                f"{e}\n\n"
                "Deleting anyway will PERMANENTLY erase every one of those "
                "records too - not just this supplier, but every purchase, "
                "payment, advance, and return ever recorded against them. "
                "There is no undo.\n\n"
                "Are you certain you want to delete everything?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if escalate != QMessageBox.Yes:
                return

            try:
                removed = SupplierProfileService.force_delete_supplier(int(sup["id"]))
            except Exception as e2:
                QMessageBox.critical(self, "Delete Failed", str(e2))
                return

            details = "\n".join(
                f"  {label.replace('_', ' ')}: {count}"
                for label, count in removed.items() if count > 0
            )
            QMessageBox.information(
                self, "Supplier and History Deleted",
                f"\"{sup.get('name', '')}\" and everything tied to them has "
                f"been permanently removed.\n\n{details or '  (no related records found)'}"
            )
            self.refresh()
            return

        QMessageBox.information(self, "Success", "Supplier deleted.")
        self.refresh()