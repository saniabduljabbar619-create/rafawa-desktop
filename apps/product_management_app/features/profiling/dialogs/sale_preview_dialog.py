from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem
)

from database.session import get_session
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.models.product import Product


class SalePreviewDialog(QDialog):

    def __init__(self, sale_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Sale Invoice #{sale_id}")
        self.resize(700, 400)

        layout = QVBoxLayout(self)

        with get_session() as s:

            sale = s.query(Sale).get(sale_id)

            header = QLabel(
                f"Date: {sale.completed_at} | Status: {sale.status}"
            )
            layout.addWidget(header)

            items = (
                s.query(
                    Product.name,
                    SaleItem.qty_bags,
                    SaleItem.total_weight_kg,
                    SaleItem.unit_sale_rate,
                    SaleItem.line_total
                )
                .join(Product, Product.id == SaleItem.product_id)
                .filter(SaleItem.sale_id == sale_id)
                .all()
            )

        table = QTableWidget(len(items), 5)

        table.setHorizontalHeaderLabels([
            "Product",
            "Bags",
            "KG",
            "Rate",
            "Amount"
        ])

        for r, row in enumerate(items):

            table.setItem(r, 0, QTableWidgetItem(str(row[0])))
            table.setItem(r, 1, QTableWidgetItem(str(row[1])))
            table.setItem(r, 2, QTableWidgetItem(str(row[2])))
            table.setItem(r, 3, QTableWidgetItem(str(row[3])))
            table.setItem(r, 4, QTableWidgetItem(str(row[4])))

        layout.addWidget(table)