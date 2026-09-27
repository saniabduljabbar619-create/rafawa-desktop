# apps/product_management_app/features/cashier/dialogs/sale_item_dialog.py
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QDoubleSpinBox, QMessageBox, QGroupBox, QFrame, QSpinBox
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from database.services.sales_service import SaleService


class SaleItemDialog(QDialog):
    """
    Rafawa Cashier Sales Dialog (Client-Truth Canonical Version)

    Canonical outputs (NO inference):
    - qty_bags (int > 0)
    - total_weight_kg (float > 0)

    Pricing outputs:
    - unit_cost_at_sale snapshot (from product.cost_price)
    - unit_price (selling rate per kg OR per bag; your sales_service defines revenue calc)
    """

    def __init__(self, parent=None, product_id: int = 0, product_name: str = "", product_type: str = "crops", unit_price: float = 0.0):

        super().__init__(parent)

        self.product_type = (product_type or "").strip().lower()
        self.is_fert = self.product_type in ("fertilizer", "fertilizers")

        self.setModal(True)
        self.resize(580, 420)
        self.setWindowTitle("Add Sale Item")

        self.product_id = int(product_id)
        self.product_name = product_name
        self.base_price = float(unit_price)

        # snapshot cost price
        self.cost_price = float(self._get_cost_price(self.product_id) or 0.0)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel(f"Sell: {product_name}")
        title.setObjectName("Title")
        root.addWidget(title)

        # Variant/category
        rowv = QHBoxLayout()
        root.addLayout(rowv)
        rowv.addWidget(QLabel("Type / Variant:"))

        self.variant_cb = QComboBox()
        self.variant_cb.setEditable(False)
        rowv.addWidget(self.variant_cb, 1)

        self.variant_new = QLineEdit()
        self.variant_new.setPlaceholderText("Add new type (optional)")
        rowv.addWidget(self.variant_new, 1)

        self.btn_add_variant = QPushButton("Add")
        rowv.addWidget(self.btn_add_variant)

        # Quantities (STRICT)
        gb_qty = QGroupBox("Quantities (Required)")
        root.addWidget(gb_qty)
        ql = QVBoxLayout(gb_qty)

        r1 = QHBoxLayout()
        ql.addLayout(r1)
        r1.addWidget(QLabel("Bags:"))
        self.qty_bags = QSpinBox()
        self.qty_bags.setMinimum(1)
        self.qty_bags.setMaximum(1_000_000)
        self.qty_bags.setValue(1)
        r1.addWidget(self.qty_bags, 1)

        r2 = QHBoxLayout()
        ql.addLayout(r2)
        r2.addWidget(QLabel("Total weight (kg):"))
        self.total_weight_kg = QDoubleSpinBox()
        self.total_weight_kg.setDecimals(2)
        self.total_weight_kg.setMinimum(0.01)
        self.total_weight_kg.setMaximum(1_000_000_000.0)
        self.total_weight_kg.setValue(1.0)
        r2.addWidget(self.total_weight_kg, 1)

        hint = QLabel("Enter the real bags and the real total kg sold. No automatic conversion.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        ql.addWidget(hint)
        self.hint_label = hint

        # Sale-by-bag mode - CROPS ONLY. Fertilizer is already sold purely
        # by the bag (no kg tracked at all), so this toggle would be
        # meaningless there and is never shown for it.
        #
        # "Per Bag" doesn't change what's actually recorded downstream -
        # SaleService.record()/SaleItem still only ever store kg + a
        # per-kg rate, exactly as before. It only changes how the
        # CASHIER enters the deal: N bags at a price the customer agreed
        # PER BAG, with the real kg figured out from the CURRENT average
        # bag weight in stock (SaleService.get_bag_average_weight) rather
        # than the cashier having to weigh/guess it themselves. The
        # amount charged is always exactly qty_bags * price_per_bag -
        # never re-derived from the average, so it can't drift from what
        # was actually agreed.
        self._bag_avg_info = None  # cached snapshot for the current mode session
        # Explicit flag, deliberately NOT derived from override_row.isVisible():
        # once self.accept() runs (below, in _confirm), the whole dialog
        # starts hiding, which cascades to every child widget's isVisible()
        # regardless of whether it was ever hidden directly - isVisible()
        # reflects the widget AND all its ancestors. cashier_sales.py calls
        # dlg.payload() a SECOND time after dlg.exec_() returns (to actually
        # build the cart item), by which point the dialog is already
        # hidden - so an isVisible() check there would silently see "no
        # override" and fall back to the corrupted stock average instead.
        self._override_active = False

        if not self.is_fert:
            rowm = QHBoxLayout()
            root.addLayout(rowm)
            rowm.addWidget(QLabel("Sell by:"))
            self.sale_mode_cb = QComboBox()
            self.sale_mode_cb.addItems(["Per Kg", "Per Bag (avg weight)"])
            rowm.addWidget(self.sale_mode_cb, 1)

            self.lbl_bag_avg = QLabel("")
            self.lbl_bag_avg.setStyleSheet("color:#888;")
            rowm.addWidget(self.lbl_bag_avg, 2)

            self.sale_mode_cb.currentIndexChanged.connect(self._on_sale_mode_changed)

            # Hidden unless the computed average looks physically wrong
            # (see BAG_WEIGHT_SANITY_MIN/MAX_KG) - the cashier's manual
            # correction when that happens. Created here, in place, so it
            # never needs fragile runtime layout insertion later.
            self.override_row = QFrame()
            row_layout = QHBoxLayout(self.override_row)
            row_layout.setContentsMargins(0, 0, 0, 0)

            self.override_warning = QLabel("")
            self.override_warning.setWordWrap(True)
            self.override_warning.setStyleSheet("color:#b45309;")
            row_layout.addWidget(self.override_warning, 1)

            self.override_input = QDoubleSpinBox()
            self.override_input.setDecimals(2)
            self.override_input.setMinimum(0.0)
            self.override_input.setMaximum(1_000_000.0)
            self.override_input.setSuffix(" kg/bag")
            self.override_input.valueChanged.connect(self._recalc)
            row_layout.addWidget(self.override_input)

            root.addWidget(self.override_row)
            self.override_row.hide()
        else:
            self.sale_mode_cb = None
            self.lbl_bag_avg = None
            self.override_row = None

        # Pricing
        rowp = QHBoxLayout()
        root.addLayout(rowp)

        self.lbl_price = QLabel("Selling price per kg:")
        self.lbl_price.setObjectName("Label")
        self.price = QDoubleSpinBox()
        self.price.setDecimals(2)
        self.price.setMinimum(0.0)
        self.price.setMaximum(1_000_000_000.0)
        self.price.setValue(self.base_price)
        rowp.addWidget(self.price, 1)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setFrameShadow(QFrame.Sunken)
        root.addWidget(div)

        # Summary
        gb_sum = QGroupBox("Summary (Preview)")
        root.addWidget(gb_sum)
        sl = QVBoxLayout(gb_sum)

        self.lbl_cost_price = QLabel(f"Cost basis (cost_price): {self._fmt(self.cost_price)}")
        sl.addWidget(self.lbl_cost_price)

        self.lbl_unit_cost_at_sale = QLabel("Unit cost at sale: 0.00")

        self.lbl_line_total = QLabel("Line total: 0.00")
        self.lbl_line_profit = QLabel("Line profit: 0.00")
        sl.addWidget(self.lbl_unit_cost_at_sale)
        sl.addWidget(self.lbl_line_total)
        sl.addWidget(self.lbl_line_profit)

        # Note
        rown = QHBoxLayout()
        root.addLayout(rown)
        rown.addWidget(QLabel("Note:"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional")
        rown.addWidget(self.note, 1)

        # Buttons
        btns = QHBoxLayout()
        root.addLayout(btns)
        btns.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Add Item")
        self.btn_ok.setObjectName("PrimaryButton")
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)

        # Signals
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._confirm)
        self.btn_add_variant.clicked.connect(self._add_variant)

        self.qty_bags.valueChanged.connect(self._recalc)
        self.total_weight_kg.valueChanged.connect(self._recalc)
        self.price.valueChanged.connect(self._recalc)

        # Init
        self._apply_basis_mode()
        self._load_variants()
        self._recalc()

    # -----------------------------
    # Data helpers
    # -----------------------------
    def _get_cost_price(self, product_id: int) -> float:
        for attr in ("products", "products_by_id", "product_map"):
            try:
                mp = getattr(state, attr, None)
                if isinstance(mp, dict) and product_id in mp:
                    pr = mp[product_id]
                    if isinstance(pr, dict):
                        return float(pr.get("cost_price", 0.0) or 0.0)
                    return float(getattr(pr, "cost_price", 0.0) or 0.0)
            except Exception:
                continue
        return 0.0

    def _fmt(self, v: float) -> str:
        try:
            return f"{float(v):,.2f}"
        except Exception:
            return "0.00"

    def _is_bag_mode(self) -> bool:
        return bool(self.sale_mode_cb) and self.sale_mode_cb.currentIndex() == 1

    # -----------------------------
    # Sale-by-bag mode
    # -----------------------------
    def _on_sale_mode_changed(self):
        if self._is_bag_mode():
            info = SaleService.get_bag_average_weight(self.product_id)

            if info["avg_kg_per_bag"] <= 0:
                QMessageBox.warning(
                    self, "Sell by Bag",
                    "Can't sell this product by the bag right now - there are "
                    "no bags currently on hand to average a weight from.\n\n"
                    "Falling back to Per Kg."
                )
                self.sale_mode_cb.blockSignals(True)
                self.sale_mode_cb.setCurrentIndex(0)
                self.sale_mode_cb.blockSignals(False)
                self._bag_avg_info = None
                self._recalc()
                return

            self._bag_avg_info = info

            if not info["plausible"]:
                # The computed average doesn't look like a real bag - almost
                # certainly historical kg/bag entries for this product are
                # out of sync somewhere. Don't act on it automatically -
                # let the cashier type in the real known weight instead.
                self._show_override_field(info["avg_kg_per_bag"])
            else:
                self._hide_override_field()

            # Kg becomes a computed, read-only display in this mode - the
            # cashier is agreeing a price per bag, not weighing anything.
            self.total_weight_kg.setEnabled(False)
            self.lbl_price.setText("Selling price per bag:")
            self.hint_label.setText(
                "Kg is computed automatically from the current average "
                "bag weight in stock. The amount charged is exactly "
                "bags × price per bag."
            )
        else:
            self._bag_avg_info = None
            self._hide_override_field()
            self.total_weight_kg.setEnabled(True)
            self.lbl_price.setText("Selling price per kg:")
            self.lbl_bag_avg.setText("")
            self.hint_label.setText(
                "Enter the real bags and the real total kg sold. No automatic conversion."
            )

        self._recalc()

    def _show_override_field(self, computed_avg: float):
        self.override_warning.setText(
            f"⚠ Computed average ({self._fmt(computed_avg)} kg/bag) looks "
            f"wrong for this product - likely a data issue in past stock "
            f"entries. Enter the real weight per bag to continue:"
        )
        self.override_input.setValue(0.0)
        self.override_row.show()
        self._override_active = True

    def _hide_override_field(self):
        if self.override_row is not None:
            self.override_row.hide()
        self._override_active = False

    def _override_avg(self) -> float | None:
        """Returns the cashier-entered override, if the override field was
        activated for this mode session (see _override_active's own
        comment for why this can't just check widget visibility) AND has
        a value - None otherwise (meaning: use the computed stock
        average as normal)."""
        if self._override_active:
            v = float(self.override_input.value())
            return v if v > 0 else None
        return None

    # -----------------------------
    # Variants
    # -----------------------------
    def _load_variants(self):
        self.variant_cb.clear()
        items = state.list_variants(self.product_id)
        if items:
            self.variant_cb.addItems(items)
        else:
            self.variant_cb.addItem("Default")

    def _add_variant(self):
        v = self.variant_new.text().strip()
        if not v:
            QMessageBox.information(self, "Variant", "Enter a type/variant name first.")
            return
        state.add_variant(self.product_id, v)
        self.variant_new.clear()
        self._load_variants()
        idx = self.variant_cb.findText(v, Qt.MatchExactly)
        if idx >= 0:
            self.variant_cb.setCurrentIndex(idx)

    # -----------------------------
    # Calculations (pricing is kg-based)
    # -----------------------------
    def _recalc(self):
        qb = int(self.qty_bags.value())
        unit_price = float(self.price.value())
        ucost = float(self.cost_price)

        if self._is_bag_mode() and self._bag_avg_info:
            # Live preview only - uses the cached average (or the
            # cashier's manual override, if the computed one was flagged
            # implausible) from when the mode was switched on, so typing
            # doesn't hit the database on every keystroke. payload()/
            # _confirm() re-derive the final figures fresh right before
            # the item is actually added.
            override = self._override_avg()
            avg = override if override is not None else float(self._bag_avg_info["avg_kg_per_bag"])
            kg = qb * avg

            self.total_weight_kg.blockSignals(True)
            self.total_weight_kg.setValue(kg)
            self.total_weight_kg.blockSignals(False)

            self.lbl_bag_avg.setText(f"Avg weight/bag: {self._fmt(avg)} kg  →  {self._fmt(kg)} kg total")

            total = qb * unit_price   # unit_price here is per-bag while previewing
            basis_qty = kg
            unit_cost_basis = kg if kg > 0 else 0.0
            profit = total - (ucost * unit_cost_basis)
            unit_lbl = "per bag"
        else:
            kg = float(self.total_weight_kg.value())
            basis_qty = float(qb) if self.is_fert else float(kg)
            total = basis_qty * unit_price
            profit = (unit_price - ucost) * basis_qty
            unit_lbl = "per bag" if self.is_fert else "per kg"

        self.lbl_unit_cost_at_sale.setText(f"Unit cost at sale ({unit_lbl}): {self._fmt(ucost)}")
        self.lbl_line_total.setText(f"Line total: {self._fmt(total)}")
        self.lbl_line_profit.setText(f"Line profit: {self._fmt(profit)}")

    # -----------------------------
    # Payload / Validation
    # -----------------------------
    def payload(self) -> dict:
        qty_bags = int(self.qty_bags.value())
        unit_cost_at_sale = float(self.cost_price)

        if self._is_bag_mode():
            # Authoritative, fresh call - re-fetches the average right now
            # rather than trusting whatever was cached when the mode was
            # switched on or last recalculated, so the item that actually
            # gets added always reflects current stock. Raises ValueError
            # (surfaced via validate()) if bags on hand have dropped to
            # zero in the meantime.
            price_per_bag = float(self.price.value())
            override = self._override_avg()
            result = SaleService.price_by_bag(
                self.product_id, qty_bags, price_per_bag,
                override_avg_kg_per_bag=override,
            )

            total_weight_kg = float(result["total_weight_kg"])
            unit_price = float(result["unit_price"])           # per-kg equivalent for SaleItem
            line_total = float(result["line_total"])           # == qty_bags * price_per_bag, exact
            line_profit = line_total - (unit_cost_at_sale * total_weight_kg)

            return {
                "variant": self.variant_cb.currentText().strip(),
                "qty_bags": qty_bags,
                "total_weight_kg": total_weight_kg,
                "unit_price": unit_price,

                # pricing snapshots
                "cost_price": float(self.cost_price),
                "unit_cost_at_sale": unit_cost_at_sale,
                "line_total": line_total,
                "line_profit": float(line_profit),

                # bag-mode extras, purely informational (receipt/audit) -
                # SaleService.record() itself never needs these.
                "sold_by_bag": True,
                "price_per_bag": float(result["price_per_bag"]),
                "avg_kg_per_bag": float(result["avg_kg_per_bag"]),

                "note": self.note.text().strip(),
                "image_key": "",
            }

        total_weight_kg = 0.0 if self.is_fert else float(self.total_weight_kg.value())
        unit_price = float(self.price.value())
        basis_qty = float(qty_bags) if self.is_fert else float(total_weight_kg)

        line_total = basis_qty * unit_price
        line_profit = (unit_price - unit_cost_at_sale) * basis_qty

        return {
            "variant": self.variant_cb.currentText().strip(),
            "qty_bags": qty_bags,
            "total_weight_kg": total_weight_kg,
            "unit_price": unit_price,

            # pricing snapshots
            "cost_price": float(self.cost_price),
            "unit_cost_at_sale": unit_cost_at_sale,
            "line_total": float(line_total),
            "line_profit": float(line_profit),

            "sold_by_bag": False,

            "note": self.note.text().strip(),
            "image_key": "",
        }

    def _confirm(self):
        try:
            p = self.payload()
        except ValueError as e:
            # Raised by SaleService.price_by_bag - e.g. stock ran out of
            # bags to average from between opening this dialog and
            # confirming it.
            QMessageBox.warning(self, "Sell by Bag", str(e))
            return

        err = self.validate(p)
        if err:
            QMessageBox.warning(self, "Validation", err)
            return
        self.accept()

    def validate(self, p: dict) -> str:
        if int(p["qty_bags"]) <= 0:
            return "Bags must be greater than zero."

        if not self.is_fert:
            if float(p["total_weight_kg"]) <= 0:
                return "Total weight (kg) must be greater than zero."

        if float(p["unit_price"]) < 0:
            return "Unit price cannot be negative."

        if p.get("sold_by_bag") and float(p.get("price_per_bag") or 0) <= 0:
            return "Price per bag must be greater than zero."

        return ""


    def _apply_basis_mode(self):
        # fertilizer: bags-only (freeze kg)
        if self.is_fert:
            self.total_weight_kg.setValue(0.0)
            self.total_weight_kg.setEnabled(False)
            self.total_weight_kg.setMinimum(0.0)
            self.lbl_price.setText("Selling price per bag:")
        else:
            self.total_weight_kg.setEnabled(True)
            self.total_weight_kg.setMinimum(0.01)
            self.lbl_price.setText("Selling price per kg:")