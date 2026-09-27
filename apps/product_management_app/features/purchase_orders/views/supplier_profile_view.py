# apps/product_management_app/features/purchase_orders/views/supplier_profile_view.py
# -*- coding: utf-8 -*-
"""
The Supplier Profile - built fresh for this isolated context (a PO
supplier is its own entity, not a Party). One place to see everything
about this one supplier's relationship: their own info at top, then
every Purchase Order and every payment against them, tabbed below.
Reached by double-clicking a row on SuppliersView.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QWidget, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PySide2.QtCore import Qt

_STATUS_LABELS = {
    "draft": "Draft",
    "in_transit": "In Transit",
    "confirmed": "Confirmed",
    "cancelled": "Cancelled",
}


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class SupplierProfileView(QDialog):
    def __init__(self, supplier_id: int, parent=None, is_admin: bool = True):
        super().__init__(parent)
        self.supplier_id = int(supplier_id)
        self.is_admin = is_admin
        self._changed = False

        self.setWindowTitle("Supplier Profile")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(940, 680)

        layout = QVBoxLayout(self)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet("font-weight: 800; font-size: 18px;")
        layout.addWidget(self.header_label)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #555;")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # ---- Orders tab ----
        orders_tab = QWidget()
        ov = QVBoxLayout(orders_tab)

        orders_top = QHBoxLayout()
        orders_top.addStretch(1)
        self.btn_create_order = QPushButton("Create Order")
        orders_top.addWidget(self.btn_create_order)
        ov.addLayout(orders_top)
        if not self.is_admin:
            self.btn_create_order.setVisible(False)

        self.orders_table = QTableWidget(0, 7)
        self.orders_table.setHorizontalHeaderLabels(
            ["Order No.", "Status", "Marked In-Transit", "Confirmed", "Expected Delivery",
            "Final Amount", "Payment"])
        self.orders_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.orders_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.cellDoubleClicked.connect(self._open_order_detail)
        ov.addWidget(self.orders_table, 1)

        tabs.addTab(orders_tab, "Orders")

        # ---- Payments tab ----
        payments_tab = QWidget()
        pv = QVBoxLayout(payments_tab)

        self.payments_table = QTableWidget(0, 5)
        self.payments_table.setHorizontalHeaderLabels(
            ["Order No.", "Date", "Amount", "Method", "Status"])
        self.payments_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.payments_table.horizontalHeader().setStretchLastSection(True)
        self.payments_table.setAlternatingRowColors(True)
        pv.addWidget(self.payments_table, 1)

        tabs.addTab(payments_tab, "Payments")

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.btn_close.clicked.connect(self.accept)
        self.btn_create_order.clicked.connect(self._open_create_order)

        self._orders_cache = {}
        self._load()

    # ------------------------------------------------------------------
    def _load(self):
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService

        supplier = PurchaseOrderSupplierService.get(self.supplier_id)
        if not supplier:
            QMessageBox.critical(self, "Error", "Supplier not found.")
            self.reject()
            return

        self.header_label.setText(supplier["name"])
        meta_bits = []
        if supplier["address"]:
            meta_bits.append(supplier["address"])
        if supplier["phone"]:
            meta_bits.append(supplier["phone"])
        if supplier["contact_person"]:
            meta_bits.append(f"Contact: {supplier['contact_person']}")
        self.meta_label.setText("  |  ".join(meta_bits) if meta_bits else "")

        self._load_orders()
        self._load_payments()

    def _load_orders(self):
        from database.services.purchase_order_service import PurchaseOrderService

        orders = PurchaseOrderService.list_for_supplier(self.supplier_id)
        self._orders_cache = {o["purchase_order_id"]: o for o in orders}

        self.orders_table.setRowCount(0)
        for o in orders:
            r = self.orders_table.rowCount()
            self.orders_table.insertRow(r)

            dispatched_s = o["dispatched_at"].strftime("%Y-%m-%d %H:%M") if o["dispatched_at"] else ""
            confirmed_s = o["confirmed_at"].strftime("%Y-%m-%d %H:%M") if o["confirmed_at"] else ""
            expected_s = o["expected_delivery_date"].strftime("%Y-%m-%d") if o["expected_delivery_date"] else ""
            final_s = _money(o["final_total_amount"]) if o["final_total_amount"] is not None else ""
            payment_s = o["payment_status"].capitalize() if o["payment_status"] else ""

            cells = [
                o["order_no"], _STATUS_LABELS.get(o["status"], o["status"]),
                dispatched_s, confirmed_s, expected_s, final_s, payment_s,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, o["purchase_order_id"])
                if c >= 5:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.orders_table.setItem(r, c, item)

    def _load_payments(self):
        from database.services.order_payment_service import OrderPaymentService

        self.payments_table.setRowCount(0)
        for order_id, o in self._orders_cache.items():
            if o["status"] != "confirmed":
                continue
            payments = OrderPaymentService.list_payments("purchase_order", order_id)
            for p in payments:
                r = self.payments_table.rowCount()
                self.payments_table.insertRow(r)
                when = p["created_at"].strftime("%Y-%m-%d %H:%M") if p["created_at"] else ""
                cells = [o["order_no"], when, _money(p["amount"]),
                        p["method"].capitalize(), p["status"].capitalize()]
                for c, text in enumerate(cells):
                    self.payments_table.setItem(r, c, QTableWidgetItem(str(text)))

    # ------------------------------------------------------------------
    def _open_create_order(self):
        from apps.product_management_app.features.purchase_orders.dialogs.purchase_order_create_dialog import (
            PurchaseOrderCreateDialog,
        )
        dlg = PurchaseOrderCreateDialog(parent=self)
        # Pre-fix the supplier to this profile, since we're already
        # inside it - no picker needed for a brand new order here.
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
        supplier = PurchaseOrderSupplierService.get(self.supplier_id)
        if supplier:
            dlg._supplier_id = self.supplier_id
            dlg.supplier_display.setText(supplier["name"])

        if dlg.exec_() == dlg.Accepted:
            self._changed = True
            self._load()

    def _open_order_detail(self, row, _col):
        item = self.orders_table.item(row, 0)
        if not item:
            return
        order_id = item.data(Qt.UserRole)
        if not order_id:
            return

        from apps.product_management_app.features.purchase_orders.dialogs.purchase_order_detail_dialog import (
            PurchaseOrderDetailDialog,
        )
        dlg = PurchaseOrderDetailDialog(purchase_order_id=order_id, parent=self, is_admin=self.is_admin)
        dlg.exec_()
        self._changed = True
        self._load()