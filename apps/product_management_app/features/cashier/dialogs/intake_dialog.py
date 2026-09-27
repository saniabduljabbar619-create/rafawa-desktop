# apps/product_management_app/features/cashier/dialogs/intake_dialog.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QDoubleSpinBox, QComboBox, QTextEdit, QMessageBox, QGroupBox
)

from shared.config.constants import LEDGER_CROPS
from apps.product_management_app.app_state import state
from PySide2.QtWidgets import QComboBox
from apps.product_management_app.app_state import Customer


class IntakeDialog(QDialog):
    """
    Delivery/Intake dialog:
    - Supplier/Source info (text)
    - Product data: name, sku, category, unit price
    - Qty added to stock
    - Ledger selector (fertilizers/crops)
    - Records:
        1) state.upsert_product_from_intake(...)
        2) state.add_activity(typ="delivery/intake", customer_id=0, meta=...)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.resize(560, 420)
        self.setWindowTitle("Delivery / Intake")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("New Delivery / Intake")
        title.setObjectName("Title")
        root.addWidget(title)

        # Customer selector (optional)
        rowc = QHBoxLayout()
        root.addLayout(rowc)

        rowc.addWidget(QLabel("Customer (optional):"))
        self.customer_cb = QComboBox()
        self.customer_cb.setMinimumWidth(320)
        self.customer_cb.addItem("No customer (Supplier/Internal)", None)

        for c in getattr(state, "customers", []):
            self.customer_cb.addItem(f"{c.full_name} ({c.phone})", c.id)

        rowc.addWidget(self.customer_cb, 1)


        # Source box
        gb_src = QGroupBox("Source / Supplier")
        root.addWidget(gb_src)
        src_l = QVBoxLayout(gb_src)

        row1 = QHBoxLayout()
        src_l.addLayout(row1)

        row1.addWidget(QLabel("Source name:"))
        self.src_name = QLineEdit()
        self.src_name.setPlaceholderText("Supplier name / Customer supply / Internal")
        row1.addWidget(self.src_name, 1)

        row2 = QHBoxLayout()
        src_l.addLayout(row2)

        row2.addWidget(QLabel("Phone (optional):"))
        self.src_phone = QLineEdit()
        self.src_phone.setPlaceholderText("e.g. 0706...")
        row2.addWidget(self.src_phone, 1)

        # Product box
        gb_prod = QGroupBox("Product Details")
        root.addWidget(gb_prod)
        prod_l = QVBoxLayout(gb_prod)

        rA = QHBoxLayout()
        prod_l.addLayout(rA)
        rA.addWidget(QLabel("Product name:"))
        self.p_name = QLineEdit()
        self.p_name.setPlaceholderText("e.g. NPK Fertilizer")
        rA.addWidget(self.p_name, 1)

        rB = QHBoxLayout()
        prod_l.addLayout(rB)
        rB.addWidget(QLabel("SKU:"))
        self.p_sku = QLineEdit()
        self.p_sku.setPlaceholderText("Unique code, e.g. NPK-20-10")
        rB.addWidget(self.p_sku, 1)

        rC = QHBoxLayout()
        prod_l.addLayout(rC)
        rC.addWidget(QLabel("Category:"))
        self.p_cat = QLineEdit()
        self.p_cat.setPlaceholderText("e.g. Fertilizers / Crops / Others")
        rC.addWidget(self.p_cat, 1)

        rD = QHBoxLayout()
        prod_l.addLayout(rD)
        rD.addWidget(QLabel("Unit price:"))
        self.p_price = QDoubleSpinBox()
        self.p_price.setDecimals(2)
        self.p_price.setMinimum(0.0)
        self.p_price.setMaximum(1_000_000_000.0)
        self.p_price.setValue(0.0)
        rD.addWidget(self.p_price, 1)

        rE = QHBoxLayout()
        prod_l.addLayout(rE)
        rE.addWidget(QLabel("Qty added (stock):"))
        self.qty_added = QDoubleSpinBox()
        self.qty_added.setDecimals(2)
        self.qty_added.setMinimum(0.01)
        self.qty_added.setMaximum(1_000_000_000.0)
        self.qty_added.setValue(1.0)
        rE.addWidget(self.qty_added, 1)

        # Ledger + note
        rF = QHBoxLayout()
        root.addLayout(rF)
        rF.addWidget(QLabel("Ledger:"))
        self.ledger = QComboBox()
        # keep flexible; you already have LEDGERS constant, but avoid dependency here
        self.ledger.addItems(["fertilizers", LEDGER_CROPS])
        rF.addWidget(self.ledger)

        rF.addWidget(QLabel("Status:"))
        self.status = QComboBox()
        self.status.addItems(["active", "inactive"])
        rF.addWidget(self.status)

        root.addWidget(QLabel("Note (optional):"))
        self.note = QTextEdit()
        self.note.setPlaceholderText("Any remarks about this intake...")
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

    def payload(self) -> dict:
        cid = self.customer_cb.currentData()
        customer_id = int(cid) if cid is not None else 0

        return {
            "customer_id": customer_id,
            "source_name": self.src_name.text().strip(),
            "source_phone": self.src_phone.text().strip(),
            "name": self.p_name.text().strip(),
            "sku": self.p_sku.text().strip(),
            "category": self.p_cat.text().strip(),
            "price": float(self.p_price.value()),
            "qty_added": float(self.qty_added.value()),
            "ledger": self.ledger.currentText().strip().lower(),
            "status": self.status.currentText().strip().lower(),
            "note": self.note.toPlainText().strip(),
        }


    def _submit(self):
        p = self.payload()
        err = self.validate(p)
        if err:
            QMessageBox.warning(self, "Validation", err)
            return

        try:
            result = state.record_intake_db(p)
        except Exception as e:
            QMessageBox.critical(self, "DB Error", str(e))
            return

        ref = result.get("reference", "")
        mode = result.get("mode", "posted")

        QMessageBox.information(
            self,
            "Recorded",
            f"Intake recorded ({mode}). Ref: {ref}. Product is now available in Sales."
        )
        self.accept()


    def validate(self, p: dict) -> str:
        if not p["name"]:
            return "Product name is required."
        if not p["sku"]:
            return "SKU is required."
        if not p["category"]:
            return "Category is required."
        if p["qty_added"] <= 0:
            return "Qty added must be greater than zero."
        if p["price"] < 0:
            return "Unit price cannot be negative."
        return ""
