# apps/product_management_app/features/sales_orders/views/companies_view.py
# -*- coding: utf-8 -*-
"""
Companies - the new primary entry point for the Sales Order module.
One row per company, not a flat list of every order from every company
mixed together. Double-click opens that company's own profile, where
every order and payment for them lives.
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


class CompaniesView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()
        self.is_admin = self.mode == "admin"

        root = make_scrollable_page(self, margins=(12, 12, 12, 12))

        title = QLabel("Companies")
        title.setObjectName("Title")
        root.addWidget(title)

        subtitle = QLabel(
            "Selling crops to companies via Sales Orders - open a "
            "company's own profile to see every order and payment "
            "with them, and to create a new order."
        )
        subtitle.setStyleSheet("color: #666;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        top = QHBoxLayout()
        root.addLayout(top)

        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Company name…")
        top.addWidget(self.search_in, 1)

        self.btn_new_company = QPushButton("+ New Company")
        top.addWidget(self.btn_new_company)
        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)
        if not self.is_admin:
            self.btn_new_company.setVisible(False)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Company", "Contact Person", "Phone", "Active Orders", "Outstanding Balance"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(360)
        self.table.cellDoubleClicked.connect(self._open_profile)
        root.addWidget(self.table, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        self.btn_new_company.clicked.connect(self._new_company)

        self.refresh()

    def refresh(self):
        from database.services.company_service import CompanyService
        from database.services.sales_order_service import SalesOrderService

        companies = CompanyService.search(self.search_in.text(), limit=200)

        self.table.setRowCount(0)
        for c in companies:
            orders = SalesOrderService.list_for_company(c["id"])
            active_count = sum(1 for o in orders if o["status"] in ("draft", "in_transit"))
            outstanding = sum(
                float(o["payment_balance"] or 0) for o in orders
                if o["status"] == "confirmed" and o["payment_balance"] is not None
            )

            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [c["name"], c["contact_person"], c["phone"],
                    str(active_count), _money(outstanding)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, c["id"])
                if col >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, col, item)

    def _open_profile(self, row, _col):
        item = self.table.item(row, 0)
        if not item:
            return
        company_id = item.data(Qt.UserRole)
        if not company_id:
            return

        from apps.product_management_app.features.sales_orders.views.company_profile_view import (
            CompanyProfileView,
        )
        dlg = CompanyProfileView(company_id=company_id, parent=self, is_admin=self.is_admin)
        dlg.exec_()
        self.refresh()

    def _new_company(self):
        from apps.product_management_app.features.sales_orders.dialogs.company_picker_dialog import (
            CompanyPickerDialog,
        )
        dlg = CompanyPickerDialog(parent=self)
        if dlg.exec_() == dlg.Accepted and dlg.selected_company_id:
            self.refresh()