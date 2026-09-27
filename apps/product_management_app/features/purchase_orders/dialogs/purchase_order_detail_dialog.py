# apps/product_management_app/features/purchase_orders/dialogs/purchase_order_detail_dialog.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state

_STATUS_LABELS = {
    "draft": "Draft",
    "in_transit": "In Transit",
    "confirmed": "Confirmed",
    "cancelled": "Cancelled",
}


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class PurchaseOrderDetailDialog(QDialog):
    """
    Every line, whichever unit each one uses (kg for crops, bags for
    fertilizer), plus the order's own status and amounts - the action
    hub itself: Mark In-Transit, Confirm, Revert to Draft, Cancel, and
    Make Payment all live here.

    Reached by double-clicking a row on the supplier profile's Orders
    tab. Which actions actually show is governed by the order's own
    current status, mirroring Sales Order's exact rules.
    """
    def __init__(self, purchase_order_id: int, parent=None, is_admin: bool = True):
        super().__init__(parent)
        self.purchase_order_id = int(purchase_order_id)
        self.is_admin = is_admin
        self._detail = None
        self._changed = False  # True if any action here actually changed the order

        self.setWindowTitle("Purchase Order Detail")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(880, 640)

        layout = QVBoxLayout(self)

        self.header_label = QLabel("")
        self.header_label.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(self.header_label)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #555;")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.driver_label = QLabel("")
        self.driver_label.setStyleSheet("color: #555;")
        layout.addWidget(self.driver_label)

        self.payment_label = QLabel("")
        self.payment_label.setStyleSheet("font-weight: 600; padding: 4px 0;")
        layout.addWidget(self.payment_label)

        # Bags shown for crop rows two places: alongside Ordered (what was
        # ordered) and alongside Accepted/Rejected (what actually came
        # in) - fertilizer rows show a dash in both, since bags is
        # already that row's primary unit and a second track would be
        # redundant (same rule the Confirm dialog's spinboxes follow).
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Product", "Unit", "Ordered", "Ordered Bags", "Expected Price",
            "Received Qty", "Received Price", "Accepted / Rejected",
            "Accepted / Rejected Bags", "Final Amount",
        ])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setMinimumHeight(240)
        layout.addWidget(self.table, 1)

        self.notes_label = QLabel("")
        self.notes_label.setStyleSheet("color: #555;")
        self.notes_label.setWordWrap(True)
        layout.addWidget(self.notes_label)

        # ---- action hub ----
        actions = QHBoxLayout()
        self.btn_mark_in_transit = QPushButton("Mark In-Transit")
        self.btn_confirm = QPushButton("Confirm (Finalize)")
        self.btn_revert = QPushButton("Revert to Draft")
        self.btn_cancel = QPushButton("Cancel Order")
        self.btn_make_payment = QPushButton("Make Payment")
        actions.addWidget(self.btn_mark_in_transit)
        actions.addWidget(self.btn_confirm)
        actions.addWidget(self.btn_revert)
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_make_payment)
        actions.addStretch(1)
        layout.addLayout(actions)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.btn_close.clicked.connect(self.accept)
        self.btn_mark_in_transit.clicked.connect(self._mark_in_transit)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_revert.clicked.connect(self._revert)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_make_payment.clicked.connect(self._make_payment)

        self._load()

    # ------------------------------------------------------------------
    def _load(self):
        from database.services.purchase_order_service import PurchaseOrderService
        from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService

        try:
            detail = PurchaseOrderService.get_order_detail(self.purchase_order_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.reject()
            return
        self._detail = detail

        supplier = PurchaseOrderSupplierService.get(detail["supplier_id"])
        supplier_name = supplier["name"] if supplier else "Unknown supplier"

        self.header_label.setText(f"{detail['order_no']} \u2014 {supplier_name}")

        status = _STATUS_LABELS.get(detail["status"], detail["status"])
        created_s = detail["created_at"].strftime("%Y-%m-%d %H:%M") if detail["created_at"] else "\u2014"
        dispatched_s = detail["dispatched_at"].strftime("%Y-%m-%d %H:%M") if detail["dispatched_at"] else "not yet marked"
        confirmed_s = detail["confirmed_at"].strftime("%Y-%m-%d %H:%M") if detail["confirmed_at"] else "not yet confirmed"
        final_s = f"\u20a6{detail['final_total_amount']:,.2f}" if detail["final_total_amount"] is not None else "not yet finalized"
        expected_s = (
            detail["expected_delivery_date"].strftime("%Y-%m-%d")
            if detail.get("expected_delivery_date") else "not set"
        )

        self.meta_label.setText(
            f"Status: {status}  |  Created: {created_s}  |  "
            f"Marked In-Transit: {dispatched_s}  |  Confirmed: {confirmed_s}  |  "
            f"Final Amount: {final_s}  |  Expected Delivery: {expected_s}"
        )

        driver = detail.get("driver")
        if driver:
            self.driver_label.setText(
                f"Driver: {driver['name']}"
                + (f" | {driver['phone']}" if driver.get('phone') else "")
                + (f" | {driver['vehicle_number']}" if driver.get('vehicle_number') else "")
            )
            self.driver_label.setVisible(True)
        else:
            self.driver_label.setVisible(False)

        payment = detail.get("payment")
        if payment:
            status_label = payment["status"].replace("_", " ").capitalize()
            self.payment_label.setText(
                f"Payment: {status_label} \u2014 "
                f"\u20a6{payment['paid']:,.2f} paid of \u20a6{payment['total']:,.2f} "
                f"(balance \u20a6{payment['balance']:,.2f})"
            )
            self.payment_label.setVisible(True)
        else:
            self.payment_label.setVisible(False)

        self.table.setRowCount(0)
        for it in detail["items"]:
            is_fert = _is_fert(it["product_type_snapshot"])
            unit = "bags" if is_fert else "kg"
            ordered = it["ordered_qty_bags"] if is_fert else it["ordered_weight_kg"]
            received_qty = (it["received_qty_bags"] if is_fert else it["received_weight_kg"])
            accepted = (it["accepted_qty_bags"] if is_fert else it["accepted_weight_kg"])
            rejected = (it["rejected_qty_bags"] if is_fert else it["rejected_weight_kg"])

            received_qty_s = f"{received_qty:,.2f}" if received_qty is not None else "\u2014"
            received_price_s = f"{it['received_price']:,.2f}" if it["received_price"] is not None else "\u2014"
            accepted_rejected_s = (
                f"{accepted:,.2f} / {rejected:,.2f}"
                if accepted is not None and rejected is not None else "\u2014"
            )
            final_line_s = f"{it['final_line_amount']:,.2f}" if it["final_line_amount"] is not None else "\u2014"

            if is_fert:
                ordered_bags_s = "\u2014"
                accepted_rejected_bags_s = "\u2014"
            else:
                ordered_bags_s = f"{it['ordered_qty_bags']:,.0f}"
                ab, rb = it["accepted_qty_bags"], it["rejected_qty_bags"]
                accepted_rejected_bags_s = f"{ab:,.0f} / {rb:,.0f}" if ab is not None and rb is not None else "\u2014"

            r = self.table.rowCount()
            self.table.insertRow(r)
            cells = [
                it["product_name"], unit, f"{ordered:,.2f}", ordered_bags_s, f"{it['expected_price']:,.2f}",
                received_qty_s, received_price_s, accepted_rejected_s, accepted_rejected_bags_s, final_line_s,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

        self.notes_label.setText(f"Notes: {detail['notes']}" if detail["notes"] else "")

        self._sync_action_state()

    def _sync_action_state(self):
        status = self._detail["status"]
        payment = self._detail.get("payment")
        can_pay = bool(payment and payment["status"] in ("unpaid", "partial"))

        self.btn_mark_in_transit.setVisible(self.is_admin and status == "draft")
        self.btn_revert.setVisible(self.is_admin and status == "in_transit")
        self.btn_cancel.setVisible(self.is_admin and status == "draft")
        self.btn_confirm.setVisible(status == "in_transit")
        self.btn_make_payment.setVisible(status == "confirmed" and can_pay)

    # ------------------------------------------------------------------
    def _mark_in_transit(self):
        from apps.product_management_app.features.sales_orders.dialogs.driver_picker_dialog import (
            DriverPickerDialog,
        )
        driver_dlg = DriverPickerDialog(
            parent=self,
            skip_label="Mark In-Transit Without Driver",
            hint_text="Optional - who's bringing these goods, if known yet?",
        )
        if driver_dlg.exec_() != driver_dlg.Accepted:
            return  # operator cancelled the driver step entirely - nothing happens
        driver_id = driver_dlg.selected_driver_id  # may be None - that's fine, it's optional

        confirm = QMessageBox.question(
            self, "Confirm",
            f"Mark {self._detail['order_no']} as In-Transit? This is purely "
            f"informational - the supplier has acknowledged or is sending "
            f"the order, but nothing changes in Available Stock until you "
            f"confirm what actually arrives.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.purchase_order_service import PurchaseOrderService
        try:
            PurchaseOrderService.mark_in_transit(
                self.purchase_order_id, marked_by_user_id=state.require_user_id(),
                driver_id=driver_id,
            )
        except Exception as e:
            QMessageBox.critical(self, "Failed", str(e))
            return

        self._changed = True
        QMessageBox.information(self, "Marked In-Transit", f"{self._detail['order_no']} is now In Transit.")
        self._load()

    def _confirm(self):
        from apps.product_management_app.features.purchase_orders.dialogs.purchase_order_confirm_dialog import (
            PurchaseOrderConfirmDialog,
        )
        dlg = PurchaseOrderConfirmDialog(purchase_order_id=self.purchase_order_id, parent=self)
        if dlg.exec_() == dlg.Accepted:
            self._changed = True
            self._load()

    def _revert(self):
        confirm = QMessageBox.question(
            self, "Confirm Revert",
            f"Revert {self._detail['order_no']} back to draft? This clears "
            f"any confirmation feedback already saved for it - genuinely "
            f"starting the process over. Since nothing ever touched stock "
            f"at the in-transit stage, there's nothing to reverse there.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.purchase_order_service import PurchaseOrderService
        try:
            PurchaseOrderService.revert_to_draft(self.purchase_order_id, reverted_by_user_id=state.require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Revert Failed", str(e))
            return

        self._changed = True
        QMessageBox.information(self, "Reverted", f"{self._detail['order_no']} is back to draft.")
        self._load()

    def _cancel(self):
        confirm = QMessageBox.question(
            self, "Confirm Cancel",
            f"Cancel {self._detail['order_no']}? Nothing has touched stock "
            f"yet - this simply withdraws it.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        from database.services.purchase_order_service import PurchaseOrderService
        try:
            PurchaseOrderService.cancel(self.purchase_order_id, cancelled_by_user_id=state.require_user_id())
        except Exception as e:
            QMessageBox.critical(self, "Cancel Failed", str(e))
            return

        self._changed = True
        QMessageBox.information(self, "Cancelled", f"{self._detail['order_no']} has been cancelled.")
        self._load()

    def _make_payment(self):
        from apps.product_management_app.features.purchase_orders.dialogs.purchase_order_payment_dialog import (
            PurchaseOrderPaymentDialog,
        )
        dlg = PurchaseOrderPaymentDialog(purchase_order_id=self.purchase_order_id, parent=self)
        dlg.exec_()
        self._load()  # refresh regardless - a payment may have been recorded even if the dialog stayed open

    def closeEvent(self, event):
        if self._changed:
            self.accept()
        super().closeEvent(event)