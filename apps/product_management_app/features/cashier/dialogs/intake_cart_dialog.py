# apps/product_management_app/features/cashier/dialogs/intake_cart_dialog.py
from __future__ import annotations

from typing import Optional

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


def capture_intake_line(parent, intake_type: str) -> Optional[dict]:
    """
    Ask for ONE intake line (product, bags, kg, price) with the standard intake dialog.
    Returns it shaped for PurchaseService.record()'s "items", or None when cancelled/invalid.
    Shared by the intake cart and the goods-swap dialog.
    """
    from shared.uix.widgets.dialogs import DeliveryIntakeDialog

    intake_type = "fertilizer" if str(intake_type).strip().lower() == "fertilizer" else "crops"
    title = "Crops Intake" if intake_type == "crops" else "Fertilizer Intake"
    dlg = DeliveryIntakeDialog(parent, title=title, initial_bags=1)
    dlg.ledger_in.setCurrentText("Crops" if intake_type == "crops" else "Fertilizers")

    if dlg.exec_() != dlg.Accepted:
        return None

    payload = dlg.payload()

    bags = int(payload.get("qty_bags") or payload.get("bags") or 0)
    if bags <= 0:
        QMessageBox.warning(parent, "Item", "Bags must be > 0.")
        return None

    product = str(payload.get("product") or "").strip()
    if not product:
        QMessageBox.warning(parent, "Item", "Product is required.")
        return None

    rate = float(payload.get("rate_per_kg") or 0.0)
    total_kg = float(payload.get("total_kg") or 0.0)

    if intake_type == "fertilizer":
        if rate <= 0:
            QMessageBox.warning(parent, "Item", "Price Per Bag must be > 0.")
            return None
        total_kg = 0.0
    else:
        if total_kg <= 0 or rate <= 0:
            QMessageBox.warning(parent, "Item", "Total KG and Price Per Kg must be > 0.")
            return None

    sku = str(payload.get("sku") or "").strip() or state.make_intake_sku(intake_type, product)

    return {
        "product_name": product,
        "sku": sku,
        "product_type": intake_type,
        "qty_bags": bags,
        "total_weight_kg": total_kg,
        "unit_cost": rate,
    }


class IntakeCartDialog(QDialog):
    """
    Step 2 - Cart.

    One source (chosen in IntakeStartDialog) can now bring in as many
    different products as the operator needs in one sitting - e.g. Masar,
    Dawa, Wake, Waken Soya all delivered together - instead of forcing a
    separate "New Intake" round-trip (and a separate purchase record,
    payment prompt, and receipt) per product.

    Each "+ Add Item" click opens the existing single-product
    DeliveryIntakeDialog to capture one line; the line is only added to
    the cart shown here, nothing is recorded yet. Lines can be removed
    before finalizing. "Finalize Purchase" is the only action that
    touches the database - the whole cart is then recorded as ONE
    purchase (PurchaseService.record's native multi-item support), so it
    gets one purchase reference, one advance/payment prompt, and one
    receipt.
    """

    def __init__(self, parent=None, intake_type: str = "crops", source_label: str = ""):
        super().__init__(parent)
        self.intake_type = "fertilizer" if str(intake_type).strip().lower() == "fertilizer" else "crops"
        self._items: list[dict] = []

        self.setModal(True)
        self.resize(700, 440)
        self.setWindowTitle("Intake Cart")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        root.addLayout(header)

        kind_label = "Fertilizer" if self.intake_type == "fertilizer" else "Crops"
        title = QLabel(f"Cart — {source_label or 'Selected source'} ({kind_label})")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_add_item = QPushButton("+ Add Item")
        self.btn_add_item.setObjectName("PrimaryButton")
        header.addWidget(self.btn_add_item)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Product", "SKU", "Bags", "Qty (KG)", "Price"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.total_label = QLabel("Total: 0.00")
        self.total_label.setAlignment(Qt.AlignRight)
        root.addWidget(self.total_label)

        row = QHBoxLayout()
        root.addLayout(row)
        self.btn_remove_item = QPushButton("Remove Selected")
        row.addWidget(self.btn_remove_item)
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_finalize = QPushButton("Finalize Purchase")
        self.btn_finalize.setObjectName("PrimaryButton")
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_finalize)

        self.btn_add_item.clicked.connect(self._add_item)
        self.btn_remove_item.clicked.connect(self._remove_selected)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_finalize.clicked.connect(self._finalize)

    def items(self) -> list[dict]:
        """
        Cart lines, already shaped to drop straight into
        PurchaseService.record()'s "items" list (via
        AppState.record_intake_multi_db).
        """
        return list(self._items)

    def _add_item(self):
        line = capture_intake_line(self, self.intake_type)
        if line is not None:
            self._items.append(line)
            self._refresh_table()

    def _remove_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._items):
            del self._items[row]
            self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(0)
        total = 0.0

        for it in self._items:
            row = self.table.rowCount()
            self.table.insertRow(row)

            if it["product_type"] == "fertilizer":
                line_total = it["qty_bags"] * it["unit_cost"]
                kg_display = "—"
            else:
                line_total = it["total_weight_kg"] * it["unit_cost"]
                kg_display = f"{it['total_weight_kg']:,.2f}"

            total += line_total

            vals = [
                it["product_name"],
                it["sku"],
                str(it["qty_bags"]),
                kg_display,
                f"{it['unit_cost']:,.2f}",
            ]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c in (2, 3, 4):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, c, cell)

        self.total_label.setText(f"Total: {total:,.2f}")

    def _finalize(self):
        if not self._items:
            QMessageBox.warning(self, "Cart", "Add at least one product before finalizing.")
            return
        self.accept()