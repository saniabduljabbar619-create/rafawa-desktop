from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem
)

from database.session import get_session
from database.models.sale import Sale
from database.services.sale_adjustment_service import SaleAdjustmentService


class SalePreviewDialog(QDialog):

    def __init__(self, sale_id, parent=None):
        super().__init__(parent)
        self.sale_id = int(sale_id)
        self.edited = False

        self.setWindowTitle(f"Sale Invoice #{sale_id}")
        self.resize(700, 400)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.header = QLabel("")
        self.btn_edit = QPushButton("Edit prices")
        self.btn_edit.clicked.connect(self.edit_prices)
        top.addWidget(self.header)
        top.addStretch(1)
        top.addWidget(self.btn_edit)
        layout.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Product",
            "Bags",
            "KG",
            "Rate",
            "Amount"
        ])
        layout.addWidget(self.table)
        self._load()

    def _load(self):
        with get_session() as s:
            sale = s.query(Sale).get(self.sale_id)
            if sale:
                nums = SaleAdjustmentService._sale_balance(s, sale)
                self.header.setText(
                    f"Date: {sale.completed_at} | Status: {sale.status} | Total: \u20a6{nums['net_total']:,.2f} | Balance: \u20a6{nums['balance']:,.2f}"
                )
            lines = SaleAdjustmentService._line_prices(s, self.sale_id)

        self.table.setRowCount(len(lines))
        for r, l in enumerate(lines):
            self.table.setItem(r, 0, QTableWidgetItem(str(l["product"])))
            self.table.setItem(r, 1, QTableWidgetItem(str(l["bags"])))
            self.table.setItem(r, 2, QTableWidgetItem(f"{l['kg']:,.2f}"))
            self.table.setItem(r, 3, QTableWidgetItem(f"{l['rate']:,.2f}"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{l['amount']:,.2f}"))

    def edit_prices(self) -> bool:
        from apps.product_management_app.features.profiling.dialogs.purchase_price_edit_dialog import (
            open_price_editor,
        )
        if open_price_editor(self, "sale", self.sale_id, ref=f"SAL-{self.sale_id}"):
            self.edited = True
            self._load()
            if self.parent() is not None and hasattr(self.parent(), "refresh_all"):
                self.parent().refresh_all()
            return True
        return False
