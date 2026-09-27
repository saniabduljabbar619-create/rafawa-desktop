# apps/product_management_app/features/purchase_orders/views/suppliers_view.py
# -*- coding: utf-8 -*-
"""
Suppliers - the primary entry point for the Purchase Order module.
One row per supplier, not a flat list of every order from every
supplier mixed together. Double-click opens that supplier's own
profile, where every order and payment with them lives.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide2.QtCore import Qt

from shared.uix.layout.scrollable import make_scrollable_page


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class SuppliersView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()
        self.is_admin = self.mode == "admin"

        root = make_scrollable_page(self, margins=(12, 12, 12, 12))

        title = QLabel("Suppliers")
        title.setObjectName("Title")
        root.addWidget(title)

        subtitle = QLabel(
            "Buying crops/fertilizer from suppliers via Purchase Orders - "
            "open a supplier's own profile to see every order and "
            "payment with them, and to create a new order."
        )
        subtitle.setStyleSheet("color: #666;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        top = QHBoxLayout()
        root.addLayout(top)

        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Supplier name…")
        top.addWidget(self.search_in, 1)

        self.btn_new_supplier = QPushButton("+ New Supplier")
        top.addWidget(self.btn_new_supplier)
        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)
        if not self.is_admin:
            self.btn_new_supplier.setVisible(False)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Supplier", "Contact Person", "Phone", "Active Orders", "Outstanding Balance"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(360)
        self.table.cellDoubleClicked.connect(self._open_profile)
        root.addWidget(self.table, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        self.btn_new_supplier.clicked.connect(self._new_supplier)

        self.refresh()

    def refresh(self):
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
        from database.services.purchase_order_service import PurchaseOrderService

        suppliers = PurchaseOrderSupplierService.search(self.search_in.text(), limit=200)

        self.table.setRowCount(0)
        for sup in suppliers:
            orders = PurchaseOrderService.list_for_supplier(sup["id"])
            active_count = sum(1 for o in orders if o["status"] in ("draft", "in_transit"))
            outstanding = sum(
                float(o["payment_balance"] or 0) for o in orders
                if o["status"] == "confirmed" and o["payment_balance"] is not None
            )

            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [sup["name"], sup["contact_person"], sup["phone"],
                    str(active_count), _money(outstanding)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, sup["id"])
                if col >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, col, item)

    def _open_profile(self, row, _col):
        item = self.table.item(row, 0)
        if not item:
            return
        supplier_id = item.data(Qt.UserRole)
        if not supplier_id:
            return

        from apps.product_management_app.features.purchase_orders.views.supplier_profile_view import (
            SupplierProfileView,
        )
        dlg = SupplierProfileView(supplier_id=supplier_id, parent=self, is_admin=self.is_admin)
        dlg.exec_()
        self.refresh()

    def _new_supplier(self):
        from apps.product_management_app.features.purchase_orders.dialogs.supplier_picker_dialog import (
            SupplierPickerDialog,
        )
        dlg = SupplierPickerDialog(parent=self)
        if dlg.exec_() == dlg.Accepted and dlg.selected_supplier_id:
            self.refresh()