# apps/product_management_app/features/catalog/views/product_inventory_capital_view.py

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTableWidget,
    QTableWidgetItem, QFrame
)
from PySide2.QtCore import Qt

from database.services.inventory_capital_service import (
    get_live_inventory_capital_summary_db
)
from shared.uix.layout.scrollable import make_scrollable_page


class ProductInventoryCapitalView(QWidget):
    """
    Live Inventory Capital

    Shows real-time capital tied in stock.
    """

    def __init__(self):
        super().__init__()

        layout = make_scrollable_page(self, margins=(12, 12, 12, 12), spacing=10)

        # Toolbar
        top = QHBoxLayout()
        layout.addLayout(top)

        top.addWidget(QLabel("Category:"))

        self.cat_filter = QComboBox()
        self.cat_filter.addItems(["All", "Crops", "Fertilizer"])
        top.addWidget(self.cat_filter)

        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)

        top.addStretch(1)

        # Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Product",
            "Category",
            "Net Bags",
            "Net KG",
            "Avg Cost",
            "Inventory Capital"
        ])

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.table, 1)

        # Footer
        footer = QFrame()
        f = QHBoxLayout(footer)
        f.setContentsMargins(12, 8, 12, 8)

        self.lbl_total = QLabel("Total Inventory Capital: ₦0.00")
        self.lbl_total.setStyleSheet("font-weight:800;")

        f.addStretch(1)
        f.addWidget(self.lbl_total)

        layout.addWidget(footer)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.cat_filter.currentTextChanged.connect(self.refresh)

        self.refresh()

    def refresh(self):
        rows = get_live_inventory_capital_summary_db()

        category = self.cat_filter.currentText().strip().lower()

        if category == "crops":
            rows = [r for r in rows if r["product_type"] == "crops"]
        elif category == "fertilizer":
            rows = [r for r in rows if r["product_type"] == "fertilizer"]

        self.table.setRowCount(0)

        grand_total = 0.0

        for r in rows:
            name = r["product_name"]
            ptype = r["product_type"]

            net_bags = float(r.get("net_bags") or 0)
            net_kg = float(r.get("net_kg") or 0)
            avg_cost = float(r.get("avg_unit_cost") or 0)

            if ptype == "crops":
                inventory_capital = net_kg * avg_cost
            else:
                inventory_capital = net_bags * avg_cost

            grand_total += inventory_capital

            row = self.table.rowCount()
            self.table.insertRow(row)

            cols = [
                name,
                ptype,
                f"{net_bags:,.0f}",
                f"{net_kg:,.2f}",
                f"{avg_cost:,.2f}",
                f"{inventory_capital:,.2f}",
            ]

            for c, val in enumerate(cols):
                item = QTableWidgetItem(val)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, c, item)

        self.lbl_total.setText(f"Total Inventory Capital: ₦{grand_total:,.2f}")