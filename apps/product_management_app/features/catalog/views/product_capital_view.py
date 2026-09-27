# apps/product_management_app/features/catalog/views/product_capital_view.py

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTableWidget,
    QTableWidgetItem, QFrame
)
from PySide2.QtCore import Qt

from database.services.product_analytics_service import (
    get_product_purchase_capital_summary_db
)
from shared.uix.layout.scrollable import make_scrollable_page


class ProductCapitalView(QWidget):
    """
    Product Capital Overview (Read-Only Analytics)

    Displays:
        - Total Bags Purchased
        - Total Purchased Quantity (kg for crops, bags for fertilizer)
        - Weighted Average Cost
        - Total Purchase Value (Historical Capital)
        - Category totals
        - Grand total capital
    """

    def __init__(self):
        super().__init__()

        layout = make_scrollable_page(self, margins=(12, 12, 12, 12), spacing=10)

        # ---------------------------------------------------
        # Toolbar
        # ---------------------------------------------------
        top = QHBoxLayout()
        layout.addLayout(top)

        top.addWidget(QLabel("Category:"))

        self.cat_filter = QComboBox()
        self.cat_filter.addItems(["All", "Crops", "Fertilizer"])
        top.addWidget(self.cat_filter)

        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)

        top.addStretch(1)

        # ---------------------------------------------------
        # Table
        # ---------------------------------------------------
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Product",
            "Category",
            "Total Bags",
            "Total Purchased Qty",
            "Avg Unit Cost",
            "Total Purchase Value"
        ])

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.table, 1)

        # ---------------------------------------------------
        # Footer Summary
        # ---------------------------------------------------
        footer = QFrame()
        footer.setObjectName("Card")

        f = QHBoxLayout(footer)
        f.setContentsMargins(12, 8, 12, 8)

        self.lbl_crops_total = QLabel("Crops Capital: ₦0.00")
        self.lbl_fert_total = QLabel("Fertilizer Capital: ₦0.00")
        self.lbl_grand_total = QLabel("Grand Capital: ₦0.00")

        self.lbl_grand_total.setStyleSheet("font-weight:800;")

        f.addWidget(self.lbl_crops_total)
        f.addSpacing(20)
        f.addWidget(self.lbl_fert_total)
        f.addStretch(1)
        f.addWidget(self.lbl_grand_total)

        layout.addWidget(footer)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.cat_filter.currentTextChanged.connect(self.refresh)

        self.refresh()

    # ---------------------------------------------------
    # Refresh Data
    # ---------------------------------------------------
    def refresh(self):
        rows = get_product_purchase_capital_summary_db()

        category = self.cat_filter.currentText().strip().lower()

        if category == "crops":
            rows = [r for r in rows if r["product_type"] == "crops"]
        elif category in ("fertilizer", "fertilizers"):
            rows = [r for r in rows if r["product_type"] == "fertilizer"]

        self.table.setRowCount(0)

        crops_total = 0.0
        fert_total = 0.0

        for r in rows:
            name = str(r.get("product_name") or "")
            ptype = str(r.get("product_type") or "")

            total_bags = float(r.get("total_bags") or 0.0)
            total_qty = float(r.get("total_qty") or 0.0)
            avg_cost = float(r.get("avg_unit_cost") or 0.0)
            total_value = float(r.get("total_purchase_value") or 0.0)

            if ptype == "crops":
                crops_total += total_value
            elif ptype == "fertilizer":
                fert_total += total_value

            display_qty = (
                f"{total_qty:,.2f} kg"
                if ptype == "crops"
                else f"{total_qty:,.0f} bags"
            )

            display_bags = f"{total_bags:,.0f}"

            row = self.table.rowCount()
            self.table.insertRow(row)

            cols = [
                name,
                ptype,
                display_bags,
                display_qty,
                f"{avg_cost:,.2f}",
                f"{total_value:,.2f}",
            ]

            for c, val in enumerate(cols):
                item = QTableWidgetItem(val)
                if c in (2, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, c, item)

        grand_total = crops_total + fert_total

        self.lbl_crops_total.setText(f"Crops Capital: ₦{crops_total:,.2f}")
        self.lbl_fert_total.setText(f"Fertilizer Capital: ₦{fert_total:,.2f}")
        self.lbl_grand_total.setText(f"Grand Capital: ₦{grand_total:,.2f}")