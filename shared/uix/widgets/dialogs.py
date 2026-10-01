# shared/uix/widgets/dialogs.py
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QMessageBox
)
from PySide2.QtCore import Qt

from shared.config.constants import LEDGER_CROPS, LEDGER_FERTILIZERS
from shared.uix.layout.scrollable import make_scrollable_dialog
from datetime import datetime

class ProductFormDialog(QDialog):
    """
    DB-aligned Add/Edit product dialog.

    Stock is NOT captured here (stock comes from stock_movements).
    Required:
      - name
      - product_type (crops|fertilizer)

    Optional:
      - sku (if blank, AppState will auto-generate)
      - category (UI-only label; DB currently uses category_id, so we keep it optional & non-blocking)
      - default_cost_rate, default_sale_rate
      - status
    """

    def __init__(self, parent=None, title="Product", categories=None, initial=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        # This form has enough fields (Name, Type, SKU, Category, two
        # rates, two inventory-correction fields, Status) that it can
        # easily exceed the default dialog height on a smaller window,
        # pushing Save/Cancel out of reach - exactly what was reported.
        # Min/max buttons let the operator resize it back to something
        # usable, and the scrollable+pinned-footer pattern (already
        # proven elsewhere this session) keeps Save/Cancel reachable
        # regardless of how tall the form's own content gets.
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.setMinimumWidth(460)
        self.resize(480, 620)

        layout, footer = make_scrollable_dialog(self, margins=(12, 12, 12, 12), spacing=8)

        self.categories = categories or ["Auduga", "Masara", "waken Suya", "Others"]

        # Name (required)
        layout.addWidget(QLabel("Name *"))
        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("e.g. Masara (Maize)")
        layout.addWidget(self.name_in)

        # Type (required)
        layout.addWidget(QLabel("Type *"))
        self.type_in = QComboBox()
        self.type_in.addItems(["crops", "fertilizer"])
        layout.addWidget(self.type_in)

        # SKU (optional)
        layout.addWidget(QLabel("SKU (optional)"))
        self.sku_in = QLineEdit()
        self.sku_in.setPlaceholderText("Leave blank to auto-generate")
        layout.addWidget(self.sku_in)

        # Category (optional, UI-only for now)
        layout.addWidget(QLabel("Category (optional)"))
        self.category_in = QComboBox()
        self.category_in.addItem("")  # blank option
        self.category_in.addItems(self.categories)
        layout.addWidget(self.category_in)

        # Default rates (optional)
        layout.addWidget(QLabel("Default Cost Rate (optional)"))
        self.cost_in = QDoubleSpinBox()
        self.cost_in.setDecimals(2)
        self.cost_in.setMinimum(0.0)
        self.cost_in.setMaximum(1000000000.0)
        self.cost_in.setSingleStep(50.0)
        layout.addWidget(self.cost_in)

        layout.addWidget(QLabel("Default Sale Rate (optional)"))
        self.sale_in = QDoubleSpinBox()
        self.sale_in.setDecimals(2)
        self.sale_in.setMinimum(0.0)
        self.sale_in.setMaximum(1000000000.0)
        self.sale_in.setSingleStep(50.0)
        layout.addWidget(self.sale_in)

        # Inventory Correction (optional)
        layout.addWidget(QLabel("Inventory Correction"))

        # Qty Bags
        layout.addWidget(QLabel("Qty (Bags)"))
        self.bags_in = QDoubleSpinBox()
        self.bags_in.setDecimals(0)
        self.bags_in.setMinimum(0)
        self.bags_in.setMaximum(100000000)
        layout.addWidget(self.bags_in)

        # Total Weight
        layout.addWidget(QLabel("Total Weight (KG)"))
        self.weight_in = QDoubleSpinBox()
        self.weight_in.setDecimals(2)
        self.weight_in.setMinimum(0)
        self.weight_in.setMaximum(1000000000)
        layout.addWidget(self.weight_in)

        # Status
        layout.addWidget(QLabel("Status"))
        self.status_in = QComboBox()
        self.status_in.addItems(["active", "inactive"])
        layout.addWidget(self.status_in)

        self.type_in.currentTextChanged.connect(self._sync_type_behavior)
        self._sync_type_behavior(self.type_in.currentText())

        # Buttons - pinned footer, always reachable regardless of content height
        footer.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.save_btn = QPushButton("Save")
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.save_btn)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._on_save)

        # Prefill (edit mode)
        if initial:
            self.name_in.setText(str(initial.get("name", "")))

            pt = (initial.get("product_type") or initial.get("type") or "crops")
            pt = str(pt).strip().lower()
            self.type_in.setCurrentText("fertilizer" if pt == "fertilizer" else "crops")

            self.sku_in.setText(str(initial.get("sku", "") or ""))

            cat = str(initial.get("category", "") or "").strip()
            if cat:
                if cat in self.categories:
                    self.category_in.setCurrentText(cat)
                else:
                    # if unknown category label, keep blank (do not break)
                    self.category_in.setCurrentText("")

            try:
                self.cost_in.setValue(float(initial.get("default_cost_rate", initial.get("cost_price", 0.0)) or 0.0))
            except Exception:
                self.cost_in.setValue(0.0)

            try:
                self.sale_in.setValue(float(initial.get("default_sale_rate", initial.get("sale_price", 0.0)) or 0.0))
            except Exception:
                self.sale_in.setValue(0.0)

            try:
                self.bags_in.setValue(float(initial.get("qty_bags", 0.0)))
            except Exception:
                self.bags_in.setValue(0)

            try:
                self.weight_in.setValue(float(initial.get("total_weight_kg", 0.0)))
            except Exception:
                self.weight_in.setValue(0)

            status = str(initial.get("status", "active")).strip().lower()
            self.status_in.setCurrentText(status if status in ("active", "inactive") else "active")

    def _on_save(self):
        payload = self.payload()
        err = self.validate(payload)
        if err:
            QMessageBox.critical(self, "Validation Error", err)
            return
        self.accept()

    def _sync_type_behavior(self, product_type):
        """
        Crops → allow weight editing
        Fertilizer → bags only
        """
        if product_type == "fertilizer":
            self.weight_in.setValue(0)
            self.weight_in.setDisabled(True)
        else:
            self.weight_in.setDisabled(False)

    def payload(self) -> dict:
        return {
            "name": self.name_in.text().strip(),
            "product_type": self.type_in.currentText().strip().lower(),
            "sku": self.sku_in.text().strip(),
            "category": self.category_in.currentText().strip(),

            "default_cost_rate": float(self.cost_in.value()),
            "default_sale_rate": float(self.sale_in.value()),

            "qty_bags": float(self.bags_in.value()),
            "total_weight_kg": float(self.weight_in.value()),

            "status": self.status_in.currentText().strip(),
        }

    @staticmethod
    def validate(payload: dict) -> str:
        if not (payload.get("name") or "").strip():
            return "Name is required."
        pt = (payload.get("product_type") or "").strip().lower()
        if pt not in ("crops", "fertilizer"):
            return "Type must be crops or fertilizer."
        # SKU optional
        # cost/sale optional (>=0 already enforced by spinboxes)
        return ""

    @staticmethod
    def error(parent, message: str):
        QMessageBox.critical(parent, "Validation Error", message)



from PySide2.QtWidgets import QTextEdit

class CustomerFormDialog(QDialog):
    def __init__(self, parent=None, title="Customer", initial=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Full Name *"))
        self.name_in = QLineEdit()
        self.name_in.setPlaceholderText("e.g. Aisha Mohammed")
        layout.addWidget(self.name_in)

        layout.addWidget(QLabel("Phone * (unique)"))
        self.phone_in = QLineEdit()
        self.phone_in.setPlaceholderText("e.g. 08012345678")
        layout.addWidget(self.phone_in)

        layout.addWidget(QLabel("Email"))
        self.email_in = QLineEdit()
        self.email_in.setPlaceholderText("optional")
        layout.addWidget(self.email_in)

        layout.addWidget(QLabel("Address"))
        self.address_in = QLineEdit()
        self.address_in.setPlaceholderText("optional")
        layout.addWidget(self.address_in)

        layout.addWidget(QLabel("Status"))
        self.status_in = QComboBox()
        self.status_in.addItems(["active", "inactive"])
        layout.addWidget(self.status_in)

        layout.addWidget(QLabel("Notes"))
        self.notes_in = QTextEdit()
        self.notes_in.setPlaceholderText("Free-form notes…")
        self.notes_in.setMinimumHeight(90)
        layout.addWidget(self.notes_in)

        btns = QHBoxLayout()
        layout.addLayout(btns)
        btns.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.save_btn = QPushButton("Save")
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.save_btn)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self.accept)

        if initial:
            self.name_in.setText(str(initial.get("full_name", "")))
            self.phone_in.setText(str(initial.get("phone", "")))
            self.email_in.setText(str(initial.get("email", "")))
            self.address_in.setText(str(initial.get("address", "")))
            status = str(initial.get("status", "active")).strip().lower()
            self.status_in.setCurrentText(status if status in ("active", "inactive") else "active")
            self.notes_in.setPlainText(str(initial.get("notes", "")))

    def payload(self) -> dict:
        return {
            "full_name": self.name_in.text().strip(),
            "phone": self.phone_in.text().strip(),
            "email": self.email_in.text().strip(),
            "address": self.address_in.text().strip(),
            "status": self.status_in.currentText().strip(),
            "notes": self.notes_in.toPlainText().strip(),
        }

    @staticmethod
    def validate(payload: dict) -> str:
        if not payload.get("full_name"):
            return "Full name is required."
        if not payload.get("phone"):
            return "Phone is required."
        # simple phone sanity check
        if len(payload["phone"]) < 7:
            return "Phone looks too short."
        return ""


# --- Delivery/Intake Activity Dialog (per-bag weights) ---
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QDoubleSpinBox,
    QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide2.QtCore import Qt
from datetime import datetime


from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QDoubleSpinBox,
    QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QComboBox
)
from PySide2.QtCore import Qt
from datetime import datetime


class DeliveryIntakeDialog(QDialog):
    """
    Delivery/Intake supports:
      - Exact mode: per-bag weights table
      - Quick mode: total kg input
      - Quick mode: avg kg per bag input
      - Bulk mode: bag-count x kg pattern lines (for 500+ bags)
    Always:
      total_amount = total_kg * rate_per_kg
    """

    MODE_EXACT = "Exact (Per-bag weights)"
    MODE_TOTAL = "Quick (Total KG)"
    MODE_AVG = "Quick (Avg KG per bag)"
    MODE_BULK = "Quick (Bulk pattern: count x kg)"

    def __init__(self, parent=None, title="New Delivery/Intake", initial_bags=1):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Product (ComboBox)
        layout.addWidget(QLabel("Product *"))
        self.product_in = QComboBox()
        self.product_in.setEditable(True)
        layout.addWidget(self.product_in)

        
        # Ledger + Mode + bags + rate row
        row = QHBoxLayout()
        layout.addLayout(row)

        row.addWidget(QLabel("Ledger"))
        self.ledger_in = QComboBox()
        self.ledger_in.addItems(["Crops", "Fertilizers"])
        row.addWidget(self.ledger_in, 1)

        row.addSpacing(10)

        row.addWidget(QLabel("Mode"))
        self.mode_in = QComboBox()
        self.mode_in.addItems([self.MODE_EXACT, self.MODE_TOTAL, self.MODE_AVG, self.MODE_BULK])
        row.addWidget(self.mode_in, 2)

        row.addSpacing(10)

        row.addWidget(QLabel("Bags *"))
        self.bags_in = QSpinBox()
        self.bags_in.setMinimum(1)
        self.bags_in.setMaximum(100000)
        self.bags_in.setValue(max(1, int(initial_bags)))
        row.addWidget(self.bags_in, 1)

        row.addSpacing(10)

        row.addWidget(QLabel("Rate per Kg *"))
        self.rate_in = QDoubleSpinBox()
        self.rate_in.setDecimals(2)
        self.rate_in.setMinimum(0.01)
        self.rate_in.setMaximum(1_000_000_000.0)
        self.rate_in.setValue(1.0)
        row.addWidget(self.rate_in, 1)
        self.ledger_in.currentTextChanged.connect(lambda _t: self._reload_products())


        # Quick inputs
        quick = QHBoxLayout()
        layout.addLayout(quick)

        self.totalkg_lbl = QLabel("Total KG *")
        self.totalkg_in = QDoubleSpinBox()
        self.totalkg_in.setDecimals(2)
        self.totalkg_in.setMinimum(0.00)
        self.totalkg_in.setMaximum(1_000_000_000.0)
        self.totalkg_in.setValue(0.0)

        self.avgkg_lbl = QLabel("Avg KG per bag *")
        self.avgkg_in = QDoubleSpinBox()
        self.avgkg_in.setDecimals(2)
        self.avgkg_in.setMinimum(0.00)
        self.avgkg_in.setMaximum(1_000_000_000.0)
        self.avgkg_in.setValue(0.0)

        quick.addWidget(self.totalkg_lbl)
        quick.addWidget(self.totalkg_in, 1)
        quick.addSpacing(18)
        quick.addWidget(self.avgkg_lbl)
        quick.addWidget(self.avgkg_in, 1)

        # Bulk pattern input (hidden unless bulk mode)
        layout.addWidget(QLabel("Bulk Pattern (one per line)"))
        self.bulk_in = QTextEdit()
        self.bulk_in.setPlaceholderText(
            "Examples:\n"
            "200 x 50\n"
            "300 x 40\n\n"
            "Meaning: 200 bags at 50kg each, 300 bags at 40kg each."
        )
        self.bulk_in.setMinimumHeight(100)
        layout.addWidget(self.bulk_in)

        self.bulk_hint = QLabel("Tip: You can use 'x' or '*' between count and kg (e.g. 200*50).")
        self.bulk_hint.setStyleSheet("color: #666;")
        layout.addWidget(self.bulk_hint)

        # Weights table (Exact mode)
        self.weights_title = QLabel("Weights per bag (Kg) *")
        layout.addWidget(self.weights_title)

        self.weights_tbl = QTableWidget(0, 2)
        self.weights_tbl.setHorizontalHeaderLabels(["Bag #", "Weight (Kg)"])
        self.weights_tbl.horizontalHeader().setStretchLastSection(True)
        self.weights_tbl.setEditTriggers(QTableWidget.AllEditTriggers)
        layout.addWidget(self.weights_tbl)

        # One-line compute preview
        self.compute_line = QLabel("Compute: --")
        self.compute_line.setStyleSheet("color:#444; font-weight:600;")
        layout.addWidget(self.compute_line)

        # Totals
        totals = QHBoxLayout()
        layout.addLayout(totals)

        self.total_kg_lbl = QLabel("Total Kg: --")
        self.total_amt_lbl = QLabel("Total Amount: --")
        self.total_amt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        totals.addWidget(self.total_kg_lbl)
        totals.addStretch(1)
        totals.addWidget(self.total_amt_lbl)

        # Buttons
        btns = QHBoxLayout()
        layout.addLayout(btns)
        btns.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.save_btn = QPushButton("Save")
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.save_btn)

        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._on_save)

        # Events
        self.mode_in.currentTextChanged.connect(self._apply_mode)
        self.bags_in.valueChanged.connect(self._on_bags_changed)
        self.rate_in.valueChanged.connect(self._recalc_totals)
        self.totalkg_in.valueChanged.connect(self._recalc_totals)
        self.avgkg_in.valueChanged.connect(self._recalc_totals)
        self.weights_tbl.itemChanged.connect(lambda _it: self._recalc_totals())
        self.bulk_in.textChanged.connect(self._recalc_totals)
        self.ledger_in.currentTextChanged.connect(lambda _t: self._recalc_totals())

        self.ledger_in.currentTextChanged.connect(lambda _t: self._apply_ledger_rules())
        self._apply_ledger_rules()

        # init
        self._rebuild_weight_rows(self.bags_in.value())
        self._apply_mode(self.mode_in.currentText())
        self._reload_products()



    def _apply_mode(self, mode: str):
        is_exact = (mode == self.MODE_EXACT)
        is_total = (mode == self.MODE_TOTAL)
        is_avg = (mode == self.MODE_AVG)
        is_bulk = (mode == self.MODE_BULK)

        self.weights_title.setVisible(is_exact)
        self.weights_tbl.setVisible(is_exact)

        self.totalkg_lbl.setVisible(is_total)
        self.totalkg_in.setVisible(is_total)

        self.avgkg_lbl.setVisible(is_avg)
        self.avgkg_in.setVisible(is_avg)

        self.bulk_in.setVisible(is_bulk)
        self.bulk_hint.setVisible(is_bulk)

        self._recalc_totals()

    def _selected_product_row(self) -> dict:
        row = self.product_in.currentData()
        return row if isinstance(row, dict) else {}

    def _product_text(self) -> str:
        return (self.product_in.currentText() or "").strip()


    def _on_bags_changed(self, n: int):
        if self.mode_in.currentText() == self.MODE_EXACT:
            self._rebuild_weight_rows(n)
        else:
            self._recalc_totals()

    def _rebuild_weight_rows(self, n: int):
        existing = []
        for r in range(self.weights_tbl.rowCount()):
            item = self.weights_tbl.item(r, 1)
            existing.append(item.text().strip() if item else "")

        self.weights_tbl.blockSignals(True)
        self.weights_tbl.setRowCount(0)

        for i in range(n):
            row = self.weights_tbl.rowCount()
            self.weights_tbl.insertRow(row)

            bag_item = QTableWidgetItem(str(i + 1))
            bag_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            bag_item.setTextAlignment(Qt.AlignCenter)
            self.weights_tbl.setItem(row, 0, bag_item)

            wtxt = existing[i] if i < len(existing) else ""
            w_item = QTableWidgetItem(wtxt)
            w_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.weights_tbl.setItem(row, 1, w_item)

        self.weights_tbl.blockSignals(False)
        self._recalc_totals()

    def _read_weights_exact(self):
        weights = []
        for r in range(self.weights_tbl.rowCount()):
            item = self.weights_tbl.item(r, 1)
            txt = (item.text().strip() if item else "")
            if not txt:
                return None, f"Weight missing on bag #{r+1}"
            try:
                w = float(txt)
            except Exception:
                return None, f"Invalid weight on bag #{r+1}"
            if w <= 0:
                return None, f"Weight must be > 0 on bag #{r+1}"
            weights.append(w)
        return weights, ""

    def _parse_bulk_pattern(self):
        """
        Accept lines like:
          200 x 50
          300*40
        Returns: (total_bags, total_kg, items:list[(count, kg)], error)
        """
        raw = self.bulk_in.toPlainText().strip()
        if not raw:
            return None, None, None, "Bulk pattern is required."

        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        items = []
        total_bags = 0
        total_kg = 0.0

        for i, ln in enumerate(lines, start=1):
            ln2 = ln.replace("*", "x").replace("X", "x")
            if "x" not in ln2:
                return None, None, None, f"Invalid bulk line #{i}: '{ln}'. Use 'count x kg'."
            left, right = [p.strip() for p in ln2.split("x", 1)]
            try:
                cnt = int(float(left))
            except Exception:
                return None, None, None, f"Invalid count on line #{i}: '{left}'"
            try:
                kg = float(right)
            except Exception:
                return None, None, None, f"Invalid kg on line #{i}: '{right}'"

            if cnt <= 0:
                return None, None, None, f"Count must be > 0 on line #{i}"
            if kg <= 0:
                return None, None, None, f"Kg must be > 0 on line #{i}"

            items.append((cnt, kg))
            total_bags += cnt
            total_kg += cnt * kg

        return total_bags, float(total_kg), items, ""

    def _get_total_kg(self):
        # ✅ fertilizer is bags-only → total_kg is always 0
        if self._is_fertilizer():
            return 0.0, "", None, None

        # crops: keep your original behavior
        mode = self.mode_in.currentText()

        if mode == self.MODE_EXACT:
            weights, err = self._read_weights_exact()
            if weights is None:
                return None, err, None, None
            return float(sum(weights)), "", weights, None

        if mode == self.MODE_TOTAL:
            total = float(self.totalkg_in.value())
            if total <= 0:
                return None, "Total KG must be > 0.", None, None
            return total, "", None, None

        if mode == self.MODE_AVG:
            avg = float(self.avgkg_in.value())
            bags = int(self.bags_in.value())
            if avg <= 0:
                return None, "Avg KG per bag must be > 0.", None, None
            total = avg * bags
            return float(total), "", None, None

        if mode == self.MODE_BULK:
            total_bags, total_kg, items, err = self._parse_bulk_pattern()
            if total_kg is None:
                return None, err, None, None
            return float(total_kg), "", None, items

        return None, "Unknown mode.", None, None


    def _ledger_value(self) -> str:
        # maps UI -> state ledger key
        t = self.ledger_in.currentText().strip().lower()
        return "fertilizers" if "fert" in t else "crops"

    def _recalc_totals(self):
        is_fert = self._is_fertilizer()
        total_kg, err, _weights, bulk_items = self._get_total_kg()
        rate = float(self.rate_in.value())
        bags = int(self.bags_in.value())

        if rate <= 0:
            self.compute_line.setText("Compute: -- (Price must be > 0)")
            self.total_kg_lbl.setText("Total Kg: --")
            self.total_amt_lbl.setText("Total Amount: --")
            return

        if total_kg is None:
            self.compute_line.setText(f"Compute: -- ({err})" if err else "Compute: --")
            self.total_kg_lbl.setText("Total Kg: --")
            self.total_amt_lbl.setText("Total Amount: --")
            return

        ledger = self._ledger_value()

        if is_fert:
            # ✅ bags-only math
            total_amt = float(bags) * float(rate)
            self.compute_line.setText(
                f"Compute: {bags} bags × {rate:,.2f} = {total_amt:,.2f}  |  Ledger: {ledger}"
            )
            self.total_kg_lbl.setText("Total Kg: 0.00")
            self.total_amt_lbl.setText(f"Total Amount: {total_amt:,.2f}")
            return

        # crops math (your existing kg-based)
        total_amt = float(total_kg) * float(rate)
        mode = self.mode_in.currentText()
        if mode == self.MODE_EXACT:
            self.compute_line.setText(f"Compute: {bags} bags (exact) → {total_kg:,.2f}kg × {rate:,.2f} = {total_amt:,.2f}  |  Ledger: {ledger}")
        elif mode == self.MODE_TOTAL:
            self.compute_line.setText(f"Compute: total {total_kg:,.2f}kg × {rate:,.2f} = {total_amt:,.2f}  |  Bags: {bags}  |  Ledger: {ledger}")
        elif mode == self.MODE_AVG:
            avg = float(self.avgkg_in.value())
            self.compute_line.setText(f"Compute: {bags} × {avg:,.2f}kg = {total_kg:,.2f}kg; × {rate:,.2f} = {total_amt:,.2f}  |  Ledger: {ledger}")
        else:
            if bulk_items:
                total_bags = sum(c for c, _kg in bulk_items)
                self.compute_line.setText(f"Compute: bulk {total_bags} bags → {total_kg:,.2f}kg × {rate:,.2f} = {total_amt:,.2f}  |  Ledger: {ledger}")
            else:
                self.compute_line.setText(f"Compute: bulk → {total_kg:,.2f}kg × {rate:,.2f} = {total_amt:,.2f}  |  Ledger: {ledger}")

        self.total_kg_lbl.setText(f"Total Kg: {float(total_kg):,.2f}")
        self.total_amt_lbl.setText(f"Total Amount: {total_amt:,.2f}")

    def payload(self) -> dict:
        is_fert = self._is_fertilizer()
        total_kg, _, weights, bulk_items = self._get_total_kg()
        if total_kg is None:
            total_kg = 0.0

        rate = float(self.rate_in.value())
        bags = int(self.bags_in.value())
        if (not is_fert) and self.mode_in.currentText() == self.MODE_BULK and bulk_items:
            bags = int(sum(c for c, _kg in bulk_items))

        row = self.product_in.currentData()
        row = row if isinstance(row, dict) else {}

        typed = (self.product_in.currentText() or "").strip()

        product_id = int(row.get("id") or 0)
        sku = (row.get("sku") or "").strip()
        product_name = (row.get("name") or "").strip() or typed

        if is_fert:
            total_amt = float(bags) * float(rate)
            return {
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "ledger": "fertilizers",
                "type": "delivery",

                "product_id": product_id,
                "product": product_name,
                "product_name": product_name,
                "sku": sku,

                "bags": bags,
                "mode": "BAGS_ONLY",
                "weights": None,
                "bulk": None,

                "total_kg": 0.0,
                "rate_per_kg": rate,          # reused field == price_per_bag
                "total_amount": float(total_amt),
                "status": "Recorded",
                "unit": "bag",
            }

        # crops
        total_amt = float(total_kg) * float(rate)
        return {
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ledger": self._ledger_value(),
            "type": "delivery",

            "product_id": product_id,
            "product": product_name,
            "product_row": row,

            "product_name": product_name,
            "sku": sku,

            "bags": bags,
            "mode": self.mode_in.currentText(),
            "weights": weights,
            "bulk": bulk_items,

            "total_kg": float(total_kg),
            "rate_per_kg": rate,
            "total_amount": float(total_amt),
            "status": "Recorded",
            "unit": "kg",
        }


    def _reload_products(self):
        from apps.product_management_app.app_state import state

        ledger = self._ledger_value()
        pt = "fertilizer" if ledger == "fertilizers" else "crops"

        rows = state.list_products_db(product_type=pt)

        self.product_in.blockSignals(True)
        self.product_in.clear()
        for r in rows:
            label = f"{r['name']}  ({r['sku']})"
            self.product_in.addItem(label, r)
        self.product_in.blockSignals(False)

        # ✅ UX: if user switches to fertilizers, nudge them immediately
        if ledger == "fertilizers":
            self.compute_line.setText("Compute: -- (Fertilizer is bags-only. Use FertilizerIntakeDialog)")

    def _is_fertilizer(self) -> bool:
        return self._ledger_value() == "fertilizers"

    def _apply_ledger_rules(self):
        is_fert = self._is_fertilizer()

        # Fertilizers: bags-only → disable kg modes & kg inputs & table/bulk
        self.mode_in.setEnabled(not is_fert)

        # Force a stable mode when fertilizer (so UI doesn't confuse)
        if is_fert:
            # We don't use kg in fertilizer anyway; pick any mode and hide its inputs
            self.mode_in.setCurrentText(self.MODE_TOTAL)

        # UI labels
        self.rate_in.setPrefix("")  # optional; keep clean
        if is_fert:
            # Turn "Rate per Kg" into "Price per Bag"
            # simplest: just change the label widget text if you kept a reference
            # If you didn't store it, replace the static QLabel with self.rate_lbl at creation time.
            try:
                self.rate_lbl.setText("Price per Bag *")
            except Exception:
                pass
            self.weights_title.setText("Weights per bag (Kg) *")  # irrelevant but hidden anyway
        else:
            try:
                self.rate_lbl.setText("Rate per Kg *")
            except Exception:
                pass

        # Hide kg controls when fertilizer
        self.totalkg_lbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_TOTAL))
        self.totalkg_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_TOTAL))

        self.avgkg_lbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_AVG))
        self.avgkg_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_AVG))

        self.weights_title.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_EXACT))
        self.weights_tbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_EXACT))

        self.bulk_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_BULK))
        self.bulk_hint.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_BULK))

        self._recalc_totals()

    def _on_save(self):
        product = self._product_text()
        if not product:
            QMessageBox.critical(self, "Validation Error", "Product is required.")
            return

        if float(self.rate_in.value()) <= 0:
            QMessageBox.critical(self, "Validation Error", "Unit price must be > 0.")
            return

        # ✅ Fertilizers: only require bags
        if self._is_fertilizer():
            if int(self.bags_in.value()) <= 0:
                QMessageBox.critical(self, "Validation Error", "Bags must be > 0.")
                return
            self.accept()
            return

        # ✅ Crops: keep kg validation
        total_kg, err, _weights, _bulk_items = self._get_total_kg()
        if total_kg is None:
            QMessageBox.critical(self, "Validation Error", err)
            return
        self.accept()



from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QMessageBox,
    QFormLayout
)

class PaymentDialog(QDialog):
    def __init__(self, parent, ledger: str, due: float):
        super().__init__(parent)
        self.setWindowTitle("Record Payment")
        self.setMinimumWidth(440)

        self.ledger = (ledger or "").strip().lower()
        self.due = float(due or 0.0)

        lay = QVBoxLayout(self)

        info = QLabel(f"Ledger: {self.ledger}   |   Current Due: {self.due:,.2f}")
        info.setStyleSheet("font-weight: 700;")
        lay.addWidget(info)

        form = QFormLayout()
        lay.addLayout(form)

        # -------------------------------------------------
        # 🔥 NEW: Payment Type Selector
        # -------------------------------------------------
        self.cmb_type = QComboBox()
        self.cmb_type.addItems([
            "sale",
            "purchase",
            "advance_received",
            "advance_given",
        ])
        form.addRow("Payment For", self.cmb_type)

        # Mode (only meaningful for sale/purchase)
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["complete", "half", "partial"])
        form.addRow("Mode", self.cmb_mode)

        self.ed_amount = QLineEdit()
        self.ed_amount.setPlaceholderText("Amount")
        form.addRow("Amount", self.ed_amount)

        self.cmb_method = QComboBox()
        self.cmb_method.addItems(["cash", "transfer", "pos", "other"])
        form.addRow("Method", self.cmb_method)

        self.ed_reference = QLineEdit()
        self.ed_reference.setPlaceholderText("Reference (optional)")
        form.addRow("Reference", self.ed_reference)

        self.ed_note = QLineEdit()
        self.ed_note.setPlaceholderText("Note (optional)")
        form.addRow("Note", self.ed_note)

        # Buttons
        btns = QHBoxLayout()
        lay.addLayout(btns)
        btns.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Confirm Payment")
        self.btn_ok.setMinimumHeight(30)

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_confirm)

        # Signals
        self.cmb_mode.currentTextChanged.connect(lambda _t: self._apply_mode_defaults())
        self.cmb_type.currentTextChanged.connect(lambda _t: self._on_type_changed())

        # Defaults
        self._apply_mode_defaults()
        self._on_type_changed()

        from datetime import datetime as _dt
        self._dt = _dt.now().isoformat(timespec="seconds")

        self.GenerateReceipt = True

    # -------------------------------------------------
    # Handle type change
    # -------------------------------------------------
    def _on_type_changed(self):
        typ = self.cmb_type.currentText().strip().lower()

        # Advance modes do not use document due logic
        if typ in ("advance_received", "advance_given"):
            self.cmb_mode.setEnabled(False)
            self.ed_amount.setEnabled(True)
            self.ed_amount.setText("")
        else:
            self.cmb_mode.setEnabled(True)
            self._apply_mode_defaults()

    # -------------------------------------------------
    # Mode logic (sale/purchase only)
    # -------------------------------------------------
    def _apply_mode_defaults(self):
        typ = self.cmb_type.currentText().strip().lower()
        if typ == "advance":
            return

        mode = self.cmb_mode.currentText().strip().lower()

        if mode == "complete":
            self.ed_amount.setText(f"{self.due:.2f}")
            self.ed_amount.setEnabled(False)
        elif mode == "half":
            self.ed_amount.setEnabled(True)
            self.ed_amount.setText(f"{(self.due / 2.0):.2f}")
        else:
            self.ed_amount.setEnabled(True)
            self.ed_amount.setText("")

    # -------------------------------------------------
    # Confirm
    # -------------------------------------------------
    def _on_confirm(self):

        typ = self.cmb_type.currentText().strip().lower()
        mode = self.cmb_mode.currentText().strip().lower()
        method = self.cmb_method.currentText().strip().lower()

        try:
            amount = float(self.ed_amount.text().strip())
        except Exception:
            QMessageBox.warning(self, "Invalid Amount", "Enter a valid amount.")
            return

        if amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than 0.")
            return

        # 🔒 For sale/purchase enforce due cap
        # For sale/purchase enforce due cap
        if typ in ("sale", "purchase"):
            if amount > self.due:
                QMessageBox.warning(self, "Too Much", "Amount cannot exceed current due.")
                return

        self._payload = {
            "payment_for": typ,   # 🔥 critical
            "ledger": self.ledger,
            "mode": mode,
            "amount": amount,
            "method": method,
            "reference": self.ed_reference.text().strip() or None,
            "note": self.ed_note.text().strip() or None,
            "datetime": self._dt,
        }

        self.accept()
        self.GenerateReceipt = True

    def payload(self):
        return getattr(self, "_payload", None)