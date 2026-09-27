# apps/product_management_app/features/sales_orders/dialogs/sales_order_confirm_dialog.py
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


class SalesOrderConfirmDialog(QDialog):
    """
    The company has weighed/counted and assessed the goods - enter what
    they actually confirm here. Each line uses its own correct unit
    (kg for crops, bags for fertilizer), matching its product_type_
    snapshot - the column headers make clear which applies per row via
    the "Unit" column, since a single order can mix both.

    Crops are dual-tracked, same convention as every other line-item
    table in this app: kg drives price and stock, but the sack count is
    real too, so a Bags spinbox sits next to each of Company Qty/
    Accepted/Rejected for crop rows. Fertilizer is bags-only already
    (that's its primary unit), so those columns stay blank/disabled
    there.

    Rejected auto-fills as the remainder (company quantity minus
    accepted) - the natural way to think about it - but stays editable
    by hand. Same auto-fill applies to the crop Bags pair. The server-
    side service independently re-validates both, never just trusted
    from here.
    """
    def __init__(self, sales_order_id: int, parent=None):
        super().__init__(parent)
        self.sales_order_id = int(sales_order_id)
        self.setWindowTitle("Confirm Sales Order")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(1180, 560)

        layout = QVBoxLayout(self)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(self.header_label)

        hint = QLabel(
            "Enter what the company actually confirms for each line - kg "
            "for crops, bags for fertilizer. Crop lines also get a Bags "
            "column next to each figure (sack count, for the record) - "
            "kg is still what drives the final amount, never the "
            "expected (\"My Price\") figures."
        )
        hint.setStyleSheet("color: #666;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels([
            "Product", "Unit", "Dispatched", "My Price",
            "Company Qty", "Company Bags", "Company Price",
            "Accepted", "Accepted Bags", "Rejected", "Rejected Bags",
            "Final Amount",
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

        self._rows = []  # [{item_id, is_fert, weight_spin, price_spin, accepted_spin, rejected_spin, final_item}]
        self._load()

    def _load(self):
        from database.services.sales_order_service import SalesOrderService
        try:
            detail = SalesOrderService.get_order_detail(self.sales_order_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.reject()
            return

        from database.services.company_service import CompanyService
        company = CompanyService.get(detail["buyer_company_id"])
        buyer_name = company["name"] if company else "Unknown buyer"

        self.header_label.setText(f"{detail['order_no']} \u2014 {buyer_name}")

        self.table.setRowCount(0)
        for it in detail["items"]:
            is_fert = _is_fert(it["product_type_snapshot"])
            dispatched = it["dispatched_qty_bags"] if is_fert else it["dispatched_weight_kg"]
            unit = "bags" if is_fert else "kg"

            # Prefer whatever confirmation progress was already saved for
            # this line - only fall back to the "dispatched quantity" guess
            # when nothing has been recorded for it yet.
            saved_qty = it["company_qty_bags"] if is_fert else it["company_weight_kg"]
            saved_accepted = it["accepted_qty_bags"] if is_fert else it["accepted_weight_kg"]
            saved_rejected = it["rejected_qty_bags"] if is_fert else it["rejected_weight_kg"]
            start_qty = saved_qty if saved_qty is not None else dispatched
            start_price = it["company_price"] if it["company_price"] is not None else it["my_price"]
            start_accepted = saved_accepted if saved_accepted is not None else dispatched
            start_rejected = saved_rejected if saved_rejected is not None else 0.0

            # Crops-only secondary bag track - kg (above) stays what's
            # authoritative; this is purely the sack-count record.
            start_bags_qty = (it["company_qty_bags"] if it["company_qty_bags"] is not None
                              else it["dispatched_qty_bags"]) if not is_fert else 0.0
            start_bags_accepted = it["accepted_qty_bags"] if not is_fert and it["accepted_qty_bags"] is not None else start_bags_qty
            start_bags_rejected = it["rejected_qty_bags"] if not is_fert and it["rejected_qty_bags"] is not None else 0.0

            r = self.table.rowCount()
            self.table.insertRow(r)

            self.table.setItem(r, 0, QTableWidgetItem(it["product_name"]))
            self.table.setItem(r, 1, QTableWidgetItem(unit))

            dispatched_item = QTableWidgetItem(f"{dispatched:,.2f}")
            dispatched_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 2, dispatched_item)

            my_price_item = QTableWidgetItem(f"{it['my_price']:,.2f}")
            my_price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 3, my_price_item)

            qty_spin = QDoubleSpinBox()
            qty_spin.setDecimals(2)
            qty_spin.setMaximum(1_000_000.0)
            qty_spin.setValue(start_qty)
            self.table.setCellWidget(r, 4, qty_spin)

            bags_qty_spin = self._make_bags_cell(r, 5, is_fert, start_bags_qty)

            price_spin = QDoubleSpinBox()
            price_spin.setDecimals(2)
            price_spin.setMaximum(1_000_000.0)
            price_spin.setValue(start_price)
            self.table.setCellWidget(r, 6, price_spin)

            accepted_spin = QDoubleSpinBox()
            accepted_spin.setDecimals(2)
            accepted_spin.setMaximum(1_000_000.0)
            accepted_spin.setValue(start_accepted)
            self.table.setCellWidget(r, 7, accepted_spin)

            bags_accepted_spin = self._make_bags_cell(r, 8, is_fert, start_bags_accepted)

            rejected_spin = QDoubleSpinBox()
            rejected_spin.setDecimals(2)
            rejected_spin.setMaximum(1_000_000.0)
            rejected_spin.setValue(start_rejected)
            self.table.setCellWidget(r, 9, rejected_spin)

            bags_rejected_spin = self._make_bags_cell(r, 10, is_fert, start_bags_rejected)

            final_item = QTableWidgetItem("0.00")
            final_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            final_item.setFlags(final_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 11, final_item)

            row_state = {
                "item_id": it["item_id"], "is_fert": is_fert,
                "qty_spin": qty_spin, "price_spin": price_spin,
                "accepted_spin": accepted_spin, "rejected_spin": rejected_spin,
                "bags_qty_spin": bags_qty_spin, "bags_accepted_spin": bags_accepted_spin,
                "bags_rejected_spin": bags_rejected_spin,
                "final_item": final_item,
            }
            self._rows.append(row_state)

            qty_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_qty_or_accepted_changed(rs))
            accepted_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_qty_or_accepted_changed(rs))
            price_spin.valueChanged.connect(lambda _v, rs=row_state: self._recompute_row(rs))
            rejected_spin.valueChanged.connect(lambda _v, rs=row_state: self._recompute_row(rs))

            if not is_fert:
                bags_qty_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_bags_qty_or_accepted_changed(rs))
                bags_accepted_spin.valueChanged.connect(lambda _v, rs=row_state: self._on_bags_qty_or_accepted_changed(rs))

            self._recompute_row(row_state)

    def _make_bags_cell(self, row: int, col: int, is_fert: bool, value: float):
        """Crop rows get a live Bags spinbox; fertilizer rows show a
        disabled dash, since bags is already that row's primary unit
        (no second, redundant track needed there)."""
        if is_fert:
            it = QTableWidgetItem("\u2014")
            it.setTextAlignment(Qt.AlignCenter)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, it)
            return None

        spin = QDoubleSpinBox()
        spin.setDecimals(0)
        spin.setMaximum(1_000_000.0)
        spin.setValue(value)
        self.table.setCellWidget(row, col, spin)
        return spin

    def _on_bags_qty_or_accepted_changed(self, row_state):
        """Same remainder auto-fill as the kg track, mirrored for the
        crop bags count."""
        bags_qty = float(row_state["bags_qty_spin"].value())
        bags_accepted = float(row_state["bags_accepted_spin"].value())
        remainder = max(bags_qty - bags_accepted, 0.0)
        row_state["bags_rejected_spin"].blockSignals(True)
        row_state["bags_rejected_spin"].setValue(remainder)
        row_state["bags_rejected_spin"].blockSignals(False)

    def _on_qty_or_accepted_changed(self, row_state):
        """Rejected auto-fills as the remainder - company quantity minus
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

            conf = {"item_id": rs["item_id"], "company_price": price}
            if is_fert:
                conf["company_qty_bags"] = qty
                conf["accepted_qty_bags"] = accepted
                conf["rejected_qty_bags"] = rejected
            else:
                conf["company_weight_kg"] = qty
                conf["accepted_weight_kg"] = accepted
                conf["rejected_weight_kg"] = rejected
            confirmations.append(conf)

        from database.services.sales_order_service import SalesOrderService
        try:
            SalesOrderService.save_confirmation_progress(
                self.sales_order_id, item_confirmations=confirmations,
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", str(e))
            return

        QMessageBox.information(
            self, "Saved",
            "Progress saved - come back any time to finish once more "
            "feedback arrives. The order stays In Transit until it's "
            "genuinely confirmed."
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
                QMessageBox.warning(self, "Confirm Order", "Every line needs a company quantity greater than zero.")
                return
            if abs((accepted + rejected) - qty) > 0.01:
                QMessageBox.warning(
                    self, "Confirm Order",
                    f"Accepted ({accepted:,.2f}) + Rejected ({rejected:,.2f}) must "
                    f"equal Company Quantity ({qty:,.2f}) for every line."
                )
                return

            conf = {"item_id": rs["item_id"], "company_price": price}
            if is_fert:
                conf["company_qty_bags"] = qty
                conf["accepted_qty_bags"] = accepted
                conf["rejected_qty_bags"] = rejected
            else:
                conf["company_weight_kg"] = qty
                conf["accepted_weight_kg"] = accepted
                conf["rejected_weight_kg"] = rejected

                # Secondary sack count for crops - reconciled the same way
                # as kg, but never affects stock or the final amount.
                # (This block was previously only in _save_progress() -
                # Confirm Order itself silently dropped every crop line's
                # bag counts instead of persisting them, unlike Purchase
                # Order's equivalent Confirm dialog.)
                bags_qty = float(rs["bags_qty_spin"].value())
                bags_accepted = float(rs["bags_accepted_spin"].value())
                bags_rejected = float(rs["bags_rejected_spin"].value())
                if bags_qty > 0 and abs((bags_accepted + bags_rejected) - bags_qty) > 0.01:
                    QMessageBox.warning(
                        self, "Confirm Order",
                        f"Accepted Bags ({bags_accepted:,.0f}) + Rejected Bags "
                        f"({bags_rejected:,.0f}) must equal Company Bags "
                        f"({bags_qty:,.0f}) for every crop line."
                    )
                    return
                conf["company_qty_bags"] = bags_qty
                conf["accepted_qty_bags"] = bags_accepted
                conf["rejected_qty_bags"] = bags_rejected
            confirmations.append(conf)

        answer = QMessageBox.question(
            self, "Confirm Sales Order",
            "This will finalize the order: accepted quantities are marked "
            "sold, and rejected quantities are returned to Available "
            "Stock. This cannot be undone. Proceed?",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return

        from database.services.sales_order_service import SalesOrderService
        try:
            SalesOrderService.confirm(
                self.sales_order_id,
                item_confirmations=confirmations,
                confirmed_by_user_id=state.require_user_id(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Confirm Failed", str(e))
            return

        QMessageBox.information(self, "Confirmed", "Sales Order finalized.")
        self.accept()