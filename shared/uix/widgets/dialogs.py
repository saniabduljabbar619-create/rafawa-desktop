# shared/uix/widgets/dialogs.py
import re

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QMessageBox, QWidget, QHeaderView
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor

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
from PySide2.QtWidgets import QTableWidget, QTableWidgetItem


class DeliveryIntakeDialog(QDialog):
    """
    Delivery/Intake supports:
      - Exact mode: one row per "bags round" - bags, weight (kg) and price per kg
        (the row total is computed); bags/kg/amount are the sums of the rows
      - Quick mode: total kg input
      - Quick mode: avg kg per bag input
      - Bulk mode: bag-count x kg pattern lines (for 500+ bags)
    Always:
      total_amount = total_kg * rate_per_kg
    """

    SELECT_PRODUCT = "Select Product"

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
        self.product_in.lineEdit().setPlaceholderText(self.SELECT_PRODUCT)
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
        self.mode_in.setCurrentText(self.MODE_TOTAL)          # the everyday mode is the default
        row.addWidget(self.mode_in, 2)

        row.addSpacing(10)

        self.bags_lbl = QLabel("Bags *")
        row.addWidget(self.bags_lbl)
        self.bags_in = QSpinBox()
        self.bags_in.setMinimum(1)
        self.bags_in.setMaximum(100000)
        self.bags_in.setValue(max(1, int(initial_bags)))
        row.addWidget(self.bags_in, 1)

        row.addSpacing(10)

        self.rate_lbl = QLabel("Price Per Kg *")
        row.addWidget(self.rate_lbl)
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
        self.bulk_lbl = QLabel("Bulk Pattern (one per line)")
        layout.addWidget(self.bulk_lbl)
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

        # Exact mode: one row per round of bags
        self.exact_box = QWidget()
        ev = QVBoxLayout(self.exact_box)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(6)
        self.weights_title = QLabel("Rounds - one row per batch of bags (bags, total weight, price per kg) *")
        ev.addWidget(self.weights_title)

        self.weights_tbl = QTableWidget(0, 4)
        self.weights_tbl.setHorizontalHeaderLabels(["Bags Round", "Weight (kg)", "Price Per Kg", "Total"])
        self.weights_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.weights_tbl.verticalHeader().setVisible(False)
        self.weights_tbl.setEditTriggers(QTableWidget.AllEditTriggers)
        self.weights_tbl.setMinimumHeight(170)
        ev.addWidget(self.weights_tbl)

        round_btns = QHBoxLayout()
        self.btn_add_round = QPushButton("+ Add Round")
        self.btn_remove_round = QPushButton("Remove Round")
        round_btns.addWidget(self.btn_add_round)
        round_btns.addWidget(self.btn_remove_round)
        round_btns.addStretch(1)
        ev.addLayout(round_btns)
        layout.addWidget(self.exact_box)
        self._rounds_busy = False

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
        self.bags_in.valueChanged.connect(lambda _n: self._recalc_totals())
        self.btn_add_round.clicked.connect(lambda: self._add_round_row())
        self.btn_remove_round.clicked.connect(self._remove_round_row)
        self.rate_in.valueChanged.connect(self._recalc_totals)
        self.totalkg_in.valueChanged.connect(self._recalc_totals)
        self.avgkg_in.valueChanged.connect(self._recalc_totals)
        self.weights_tbl.itemChanged.connect(self._on_round_changed)
        self.bulk_in.textChanged.connect(self._recalc_totals)
        self.ledger_in.currentTextChanged.connect(lambda _t: self._recalc_totals())

        self.ledger_in.currentTextChanged.connect(lambda _t: self._apply_ledger_rules())
        self._apply_ledger_rules()

        # init
        self._add_round_row()
        self._apply_mode(self.mode_in.currentText())
        self._reload_products()



    def _apply_mode(self, mode: str):
        is_exact = (mode == self.MODE_EXACT)
        is_total = (mode == self.MODE_TOTAL)
        is_avg = (mode == self.MODE_AVG)
        is_bulk = (mode == self.MODE_BULK)

        self.exact_box.setVisible(is_exact)
        self._sync_exact_fields()

        self.totalkg_lbl.setVisible(is_total)
        self.totalkg_in.setVisible(is_total)

        self.avgkg_lbl.setVisible(is_avg)
        self.avgkg_in.setVisible(is_avg)

        self.bulk_lbl.setVisible(is_bulk)
        self.bulk_in.setVisible(is_bulk)
        self.bulk_hint.setVisible(is_bulk)

        self._recalc_totals()

    def _selected_product_row(self) -> dict:
        row = self.product_in.currentData()
        return row if isinstance(row, dict) else {}

    def _product_text(self) -> str:
        txt = (self.product_in.currentText() or "").strip()
        return "" if txt.lower() == self.SELECT_PRODUCT.lower() else txt


    # ---- exact mode: rounds -------------------------------------------------
    def _sync_exact_fields(self):
        """In exact crops mode the rounds table owns bags and price, so the two top fields step aside."""
        exact = (not self._is_fertilizer()) and self.mode_in.currentText() == self.MODE_EXACT
        for w in (self.bags_lbl, self.bags_in, self.rate_lbl, self.rate_in):
            w.setVisible(not exact)

    def _add_round_row(self):
        t = self.weights_tbl
        self._rounds_busy = True
        try:
            r = t.rowCount()
            t.insertRow(r)
            for c in range(3):
                it = QTableWidgetItem("")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, it)
            tot = QTableWidgetItem("")
            tot.setFlags(Qt.ItemIsEnabled)
            tot.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tot.setForeground(QColor("#047857"))
            f = tot.font()
            f.setBold(True)
            tot.setFont(f)
            t.setItem(r, 3, tot)
        finally:
            self._rounds_busy = False

    def _remove_round_row(self):
        t = self.weights_tbl
        r = t.currentRow()
        if r < 0:
            r = t.rowCount() - 1
        if r >= 0:
            t.removeRow(r)
        if t.rowCount() == 0:
            self._add_round_row()
        self._recalc_totals()

    @staticmethod
    def _num(txt):
        txt = (txt or "").replace(",", "").strip()
        try:
            return float(txt)
        except ValueError:
            return None

    @staticmethod
    def _parse_bags(txt):
        m = re.match(r"^\s*(\d+)\s*(bags?)?\s*$", txt or "", re.IGNORECASE)
        return int(m.group(1)) if m else None

    def _row_texts(self, r):
        out = []
        for c in range(3):
            it = self.weights_tbl.item(r, c)
            out.append(it.text().strip() if it else "")
        return out

    def _on_round_changed(self, item):
        if self._rounds_busy or item.column() == 3:
            return
        t = self.weights_tbl
        r = item.row()
        bags, kg, price = self._row_texts(r)
        k, p = self._num(kg), self._num(price)
        self._rounds_busy = True
        try:
            total_item = t.item(r, 3)
            if total_item is not None:
                total_item.setText(f"{k * p:,.2f}" if k and p and k > 0 and p > 0 else "")
        finally:
            self._rounds_busy = False
        # keep one blank row at the bottom so the next round can be typed straight away
        if any(self._row_texts(t.rowCount() - 1)):
            self._add_round_row()
        self._recalc_totals()

    def _read_rounds(self):
        """-> ([(bags, kg, price_per_kg), ...], "") or (None, error). Blank rows are ignored."""
        rounds = []
        for r in range(self.weights_tbl.rowCount()):
            texts = self._row_texts(r)
            if not any(texts):
                continue
            bags = self._parse_bags(texts[0])
            kg = self._num(texts[1])
            price = self._num(texts[2])
            if bags is None or bags <= 0:
                return None, f"Round #{r + 1}: enter the number of bags."
            if kg is None or kg <= 0:
                return None, f"Round #{r + 1}: enter the weight in kg."
            if price is None or price <= 0:
                return None, f"Round #{r + 1}: enter the price per kg."
            rounds.append((bags, kg, price))
        if not rounds:
            return None, "Add at least one round (bags, weight and price per kg)."
        return rounds, ""

    def _exact_totals(self):
        """-> (bags, kg, amount, rounds, error)"""
        rounds, err = self._read_rounds()
        if rounds is None:
            return 0, 0.0, 0.0, None, err
        return (sum(b for b, _k, _p in rounds), sum(k for _b, k, _p in rounds),
                sum(k * p for _b, k, p in rounds), rounds, "")

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
            _bags, kg, _amt, rounds, err = self._exact_totals()
            if rounds is None:
                return None, err, None, None
            return float(kg), "", [k for _b, k, _p in rounds], None

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
        if (not is_fert) and self.mode_in.currentText() == self.MODE_EXACT:
            bags, kg, amt, rounds, err = self._exact_totals()
            if rounds is None:
                self.compute_line.setText(f"Compute: -- ({err})")
                self.total_kg_lbl.setText("Total Kg: --")
                self.total_amt_lbl.setText("Total Amount: --")
                return
            n = len(rounds)
            self.compute_line.setText(
                f"Compute: {n} round{'s' if n != 1 else ''}, {bags} bags, {kg:,.2f}kg "
                f"= {amt:,.2f}  |  Ledger: {self._ledger_value()}")
            self.total_kg_lbl.setText(f"Total Kg: {kg:,.2f}   |   Bags: {bags}")
            self.total_amt_lbl.setText(f"Total Amount: {amt:,.2f}")
            return

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
        exact_amount = None
        if (not is_fert) and self.mode_in.currentText() == self.MODE_EXACT:
            e_bags, e_kg, e_amt, rounds, _err = self._exact_totals()
            if rounds:
                bags, total_kg, exact_amount = e_bags, e_kg, e_amt
                rate = e_amt / e_kg          # weighted average, so kg x rate == the sum of the rows
        if (not is_fert) and self.mode_in.currentText() == self.MODE_BULK and bulk_items:
            bags = int(sum(c for c, _kg in bulk_items))

        row = self.product_in.currentData()
        row = row if isinstance(row, dict) else {}

        typed = self._product_text()

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
        total_amt = exact_amount if exact_amount is not None else float(total_kg) * float(rate)
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
        # Always start on the placeholder, even when products exist, so nothing
        # is picked by accident.
        self.product_in.addItem(self.SELECT_PRODUCT, None)
        for r in rows:
            label = f"{r['name']}  ({r['sku']})"
            self.product_in.addItem(label, r)
        self.product_in.setCurrentIndex(0)
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
            self.rate_lbl.setText("Price Per Bag *")
        else:
            self.rate_lbl.setText("Price Per Kg *")

        # Hide kg controls when fertilizer
        self.totalkg_lbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_TOTAL))
        self.totalkg_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_TOTAL))

        self.avgkg_lbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_AVG))
        self.avgkg_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_AVG))

        self.exact_box.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_EXACT))
        self._sync_exact_fields()

        self.bulk_lbl.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_BULK))
        self.bulk_in.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_BULK))
        self.bulk_hint.setVisible((not is_fert) and (self.mode_in.currentText() == self.MODE_BULK))

        self._recalc_totals()

    def _on_save(self):
        product = self._product_text()
        if not product:
            QMessageBox.critical(self, "Validation Error", "Select a product first.")
            return

        # Exact crops: the rounds table carries bags, weight and price
        if (not self._is_fertilizer()) and self.mode_in.currentText() == self.MODE_EXACT:
            _b, _k, _a, rounds, err = self._exact_totals()
            if rounds is None:
                QMessageBox.critical(self, "Validation Error", err)
                return
            self.accept()
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



from PySide2.QtWidgets import QFormLayout

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