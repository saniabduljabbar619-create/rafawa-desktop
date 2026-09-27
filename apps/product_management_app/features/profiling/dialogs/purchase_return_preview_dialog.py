from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem
)

from database.session import get_session
from database.models.purchase_return import PurchaseReturn
from database.models.product import Product


class PurchaseReturnPreviewDialog(QDialog):

    def __init__(self, return_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Purchase Return #{return_id}")
        self.resize(650, 350)

        layout = QVBoxLayout(self)

        with get_session() as s:

            r = (
                s.query(
                    PurchaseReturn.created_at,
                    Product.name,
                    PurchaseReturn.qty_bags,
                    PurchaseReturn.qty_kg,
                    PurchaseReturn.unit_cost_snapshot,
                    PurchaseReturn.amount,
                    PurchaseReturn.reason
                )
                .join(Product, Product.id == PurchaseReturn.product_id)
                .filter(PurchaseReturn.id == return_id)
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