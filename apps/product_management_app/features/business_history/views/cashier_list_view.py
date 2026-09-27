# apps/product_management_app/features/business_history/views/cashier_list_view.py
# -*- coding: utf-8 -*-
"""
Aggregated Business History - entry screen. Lists every operator (both
Cashiers and Admins) in the app; double-clicking one opens
CashierBusinessHistoryDialog, which shows that operator's individual
purchases or sales (picked from a dropdown), one row per transaction.

Also carries a Global ID search: type a document reference (e.g.
"SAL-123") to find who created it and which party it involves, without
knowing which operator to open first.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView,
)
from shared.uix.layout.scrollable import make_scrollable_page

from database.services.user_admin_service import UserAdminService


class CashierHistoryListView(QWidget):
    def __init__(self):
        super().__init__()

        root = make_scrollable_page(self, margins=(16, 16, 16, 16), spacing=12)

        header = QHBoxLayout()
        title = QLabel("Aggregated Business History")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_refresh = QPushButton("Refresh")
        header.addWidget(self.btn_refresh)
        root.addLayout(header)

        hint = QLabel("Double-click an operator to view their purchases and sales, individually.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(hint)

        # Global ID search - independent of the operator table below;
        # searches doc numbers (SAL-.../PUR-...) across EVERY operator at
        # once, so an admin doesn't need to know which cashier to open
        # first just to trace one reference.
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Find transaction:"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("e.g. SAL-123 or PUR-45")
        self.search_box.returnPressed.connect(self.search_transaction)
        search_row.addWidget(self.search_box, 1)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self.search_transaction)
        search_row.addWidget(self.btn_search)
        root.addLayout(search_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Username", "Role", "Active", "Created"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        self.footer = QLabel("")
        self.footer.setStyleSheet("color: #666;")
        root.addWidget(self.footer)

        self.btn_refresh.clicked.connect(self.reload)
        self.table.cellDoubleClicked.connect(self.open_cashier)

        self.reload()

    def reload(self):
        try:
            rows = UserAdminService.list_users(role_names=["cashier", "admin"])
        except Exception as e:
            QMessageBox.critical(self, "Load Failed", str(e))
            return

        self.table.setRowCount(len(rows))
        for r, (uid, username, role, is_active, created_txt) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(str(uid)))
            self.table.setItem(r, 1, QTableWidgetItem(username))
            self.table.setItem(r, 2, QTableWidgetItem((role or "").capitalize()))
            self.table.setItem(r, 3, QTableWidgetItem("Yes" if is_active else "No"))
            self.table.setItem(r, 4, QTableWidgetItem(created_txt))

        self.footer.setText(f"Loaded {len(rows)} operator(s).")

    def search_transaction(self):
        query = self.search_box.text().strip()
        if not query:
            return

        from database.services.drawer_service import DrawerService
        try:
            rows = DrawerService.search_transactions(query)
        except Exception as e:
            QMessageBox.critical(self, "Search Failed", str(e))
            return

        if not rows:
            QMessageBox.information(self, "No Results", f'No transaction found matching "{query}".')
            return

        # A single exact hit (case-insensitive) skips the results list and
        # opens the real detail dialog straight away - the common case of
        # typing a full, exact reference.
        exact = [r for r in rows if r.get("doc_no", "").strip().lower() == query.lower()]
        if len(exact) == 1:
            self._open_detail(exact[0])
            return

        from apps.product_management_app.features.business_history.dialogs.transaction_search_results_dialog import (
            TransactionSearchResultsDialog,
        )
        TransactionSearchResultsDialog(query, rows, parent=self).exec_()

    def _open_detail(self, row: dict):
        kind = row.get("kind")
        doc_id = row.get("id")

        from apps.product_management_app.features.suppliers.views.supplier_activity_dialogs import (
            SupplierSaleDetailDialog, SupplierPurchaseDetailDialog,
        )
        try:
            if kind == "sale":
                dlg = SupplierSaleDetailDialog(doc_id, parent=self)
            else:
                dlg = SupplierPurchaseDetailDialog(doc_id, parent=self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Detail Failed", str(e))

    def open_cashier(self, row: int, _col: int):
        uid_item = self.table.item(row, 0)
        name_item = self.table.item(row, 1)
        if not uid_item or not name_item:
            return
        uid = int(uid_item.text() or "0")
        if uid <= 0:
            return

        from apps.product_management_app.features.business_history.dialogs.cashier_business_history_dialog import (
            CashierBusinessHistoryDialog,
        )
        CashierBusinessHistoryDialog(user_id=uid, username=name_item.text(), parent=self).exec_()