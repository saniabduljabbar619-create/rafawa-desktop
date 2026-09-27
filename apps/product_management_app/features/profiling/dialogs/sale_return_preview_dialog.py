from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem
)

from database.session import get_session
from database.models.sale_return import SaleReturn
from database.models.product import Product


class SaleReturnPreviewDialog(QDialog):

    def __init__(self, return_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Sale Return #{return_id}")
        self.resize(650, 350)

        layout = QVBoxLayout(self)

        with get_session() as s:

            r = (
                s.query(
                    SaleReturn.created_at,
                    Product.name,
                    SaleReturn.qty_bags,
                    SaleReturn.qty_kg,
                    SaleReturn.unit_price_snapshot,
                    SaleReturn.amount,
                    SaleReturn.reason
                )
                .join(Product, Product.id == SaleReturn.product_id)
                .filter(SaleReturn.id == return_id)
                .first()
            )

        header = QLabel(f"Date: {r[0]}")
        layout.addWidget(header)

        reason = QLabel(f"Reason: {r[6]}")
        layout.addWidget(reason)

        table = QTableWidget(1, 5)

        table.setHorizontalHeaderLabels([
            "Product",
            "Bags",
            "KG",
            "Rate",
            "Amount"
        ])

        table.setItem(0, 0, QTableWidgetItem(str(r[1])))
        table.setItem(0, 1, QTableWidgetItem(str(r[2])))
        table.setItem(0, 2, QTableWidgetItem(str(r[3])))
        table.setItem(0, 3, QTableWidgetItem(str(r[4])))
        table.setItem(0, 4, QTableWidgetItem(str(r[5])))

        layout.addWidget(table)