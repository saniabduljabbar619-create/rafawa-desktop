from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem
from database.session import get_session
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.product import Product


class PurchasePreviewDialog(QDialog):

    def __init__(self, purchase_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Purchase #{purchase_id}")
        self.resize(700, 400)

        layout = QVBoxLayout(self)

        with get_session() as s:

            p = s.query(Purchase).get(purchase_id)

            header = QLabel(
                f"Date: {p.completed_at} | Status: {p.status}"
            )

            layout.addWidget(header)

            items = (
                s.query(
                    Product.name,
                    PurchaseItem.qty_bags,
                    PurchaseItem.total_weight_kg,
                    PurchaseItem.unit_cost_rate,
                    PurchaseItem.line_total
                )
                .join(Product, Product.id == PurchaseItem.product_id)
                .filter(PurchaseItem.purchase_id == purchase_id)
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