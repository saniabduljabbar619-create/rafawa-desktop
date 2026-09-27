# apps/product_management_app/features/catalog/views/products_list.py
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state, Product
from shared.uix.widgets.dialogs import ProductFormDialog
from shared.uix.layout.scrollable import make_scrollable_page


class ProductDialog(QWidget):
    """
    Minimal inline dialog using QMessageBox + inputs would be messy;
    so we keep it simple by using a small modal QDialog-like pattern later.
    For now, we implement Add/Edit via a compact custom dialog in shared/uix/widgets/dialogs.py next step.
    """
    pass


class ProductsListView(QWidget):
    def __init__(self):
        super().__init__()
        self.categories = ["All", "Crops", "Fertilizer"]

        layout = make_scrollable_page(self, margins=(12, 12, 12, 12))

        # Toolbar
        top = QHBoxLayout()
        layout.addLayout(top)

        top.addWidget(QLabel("Search:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Name / SKU / Category…")
        top.addWidget(self.search_in, 1)

        top.addWidget(QLabel("Category:"))
        self.cat_filter = QComboBox()
        self.categories = ["All", "Crops", "Fertilizer"]
        self.cat_filter.addItems(self.categories)
        top.addWidget(self.cat_filter)

        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)

        # Table
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "ID", "Name", "SKU", "Type",
            "Purchase Price", "Sale Price",
            "Qty (Bags)", "Total Weight (KG)",
            "Status"
        ])

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        # Actions
        actions = QHBoxLayout()
        layout.addLayout(actions)

        self.btn_add = QPushButton("Add")
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        actions.addWidget(self.btn_add)
        actions.addWidget(self.btn_edit)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        self.cat_filter.currentTextChanged.connect(self.refresh)

        # For now: simple actions placeholders
        self.btn_add.clicked.connect(self.add_product)
        self.btn_edit.clicked.connect(self.edit_product)

        self.btn_delete.clicked.connect(self.delete_product)

        self.refresh()

    def selected_id(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        try:
            return int(self.table.item(r, 0).text())
        except Exception:
            return None

    def refresh(self):
        q = (self.search_in.text() or "").strip()
        cat = self.cat_filter.currentText().strip().lower()
        pt = None
        if cat == "crops":
            pt = "crops"
        elif cat in ("fertilizer", "fertilizers"):
            pt = "fertilizer"
        # else pt stays None for ALL

        rows = state.list_products_db(q=self.search_in.text(), product_type=pt)


        # DB rows must include:
        # id, name, sku, product_type, default_cost_rate, default_sale_rate, net_bags, net_kg, is_active
        rows = state.list_products_db(q=q, product_type=pt)

        self.table.setRowCount(0)

        for r in rows:
            rid = int(r.get("id") or 0)
            name = str(r.get("name") or "")
            sku = str(r.get("sku") or "")
            ptype = str(r.get("product_type") or "")

            cost = float(r.get("default_cost_rate") or 0.0)
            sale = float(r.get("default_sale_rate") or 0.0)

            net_bags = float(r.get("net_bags") or 0.0)
            net_kg = float(r.get("net_kg") or 0.0)

            status = "active" if int(r.get("is_active") or 0) == 1 else "inactive"

            cols = [
                str(rid),
                name,
                sku,
                ptype,
                f"{cost:,.2f}",
                f"{sale:,.2f}",
                f"{net_bags:,.0f}",
                f"{net_kg:,.2f}",
                status,
            ]

            row = self.table.rowCount()
            self.table.insertRow(row)

            for c, val in enumerate(cols):
                it = QTableWidgetItem(val)
                if c in (0, 6, 7):  # id, bags, kg center-ish readability
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, c, it)

    def add_product(self):
        dlg = ProductFormDialog(self, title="Add Product", categories=self.categories)
        if dlg.exec_() != dlg.Accepted:
            return

        payload = dlg.payload()

        # BACKWARD COMPAT:
        # old dialog returns price/stock/status/category/sku
        # new dialog should return: name, product_type, sku(optional), status(optional), default_cost_rate/sale(optional)

        # minimal enforcement:
        name = (payload.get("name") or "").strip()
        if not name:
            ProductFormDialog.error(self, "Name is required.")
            return

        # if dialog still doesn't have product_type yet, default to crops for now
        pt = (payload.get("product_type") or "").strip().lower() or "crops"
        if pt not in ("crops", "fertilizer"):
            pt = "crops"

        db_payload = {
            "name": payload["name"],
            "product_type": payload["product_type"],
            "sku": payload.get("sku") or "",
            "unit_label": "kg",  # keep default; later we can add it to dialog if needed
            "status": payload.get("status") or "active",
            "default_cost_rate": payload.get("default_cost_rate"),
            "default_sale_rate": payload.get("default_sale_rate"),
        }


        try:
            state.create_product_db(db_payload)
        except Exception as e:
            ProductFormDialog.error(self, str(e))
            return

        self.refresh()



    def edit_product(self):
        pid = self.selected_id()
        if not pid:
            QMessageBox.information(self, "Edit Product", "Select a product row first.")
            return

        # We need the current DB row to prefill dialog
        rows = state.list_products_with_stock_db(q="", category="All")
        cur = next((x for x in rows if int(x["id"]) == int(pid)), None)
        if not cur:
            QMessageBox.warning(self, "Edit Product", "Product not found.")
            return

        dlg = ProductFormDialog(
            self,
            title="Edit Product",
            categories=self.categories,
            initial={
                "name": cur.get("name") or "",
                "sku": cur.get("sku") or "",
                "product_type": cur.get("product_type") or "crops",
                "status": "active" if int(cur.get("is_active") or 0) == 1 else "inactive",
                "default_cost_rate": float(cur.get("default_cost_rate") or 0.0),
                "default_sale_rate": float(cur.get("default_sale_rate") or 0.0),

                # IMPORTANT: pass current stock
                "qty_bags": float(cur.get("net_bags") or 0.0),
                "total_weight_kg": float(cur.get("net_kg") or 0.0),
            }
        )
        if dlg.exec_() != dlg.Accepted:
            return

        payload = dlg.payload()

        new_bags = float(payload.get("qty_bags") or 0)
        new_kg = float(payload.get("total_weight_kg") or 0)

        current_bags = float(cur.get("net_bags") or 0)
        current_kg = float(cur.get("net_kg") or 0)

        bags_diff = new_bags - current_bags
        kg_diff = new_kg - current_kg

        ptype = cur.get("product_type")

        if ptype == "fertilizer":
            kg_diff = 0

        name = (payload.get("name") or "").strip()
        if not name:
            ProductFormDialog.error(self, "Name is required.")
            return

        db_payload = {
            "name": name,
            "sku": (payload.get("sku") or "").strip(),
            "unit_label": (payload.get("unit_label") or "kg"),
            "status": (payload.get("status") or "active"),
            "default_cost_rate": payload.get("default_cost_rate", payload.get("cost_price")),
            "default_sale_rate": payload.get("default_sale_rate", payload.get("price")),
        }

        try:
            state.update_product_db(pid, db_payload)

            # Inventory adjustment
            if abs(bags_diff) > 0 or abs(kg_diff) > 0:
                state.adjust_product_inventory_db(
                    product_id=pid,
                    bags_diff=bags_diff,
                    weight_diff=kg_diff
                )
        except Exception as e:
            ProductFormDialog.error(self, str(e))
            return

        self.refresh()

    
    def delete_product(self):
        pid = self.selected_id()
        if not pid:
            QMessageBox.information(self, "Delete", "Select a row first.")
            return

        ok = QMessageBox.question(
            self,
            "Confirm",
            "Deactivate this product? (History will be preserved)",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            state.delete_product_db(pid)
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
            return

        self.refresh()