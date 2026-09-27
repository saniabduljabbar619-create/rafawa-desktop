# apps/product_management_app/features/profiling/dialogs/purchase_price_adjustment_dialog.py
# -*- coding: utf-8 -*-
"""
Records a price correction on a specific purchase - e.g. a supplier
coming back after delivery asking for more per kg than originally
agreed. Works whether the purchase is already fully paid (a fresh
balance appears) or still partial/unpaid (it adds to what's already
outstanding).

The operator enters the rate the way the negotiation actually happens -
"20 more per 1kg" - rather than having to compute the total themselves;
the total is derived automatically from the purchase's own, real,
already-recorded weight.
"""
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QTextEdit, QPushButton, QMessageBox, QHeaderView, QTableWidget, QTableWidgetItem
)
from PySide2.QtCore import Qt


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class PurchasePriceAdjustmentDialog(QDialog):
    def __init__(self, party_id: int, party_name: str, created_by_user_id: int, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.party_name = party_name
        self.created_by_user_id = int(created_by_user_id)
        self._selected_weight_kg = 0.0  # the selected purchase's own, real recorded weight

        self.setWindowTitle("Purchase Price Adjustment")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(800, 580)

        layout = QVBoxLayout(self)

        title = QLabel(f"Price Adjustment - {party_name}")
        title.setStyleSheet("font-size: 15px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel(
            "For when the agreed price on a purchase changes after the "
            "fact - e.g. the supplier asks for more per kg once you're "
            "already settled. Works whether this purchase is already "
            "fully paid or not - select it below."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Reference", "Product", "Date", "Total", "Balance", "Status"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.table, 1)

        self.selected_label = QLabel("Select a purchase above first.")
        self.selected_label.setStyleSheet("color: #666; font-size: 11px; padding: 4px 0;")
        self.selected_label.setWordWrap(True)
        layout.addWidget(self.selected_label)

        # ---- weight/price input, matching how the negotiation actually
        # happens: "X more per Y kg" - the total gets derived, not typed ----
        form = QHBoxLayout()
        form.addWidget(QLabel("Weight (kg):"))
        self.weight_unit_in = QDoubleSpinBox()
        self.weight_unit_in.setDecimals(2)
        self.weight_unit_in.setRange(0.01, 100_000.0)
        self.weight_unit_in.setValue(1.0)
        self.weight_unit_in.valueChanged.connect(self._recompute_total)
        form.addWidget(self.weight_unit_in)

        form.addWidget(QLabel("Price (\u20a6, negative for a decrease):"))
        self.price_in = QDoubleSpinBox()
        self.price_in.setDecimals(2)
        self.price_in.setRange(-1_000_000.0, 1_000_000.0)
        self.price_in.valueChanged.connect(self._recompute_total)
        form.addWidget(self.price_in)
        layout.addLayout(form)

        self.total_label = QLabel("Total adjustment: \u20a60.00")
        self.total_label.setStyleSheet("font-weight: 700; font-size: 13px; padding: 4px 0;")
        layout.addWidget(self.total_label)

        layout.addWidget(QLabel("Reason:"))
        self.reason_in = QTextEdit()
        self.reason_in.setPlaceholderText(
            "e.g. Supplier renegotiated after delivery: 350/kg -> 370/kg."
        )
        self.reason_in.setMaximumHeight(80)
        layout.addWidget(self.reason_in)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_apply = QPushButton("Apply Adjustment")
        self.btn_apply.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_apply)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self._apply)

        self._rows = []
        self.refresh()

    def refresh(self):
        from database.services.purchase_adjustment_service import PurchaseAdjustmentService
        self._rows = PurchaseAdjustmentService.list_supplier_purchases(self.party_id)

        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            info = PurchaseAdjustmentService.get_purchase_weight_and_products(row["purchase_id"])
            row["_weight_kg"] = info["total_weight_kg"]
            row["_product_summary"] = info["product_summary"]

            when = row.get("created_at")
            when_s = when.strftime("%Y-%m-%d") if when else ""
            cells = [
                row["purchase_no"], info["product_summary"] or "\u2014", when_s,
                _money(row["total_amount"]), _money(row["balance"]), row["status"].capitalize(),
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setData(Qt.UserRole, row["purchase_id"])
                if c >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)

    def _on_row_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            self.selected_label.setText("Select a purchase above first.")
            self._selected_weight_kg = 0.0
            self._recompute_total()
            return
        r = self._rows[row]
        self._selected_weight_kg = float(r.get("_weight_kg") or 0.0)

        product_bit = f"{r['_product_summary']} - " if r.get("_product_summary") else ""
        self.selected_label.setText(
            f"Selected: {r['purchase_no']} ({product_bit}{self._selected_weight_kg:,.2f} kg "
            f"recorded) - current balance {_money(r['balance'])} of {_money(r['net_total'])} total"
        )
        self._recompute_total()

    def _recompute_total(self, *_args):
        weight_unit = float(self.weight_unit_in.value())
        price = float(self.price_in.value())
        if weight_unit <= 0 or self._selected_weight_kg <= 0:
            self.total_label.setText("Total adjustment: \u20a60.00")
            return
        # price per weight_unit kg, applied across the purchase's own,
        # real recorded weight - e.g. 20 per 1kg on 1,000kg = 20,000.
        rate_per_kg = price / weight_unit
        total = rate_per_kg * self._selected_weight_kg
        self.total_label.setText(f"Total adjustment: {_money(total)}")

    def _apply(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Price Adjustment", "Select a purchase first.")
            return

        r = self._rows[row]
        weight_unit = float(self.weight_unit_in.value())
        price = float(self.price_in.value())
        selected_weight_kg = float(r.get("_weight_kg") or 0.0)

        if selected_weight_kg <= 0:
            QMessageBox.warning(
                self, "Price Adjustment",
                "This purchase has no recorded weight (kg) to base the "
                "adjustment on."
            )
            return
        if price == 0:
            QMessageBox.warning(self, "Price Adjustment", "Enter a price other than zero.")
            return

        rate_per_kg = price / weight_unit
        computed_amount = round(rate_per_kg * selected_weight_kg, 2)

        purchase_id = r["purchase_id"]
        reason = self.reason_in.toPlainText().strip()

        from database.services.purchase_adjustment_service import PurchaseAdjustmentService
        try:
            PurchaseAdjustmentService.apply_adjustment(
                purchase_id=purchase_id, amount=computed_amount,
                created_by_user_id=self.created_by_user_id, reason=reason,
            )
        except Exception as e:
            QMessageBox.critical(self, "Adjustment Failed", str(e))
            return

        QMessageBox.information(
            self, "Applied",
            f"Adjustment of {_money(computed_amount)} applied "
            f"({_money(price)} per {weight_unit:,.2f}kg \u00d7 "
            f"{selected_weight_kg:,.2f}kg). The purchase's balance now "
            f"reflects it - you can pay the difference from the payment "
            f"dialog."
        )
        self.price_in.setValue(0.0)
        self.reason_in.clear()
        self.refresh()