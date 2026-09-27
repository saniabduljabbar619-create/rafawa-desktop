# apps/product_management_app/features/purchase_orders/dialogs/purchase_order_confirm_dialog.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QDoubleSpinBox, QMessageBox, QHeaderView
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class PurchaseOrderConfirmDialog(QDialog):
    """
    We've weighed/counted and assessed what actually arrived - enter it
    here. Each line uses its own correct unit (kg for crops, bags for
    fertilizer), matching its product_type_snapshot.

    Rejected auto-fills as the remainder (received quantity minus
    accepted) - the natural way to think about it - but stays editable
    by hand. The server-side service independently re-validates the
    same rule regardless of what this screen shows - never just trusted
    from here. This is the ONLY point stock genuinely changes for a
    Purchase Order - accepted goods enter Available Stock, rejected
    goods never do at all.
    """
    def __init__(self, purchase_order_id: int, parent=None):
        super().__init__(parent)
        self.purchase_order_id = int(purchase_order_id)
        self.setWindowTitle("Confirm Purchase Order")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(940, 560)

        layout = QVBoxLayout(self)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(self.header_label)

        hint = QLabel(
            "Enter what actually arrived for each line - kg for crops, "
            "bags for fertilizer. The final amount always uses the real "
            "received quantity and price, never the expected figures. "
            "Accepted goods enter stock; rejected goods never do."
        )
        hint.setStyleSheet("color: #666;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Product", "Unit", "Ordered", "Expected Price", "Received Qty",
            "Received Price", "Accepted", "Rejected", "Final Amount",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setMinimumHeight(280)
        layout.addWidget(self.table, 1)

        self.total_label = QLabel("Total final amount: \u20a60.00")
        self.total_label.setStyleSheet("font-weight: 700; font-size: 13px; padding: 4px 0;")
        layout.addWidget(self.total_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save_progress = QPushButton("Save Progress")
        self.btn_confirm = QPushButton("Confirm Order")
        self.btn_confirm.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_save_progress)
        footer.addWidget(self.btn_confirm)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save_progress.clicked.connect(self._save_progress)
        self.btn_confirm.clicked.connect(self._confirm)

        self._rows = []  # [{item_id, is_fert, qty_spin, price_spin, accepted_spin, rejected_spin, final_item}]
        self._load()

    def _load(self):
        from database.services.purchase_order_service import PurchaseOrderService
        try:
            detail = PurchaseOrderService.get_order_detail(self.purchase_order_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.reject()
            return

        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
        supplier = PurchaseOrderSupplierService.get(detail["supplier_id"])
        supplier_name = supplier["name"] if supplier else "Unknown supplier"

        self.header_label.setText(f"{detail['order_no']} \u2014 {supplier_name}")

        self.table.setRowCount(0)
        for it in detail["items"]:
            is_fert = _is_fert(it["product_type_snapshot"])
            ordered = it["ordered_qty_bags"] if is_fert else it["ordered_weight_kg"]
            unit = "bags" if is_fert else "kg"

            # Prefer whatever confirmation progress was already saved for
            # this line - only fall back to the "ordered quantity" guess
            # when nothing has been recorded for it yet.
            saved_qty = it["received_qty_bags"] if is_fert else it["received_weight_kg"]
            saved_accepted = it["accepted_qty_bags"] if is_fert else it["accepted_weight_kg"]
            saved_rejected = it["rejected_qty_bags"] if is_fert else it["rejected_weight_kg"]
            start_qty = saved_qty if saved_qty is not None else ordered
            start_price = it["received_price"] if it["received_price"] is not None else it["expected_price"]
            start_accepted = saved_accepted if saved_accepted is not None else ordered
            start_rejected = saved_rejected if saved_rejected is not None else 0.0

            r = self.table.rowCount()
            self.table.insertRow(r)

            self.table.setItem(r, 0, QTableWidgetItem(it["product_name"]))
            self.table.setItem(r, 1, QTableWidgetItem(unit))

            ordered_item = QTableWidgetItem(f"{ordered:,.2f}")
            ordered_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 2, ordered_item)

            expected_price_item = QTableWidgetItem(f"{it['expected_price']:,.2f}")
            expected_price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 3, expected_price_item)

            qty_spin = QDoubleSpinBox()
            qty_spin.setDecimals(2)
            qty_spin.setMaximum(1_000_000.0)
            qty_spin.setValue(start_qty)
            self.table.setCellWidget(r, 4, qty_spin)

            price_spin = QDoubleSpinBox()
            price_spin.setDecimals(2)
            price_spin.setMaximum(1_000_000.0)
            price_spin.setValue(start_price)
            self.table.setCellWidget(r, 5, price_spin)

            accepted_spin = QDoubleSpinBox()
            accepted_spin.setDecimals(2)
            accepted_spin.setMaximum(1_000_000.0)
            accepted_spin.setValue(start_accepted)
            self.table.setCellWidget(r, 6, accepted_spin)

            rejected_spin = QDoubleSpinBox()
            rejected_spin.setDecimals(2)
            rejected_spin.setMaximum(1_000_000.0)
            rejected_spin.setValue(start_rejected)
            self.table.setCellWidget(r, 7, rejected_spin)

            final_item = QTableWidgetItem("0.00")
            final_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            final_item.setFlags(final_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 8, final_item)

            row_state = {
                "item_id": it["item_id"], "is_fert": is_fert,
                "qty_spin": qty_spin, "price_spin": price_spin,
                "accepted_spin": accepted_spin, "rejected_spin": rejected_spin,
                "final_item": final_item,
            }
            self._rows.append(row_state)

            qty_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_qty_or_accepted_changed(rs))
            accepted_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_qty_or_accepted_changed(rs))
            price_spin.valueChanged.connect(lambda _v, rs=row_state: self._recompute_row(rs))
            rejected_spin.valueChanged.connect(lambda _v, rs=row_state: self._recompute_row(rs))

            self._recompute_row(row_state)

    def _on_qty_or_accepted_changed(self, row_state):
        """Rejected auto-fills as the remainder - received quantity minus
        accepted - but stays editable by hand afterward."""
        qty = float(row_state["qty_spin"].value())
        accepted = float(row_state["accepted_spin"].value())
        remainder = max(qty - accepted, 0.0)
        row_state["rejected_spin"].blockSignals(True)
        row_state["rejected_spin"].setValue(remainder)
        row_state["rejected_spin"].blockSignals(False)
        self._recompute_row(row_state)

    def _recompute_row(self, row_state):
        accepted = float(row_state["accepted_spin"].value())
        price = float(row_state["price_spin"].value())
        final_amount = accepted * price
        row_state["final_item"].setText(f"{final_amount:,.2f}")
        self._recompute_total()

    def _recompute_total(self):
        total = 0.0
        for rs in self._rows:
            accepted = float(rs["accepted_spin"].value())
            price = float(rs["price_spin"].value())
            total += accepted * price
        self.total_label.setText(f"Total final amount: \u20a6{total:,.2f}")

    def _save_progress(self):
        if not self._rows:
            QMessageBox.warning(self, "Save Progress", "Nothing to save.")
            return

        confirmations = []
        for rs in self._rows:
            qty = float(rs["qty_spin"].value())
            price = float(rs["price_spin"].value())
            accepted = float(rs["accepted_spin"].value())
            rejected = float(rs["rejected_spin"].value())
            is_fert = rs["is_fert"]

            conf = {"item_id": rs["item_id"], "received_price": price}
            if is_fert:
                conf["received_qty_bags"] = qty
                conf["accepted_qty_bags"] = accepted
                conf["rejected_qty_bags"] = rejected
            else:
                conf["received_weight_kg"] = qty
                conf["accepted_weight_kg"] = accepted
                conf["rejected_weight_kg"] = rejected
            confirmations.append(conf)

        from database.services.purchase_order_service import PurchaseOrderService
        try:
            PurchaseOrderService.save_confirmation_progress(
                self.purchase_order_id, item_confirmations=confirmations,
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", str(e))
            return

        QMessageBox.information(
            self, "Saved",
            "Progress saved - come back any time to finish once more "
            "verification is done. The order stays In Transit until "
            "it's genuinely confirmed."
        )
        self.accept()

    def _confirm(self):
        if not self._rows:
            QMessageBox.warning(self, "Confirm Order", "Nothing to confirm.")
            return

        confirmations = []
        for rs in self._rows:
            qty = float(rs["qty_spin"].value())
            price = float(rs["price_spin"].value())
            accepted = float(rs["accepted_spin"].value())
            rejected = float(rs["rejected_spin"].value())
            is_fert = rs["is_fert"]

            if qty <= 0:
                QMessageBox.warning(self, "Confirm Order", "Every line needs a received quantity greater than zero.")
                return
            if abs((accepted + rejected) - qty) > 0.01:
                QMessageBox.warning(
                    self, "Confirm Order",
                    f"Accepted ({accepted:,.2f}) + Rejected ({rejected:,.2f}) must "
                    f"equal Received Qty ({qty:,.2f}) for every line."
                )
                return

            conf = {"item_id": rs["item_id"], "received_price": price}
            if is_fert:
                conf["received_qty_bags"] = qty
                conf["accepted_qty_bags"] = accepted
                conf["rejected_qty_bags"] = rejected
            else:
                conf["received_weight_kg"] = qty
                conf["accepted_weight_kg"] = accepted
                conf["rejected_weight_kg"] = rejected
            confirmations.append(conf)

        answer = QMessageBox.question(
            self, "Confirm Purchase Order",
            "This will finalize the order: accepted quantities enter "
            "Available Stock, and rejected quantities never enter stock "
            "at all. This cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return

        from database.services.purchase_order_service import PurchaseOrderService
        try:
            PurchaseOrderService.confirm(
                self.purchase_order_id,
                item_confirmations=confirmations,
                confirmed_by_user_id=state.require_user_id(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Confirm Failed", str(e))
            return

        QMessageBox.information(self, "Confirmed", "Purchase Order finalized.")
        self.accept()