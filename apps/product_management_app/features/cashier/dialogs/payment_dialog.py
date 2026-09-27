# apps/product_management_app/features/cashier/dialogs/payment_dialog.py
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QMessageBox
)

from apps.product_management_app.app_state import state


class PaymentDialog(QDialog):
    """
    Mode:
      - payment: record payment against a ledger due
      - advance: record advance (money/crops/fertilizer), give/receive
    """

    def __init__(self, parent=None, mode: str = "payment", customer_name: str = ""):
        super().__init__(parent)
        self.mode = mode
        self.customer_name = customer_name

        self.setModal(True)
        self.setWindowTitle("Make Payment" if mode == "payment" else "Record Advance")
        self.resize(460, 260)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel(self.windowTitle())
        title.setObjectName("Title")
        root.addWidget(title)

        if customer_name:
            sub = QLabel(f"Customer: {customer_name}")
            sub.setObjectName("Muted")
            root.addWidget(sub)

        # ---- Common rows container
        self.row_ledger = QHBoxLayout()
        self.row_amount = QHBoxLayout()
        self.row_method = QHBoxLayout()
        self.row_note = QHBoxLayout()

        # ---- PAYMENT fields
        self.row_ledger.addWidget(QLabel("Ledger:"))
        self.ledger = QComboBox()
        self.ledger.addItems(["Crops", "Fertilizers"])
        self.row_ledger.addWidget(self.ledger, 1)

        # ---- ADVANCE fields
        self.row_adv_type = QHBoxLayout()
        self.row_adv_dir = QHBoxLayout()
        self.row_product = QHBoxLayout()
        self.row_bags = QHBoxLayout()
        self.row_totalkg = QHBoxLayout()
        self.row_unitrate = QHBoxLayout()

        self.row_adv_type.addWidget(QLabel("Advance Type:"))
        self.advance_type = QComboBox()
        self.advance_type.addItems(["Money", "Crops", "Fertilizer"])
        self.row_adv_type.addWidget(self.advance_type, 1)

        self.row_adv_dir.addWidget(QLabel("Direction:"))
        self.direction = QComboBox()
        self.direction.addItems(["Give", "Receive"])  # maps to given/received
        self.row_adv_dir.addWidget(self.direction, 1)

        self.row_product.addWidget(QLabel("Product:"))
        self.product = QComboBox()
        self.row_product.addWidget(self.product, 1)

        self.row_bags.addWidget(QLabel("Bags:"))
        self.qty_bags = QLineEdit()
        self.qty_bags.setPlaceholderText("e.g. 10")
        self.row_bags.addWidget(self.qty_bags, 1)

        self.row_totalkg.addWidget(QLabel("Total KG:"))
        self.total_kg = QLineEdit()
        self.total_kg.setPlaceholderText("e.g. 500")
        self.row_totalkg.addWidget(self.total_kg, 1)

        self.row_unitrate.addWidget(QLabel("Unit Rate:"))
        self.unit_rate = QLineEdit()
        self.unit_rate.setPlaceholderText("optional: price per kg")
        self.row_unitrate.addWidget(self.unit_rate, 1)

        # ---- Amount (used by payment and money-advance, optional for in-kind)
        self.row_amount.addWidget(QLabel("Amount:"))
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("e.g. 5000")
        self.row_amount.addWidget(self.amount, 1)

        # ---- Method (money only)
        self.row_method.addWidget(QLabel("Method:"))
        self.method = QComboBox()
        self.method.addItems(["Cash", "Transfer"])
        self.row_method.addWidget(self.method, 1)

        # ---- Note
        self.row_note.addWidget(QLabel("Note:"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Reference / remark (optional)")
        self.row_note.addWidget(self.note, 1)

        # Add rows to UI depending on mode
        if mode == "payment":
            root.addLayout(self.row_ledger)
            root.addLayout(self.row_amount)
            root.addLayout(self.row_method)
            root.addLayout(self.row_note)
        else:
            root.addLayout(self.row_adv_type)
            root.addLayout(self.row_adv_dir)
            root.addLayout(self.row_product)
            root.addLayout(self.row_bags)
            root.addLayout(self.row_totalkg)
            root.addLayout(self.row_unitrate)
            root.addLayout(self.row_amount)
            root.addLayout(self.row_method)
            root.addLayout(self.row_note)

            self.advance_type.currentTextChanged.connect(self._sync_advance_form)
            self._sync_advance_form(self.advance_type.currentText())

        # Buttons
        btns = QHBoxLayout()
        root.addLayout(btns)
        btns.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Confirm")
        self.btn_ok.setObjectName("PrimaryButton")

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_confirm)

    def _sync_advance_form(self, t: str):
        t = (t or "Money").strip().lower()

        # money: no product fields, method visible, amount required
        is_money = (t == "money")

        self.product.setEnabled(not is_money)
        self.qty_bags.setEnabled(not is_money)
        self.total_kg.setEnabled(not is_money)
        self.unit_rate.setEnabled(not is_money)

        # Method only applies to money
        self.method.setEnabled(is_money)

        if is_money:
            self.amount.setPlaceholderText("money amount")
        else:
            self.amount.setPlaceholderText("optional: value estimate")
            # load products for selected type
            prods = state.list_products_by_advance_type_db(t)  # expects 'crops' or 'fertilizer'
            self.product.clear()
            for p in prods:
                self.product.addItem(p["name"], int(p["id"]))

    def _on_confirm(self):
        payload = self.payload()
        err = self.validate(payload)
        if err:
            QMessageBox.warning(self, "Validation", err)
            return
        self.accept()

    def payload(self) -> dict:
        if self.mode == "payment":
            led = self.ledger.currentText().strip().lower()
            led = "crops" if "crop" in led else "fertilizers"
            return {
                "mode": self.mode,
                "ledger": led,
                "amount": self.amount.text().strip(),
                "method": self.method.currentText().lower(),
                "note": self.note.text().strip(),
            }

        # advance
        adv_type_ui = (self.advance_type.currentText() or "Money").strip().lower()
        adv_type = "money" if adv_type_ui == "money" else ("crops" if "crop" in adv_type_ui else "fertilizer")

        dir_ui = (self.direction.currentText() or "Give").strip().lower()
        direction = "given" if dir_ui == "give" else "received"

        def fnum(x):
            try:
                return float(str(x or "").replace(",", "").strip() or 0)
            except Exception:
                return 0.0

        product_id = None
        if adv_type in ("crops", "fertilizer"):
            product_id = int(self.product.currentData() or 0)

        return {
            "mode": self.mode,
            "advance_type": adv_type,
            "direction": direction,
            "product_id": product_id,
            "qty_bags": fnum(self.qty_bags.text()),
            "total_weight_kg": fnum(self.total_kg.text()),
            "unit_rate": fnum(self.unit_rate.text()),
            "amount": fnum(self.amount.text()),
            "method": self.method.currentText().lower(),  # used for money only
            "note": self.note.text().strip(),
        }

    @staticmethod
    def validate(payload: dict) -> str:
        if payload.get("mode") == "payment":
            amt = payload.get("amount", "")
            if not amt:
                return "Amount is required."
            try:
                v = float(amt)
            except Exception:
                return "Amount must be a number."
            if v <= 0:
                return "Amount must be greater than 0."
            return ""

        # advance validation
        adv_type = (payload.get("advance_type") or "").strip().lower()
        direction = (payload.get("direction") or "").strip().lower()
        if adv_type not in ("money", "crops", "fertilizer"):
            return "Advance type is required."
        if direction not in ("given", "received"):
            return "Direction is required."

        if adv_type == "money":
            v = float(payload.get("amount") or 0.0)
            if v <= 0:
                return "Money amount must be greater than 0."
            return ""

        # in-kind
        pid = int(payload.get("product_id") or 0)
        if pid <= 0:
            return "Select a product."
        kg = float(payload.get("total_weight_kg") or 0.0)
        if kg <= 0:
            return "Total KG must be greater than 0."
        # bags optional, unit_rate optional
        return ""
