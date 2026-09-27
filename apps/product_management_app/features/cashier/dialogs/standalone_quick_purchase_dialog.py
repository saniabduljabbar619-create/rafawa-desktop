# apps/product_management_app/features/cashier/dialogs/standalone_quick_purchase_dialog.py
# -*- coding: utf-8 -*-
"""
Standalone "Quick Purchase" button on the Sales Console.

Separate from the auto-triggered shortfall interlude at checkout (which stays,
for the genuine partial-shortfall case: a product HAS stock, the customer
just wants more than what's on hand). This dialog exists for the OTHER case -
a product sitting at zero stock, invisible in the normal Add Item picker,
that the customer wants right now.

Only enabled once a customer is selected (gates on the Sales Console's own
customer_cb / walk-in name, matching the client's explicit requirement).

Per-row supplier assignment: unlike the in-cart interlude (one supplier per
round), EVERY checked product here gets its OWN supplier picker, so 10
missing products can be split across 5 different suppliers in one submission.
On confirm, checked rows are grouped by supplier and committed as SEPARATE
purchase records - one per distinct supplier used - exactly matching how a
purchase belongs to one supplier by nature.
"""
from __future__ import annotations

from typing import Dict, Any, List

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QCheckBox, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QWidget, QInputDialog
)

from database.services.sales_service import SaleService
from database.services.quick_purchase_service import QuickPurchaseService
from database.services.supplier_profile_service import SupplierProfileService
from apps.product_management_app.app_state import state

NAIRA = "\u20a6"
NEW_SUPPLIER = "__new__"


class StandaloneQuickPurchaseDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Purchase - Buy Missing Stock")
        self.setModal(True)
        self.resize(920, 660)

        # (product_type, is_fert) per selected product_id, so submission
        # knows which basis (bags vs kg) each row was entered in.
        self._row_meta: Dict[int, Dict[str, Any]] = {}
        self.new_cart_lines: List[Any] = []   # filled on successful submit
        self.pending_quick_purchases: List[Dict[str, Any]] = []   # planned, NOT yet committed - see _submit()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Buy products that are currently out of stock")
        title.setStyleSheet("font-size:15px; font-weight:800;")
        root.addWidget(title)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Category:"))
        self.category_cb = QComboBox()
        self.category_cb.addItem("All", "")
        self.category_cb.addItem("Crops", "crops")
        self.category_cb.addItem("Fertilizer", "fertilizer")
        self.category_cb.currentIndexChanged.connect(self._refresh_products)
        search_row.addWidget(self.category_cb)

        search_row.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Product name or SKU...")
        self.search.textChanged.connect(self._refresh_products)
        search_row.addWidget(self.search, 1)
        root.addLayout(search_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Buy?", "Product", "Current Stock", "Qty to Buy",
             "Dealer Cost Each", "Supplier"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        note = QLabel(
            "Check the products you want to buy, enter how many and from "
            "which supplier. Different rows can go to different suppliers - "
            "each supplier used creates its own purchase record.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#6b7280; font-size:11px;")
        root.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Buy Selected")
        self.btn_ok.setStyleSheet(
            "QPushButton { background:#27ae60; color:#fff; border:none;"
            " border-radius:8px; padding:8px 20px; font-weight:800; }")
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._submit)

        self._refresh_products()

    # ------------------------------------------------------------------
    def _refresh_products(self):
        self.table.setRowCount(0)
        self._row_meta.clear()
        try:
            products = SaleService.list_products_for_sale(
                q=self.search.text(), limit=500, include_zero_stock=True)
        except Exception as e:
            QMessageBox.warning(self, "Products", f"Could not load products:\n{e}")
            return

        cat = self.category_cb.currentData()
        if cat:
            products = [p for p in products
                       if str(p.get("product_type") or "").strip().lower() == cat]

        self.table.setRowCount(len(products))
        for i, p in enumerate(products):
            ptype = str(p.get("product_type") or "").strip().lower()
            is_fert = ptype in ("fertilizer", "fertilizers")
            self._row_meta[i] = {
                "product_id": int(p["id"]),
                "product_type": ptype,
                "is_fert": is_fert,
                "name": p["name"],
            }

            chk = QCheckBox()
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setAlignment(Qt.AlignCenter)
            cl.addWidget(chk)
            self.table.setCellWidget(i, 0, cell)
            self._row_meta[i]["chk"] = chk

            name_item = QTableWidgetItem(f"{p['name']} ({p['sku']})")
            self.table.setItem(i, 1, name_item)

            stock_txt = (f"{p['stock']:g} bag(s)" if is_fert
                        else f"{p['stock']:g} kg")
            self.table.setItem(i, 2, QTableWidgetItem(stock_txt))

            if is_fert:
                qty_spin = QSpinBox()
                qty_spin.setRange(0, 1_000_000)
                qty_spin.setValue(0)
            else:
                qty_spin = QDoubleSpinBox()
                qty_spin.setDecimals(2)
                qty_spin.setRange(0.0, 1_000_000_000.0)
                qty_spin.setValue(0.0)
            self.table.setCellWidget(i, 3, qty_spin)
            self._row_meta[i]["qty_spin"] = qty_spin

            cost_spin = QDoubleSpinBox()
            cost_spin.setDecimals(2)
            cost_spin.setRange(0.0, 1_000_000_000.0)
            cost_spin.setPrefix(NAIRA)
            self.table.setCellWidget(i, 4, cost_spin)
            self._row_meta[i]["cost_spin"] = cost_spin

            supplier_cb = QComboBox()
            self._populate_supplier_combo(supplier_cb)
            supplier_cb.currentIndexChanged.connect(
                lambda idx, cb=supplier_cb: self._on_supplier_pick(cb))
            self.table.setCellWidget(i, 5, supplier_cb)
            self._row_meta[i]["supplier_cb"] = supplier_cb

    def _populate_supplier_combo(self, combo: QComboBox):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("+ New supplier...", NEW_SUPPLIER)
        try:
            for sup in SupplierProfileService.active_suppliers():
                label = f"{sup['name']} ({sup['phone']})" if sup.get("phone") \
                    else sup["name"]
                combo.addItem(label, int(sup["id"]))
        except Exception:
            pass
        combo.blockSignals(False)

    def _on_supplier_pick(self, combo: QComboBox):
        if combo.currentData() != NEW_SUPPLIER:
            return
        name, ok = QInputDialog.getText(self, "New Supplier", "Supplier name:")
        name = (name or "").strip()
        if not ok or not name:
            combo.setCurrentIndex(0)
            return
        phone, _ = QInputDialog.getText(self, "New Supplier",
                                        "Phone (optional, press OK to skip):")
        try:
            sid = SupplierProfileService.get_or_lazy_create_supplier(
                name, (phone or "").strip())
        except Exception as e:
            QMessageBox.critical(self, "New Supplier", str(e))
            combo.setCurrentIndex(0)
            return
        self._populate_supplier_combo(combo)
        for i in range(combo.count()):
            if combo.itemData(i) == sid:
                combo.setCurrentIndex(i)
                return

    # ------------------------------------------------------------------
    def _submit(self):
        checked_rows = [i for i, meta in self._row_meta.items()
                        if meta["chk"].isChecked()]
        if not checked_rows:
            QMessageBox.information(self, "Quick Purchase",
                                    "Check at least one product to buy.")
            return

        # validate every checked row before planning anything
        for i in checked_rows:
            meta = self._row_meta[i]
            qty = meta["qty_spin"].value()
            if qty <= 0:
                QMessageBox.warning(self, "Quick Purchase",
                                    f"Enter a quantity to buy for {meta['name']}.")
                return
            if meta["cost_spin"].value() <= 0:
                QMessageBox.warning(self, "Quick Purchase",
                                    f"Enter the dealer cost for {meta['name']}.")
                return
            sid = meta["supplier_cb"].currentData()
            if not sid or sid == NEW_SUPPLIER:
                QMessageBox.warning(self, "Quick Purchase",
                                    f"Pick a supplier for {meta['name']}.")
                return

        # group by supplier - each distinct supplier becomes its OWN
        # purchase once actually committed later
        by_supplier: Dict[int, List[int]] = {}
        for i in checked_rows:
            sid = int(self._row_meta[i]["supplier_cb"].currentData())
            by_supplier.setdefault(sid, []).append(i)

        # Deliberately NOT committed here - only planned, matching
        # _maybe_quick_purchase()'s exact reasoning in cashier_sales.py:
        # stock must never change for goods bought to fulfil a sale that
        # hasn't happened yet. Committing here would leave stock topped
        # up for however long checkout takes, even if the operator
        # cancels the sale entirely. self.pending_quick_purchases is
        # read and actually committed by the caller, immediately before
        # the sale itself - back to back, not held open.
        self.pending_quick_purchases: List[Dict[str, Any]] = []
        purchased_lines = []

        for sid, row_indices in by_supplier.items():
            short_lines = []
            for i in row_indices:
                meta = self._row_meta[i]
                is_fert = meta["is_fert"]
                qty = meta["qty_spin"].value()
                short_lines.append({
                    "product_id": meta["product_id"],
                    "product_type": meta["product_type"],
                    "qty_bags": int(qty) if is_fert else 0,
                    "total_weight_kg": 0.0 if is_fert else float(qty),
                    "unit_cost": float(meta["cost_spin"].value()),
                })

            self.pending_quick_purchases.append({
                "supplier_party_id": sid,
                "short_lines": short_lines,
            })

            for i in row_indices:
                meta = self._row_meta[i]
                purchased_lines.append({
                    "product_id": meta["product_id"],
                    "product_type": meta["product_type"],
                    "name": meta["name"],
                    "qty": meta["qty_spin"].value(),
                    "unit_cost": float(meta["cost_spin"].value()),
                })

        self.new_cart_lines = purchased_lines
        QMessageBox.information(
            self, "Quick Purchase",
            f"Added {len(purchased_lines)} item(s) across "
            f"{len(by_supplier)} supplier(s) to the cart below. They'll "
            f"actually be bought - and enter stock - only once this sale "
            f"is completed at checkout.")

        self.accept()