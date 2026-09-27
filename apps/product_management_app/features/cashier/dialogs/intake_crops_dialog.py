#apps/product_management_app/features/cashier/dialogs/intake_crops_dialog.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QDoubleSpinBox, QSpinBox, QTextEdit, QMessageBox
)

from apps.product_management_app.app_state import state
from apps.product_management_app.features.cashier.dialogs.product_picker import (
    populate_product_picker, resolve_selected_product,
)


class CropsIntakeDialog(QDialog):
    def __init__(self, parent=None, ctx: dict | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.resize(560, 360)
        self.setWindowTitle("Crops Intake")

        self.ctx = dict(ctx or {})

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Crops Intake")
        title.setObjectName("Title")
        root.addWidget(title)

        # Product - read from the real Product table, not a hardcoded list.
        # "+ New Crop..." creates one properly if it genuinely isn't there
        # yet, with a sku generated once and kept forever after.
        r1 = QHBoxLayout()
        root.addLayout(r1)
        r1.addWidget(QLabel("Crop:"))
        self.crop_cb = QComboBox()
        populate_product_picker(self.crop_cb, "crops")
        r1.addWidget(self.crop_cb, 1)

        # Crops are kg-only, always - there used to be a "Unit: kg/bag"
        # selector here that did nothing (submit hardcoded "kg" regardless
        # of what was picked). Removed rather than leave a control that lies.

        # Qty + bags + rate
        r3 = QHBoxLayout()
        root.addLayout(r3)
        r3.addWidget(QLabel("Qty (kg):"))
        self.qty = QDoubleSpinBox()
        self.qty.setDecimals(2)
        self.qty.setMinimum(0.01)
        self.qty.setMaximum(1_000_000_000.0)
        self.qty.setValue(1.0)
        r3.addWidget(self.qty, 1)

        # Crops are dual-tracked everywhere else in this app (SaleItemBag
        # stores a weight per bag; PurchaseItem carries qty_bags alongside
        # total_weight_kg) - this field was simply missing here before.
        r3.addWidget(QLabel("Bags qty:"))
        self.qty_bags = QSpinBox()
        self.qty_bags.setMinimum(1)
        self.qty_bags.setMaximum(1_000_000)
        self.qty_bags.setValue(1)
        r3.addWidget(self.qty_bags, 1)

        r3.addWidget(QLabel("Rate per kg:"))
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(2)
        self.rate.setMinimum(0.0)
        self.rate.setMaximum(1_000_000_000.0)
        self.rate.setValue(0.0)
        r3.addWidget(self.rate, 1)

        # Selling price removed: it was collected here but never actually
        # used downstream (PurchaseService.record_intake's item payload
        # doesn't carry it) - and pricing for a sale belongs at checkout
        # time, not baked in at intake.

        # Note
        root.addWidget(QLabel("Note (optional):"))
        self.note = QTextEdit()
        self.note.setFixedHeight(90)
        root.addWidget(self.note)

        # Buttons
        btns = QHBoxLayout()
        root.addLayout(btns)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Record Intake")
        self.btn_ok.setObjectName("PrimaryButton")
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._submit)

    def _submit(self):
        qty = float(self.qty.value())
        qty_bags = int(self.qty_bags.value())
        rate = float(self.rate.value())

        if qty <= 0:
            QMessageBox.warning(self, "Validation", "Qty must be greater than zero.")
            return
        if qty_bags <= 0:
            QMessageBox.warning(self, "Validation", "Bags qty must be greater than zero.")
            return

        prod = resolve_selected_product(self.crop_cb, "crops", parent=self)
        if prod is None:
            return  # cancelled the new-product prompt

        # ✅ FIX: Read the verified source_party_id from context
        supplier_party_id = int(self.ctx.get("source_party_id") or 0) or state.get_internal_supplier_party_id()

        try:
            res = state.record_intake_db({
                "intake_type": "crops",
                "product_name": prod["name"],
                "sku": prod["sku"],   # the product's REAL, stable sku - never regenerated
                "unit": "kg",
                "qty": qty,
                "qty_bags": qty_bags,
                "total_weight_kg": qty,
                "unit_cost": rate,
                "unit_sale_price": None,
                "note": self.note.toPlainText().strip(),
                "supplier_party_id": supplier_party_id,
                "created_by_user_id": state.require_user_id(),
            })

            ref = res.get("purchase_id") or res.get("_id") or "recorded"
            QMessageBox.information(self, "Recorded", f"Crops intake recorded. Ref: {ref}")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to record intake: {str(e)}")
            return