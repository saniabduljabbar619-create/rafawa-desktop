# apps/product_management_app/features/orders/dialogs/new_activity_dialog.py
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QButtonGroup,
    QVBoxLayout, QWidget, QFrame,
)

from database.models.product import Product
from database.services.order_profile_service import OrderProfileService
from database.session import get_session


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class NewActivityDialog(QDialog):
    """
    Streamlined Dialog to start a New Activity (Sale Order or Purchase Order):
      - 🟢 Sale Order: sell goods to company
      - 🔵 Purchase Order: buy goods from supplier
      - Records: Product, Quality, Quantity (Bags / Kg), Price
    """

    def __init__(self, partner_name: str, company_id: Optional[int], supplier_id: Optional[int],
                 default_activity: str = "sales", parent=None):
        super().__init__(parent)
        self.partner_name = partner_name
        self.company_id = company_id
        self.supplier_id = supplier_id
        self.created_order_id: Optional[int] = None
        self.created_kind: str = "sales"

        self.products: List[Dict[str, Any]] = []
        self._load_products()

        self.setWindowTitle(f"New Activity • {self.partner_name}")
        self.setMinimumWidth(540)
        self._build_ui(default_activity)

    def _load_products(self):
        with get_session() as s:
            rows = s.query(Product).filter(Product.is_active == True).order_by(Product.name).all()  # noqa: E712
            for p in rows:
                self.products.append({
                    "id": int(p.id),
                    "name": p.name,
                    "sku": p.sku or "",
                    "type": "fertilizer" if _is_fert(p.product_type) else "crops",
                    "sale_rate": float(p.default_sale_rate or 0.0),
                    "cost_rate": float(p.default_cost_rate or 0.0),
                })

    def _build_ui(self, default_activity: str):
        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(12)

        # Header
        hdr = QLabel(f"<b>New Activity for {self.partner_name}</b>")
        hdr.setStyleSheet("font-size: 16px; color: #0f172a;")
        main_lay.addWidget(hdr)

        # Activity Type Selector: Radio buttons
        act_box = QFrame()
        act_box.setStyleSheet("background: #f1f5f9; border-radius: 6px; padding: 6px;")
        act_lay = QHBoxLayout(act_box)
        act_lay.setContentsMargins(10, 6, 10, 6)

        lbl_choose = QLabel("<b>Select Activity:</b>")
        act_lay.addWidget(lbl_choose)

        self.btn_group = QButtonGroup(self)
        self.rad_sale = QRadioButton("🟢  Sale Order  (Sell to Company)")
        self.rad_purchase = QRadioButton("🔵  Purchase Order  (Buy from Supplier)")
        self.btn_group.addButton(self.rad_sale, 1)
        self.btn_group.addButton(self.rad_purchase, 2)

        if str(default_activity).lower() in ("purchase", "supplier"):
            self.rad_purchase.setChecked(True)
        else:
            self.rad_sale.setChecked(True)

        self.rad_sale.toggled.connect(self._on_activity_changed)
        act_lay.addWidget(self.rad_sale)
        act_lay.addWidget(self.rad_purchase)
        act_lay.addStretch()
        main_lay.addWidget(act_box)

        # Form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # Product
        self.cbo_product = QComboBox()
        self.cbo_product.setMinimumHeight(32)
        for p in self.products:
            t_label = "Fertilizer" if p["type"] == "fertilizer" else "Crops"
            self.cbo_product.addItem(f"{p['name']} ({t_label})", p["id"])
        self.cbo_product.currentIndexChanged.connect(self._on_product_changed)
        form.addRow("Product:", self.cbo_product)

        # Quality
        self.txt_quality = QLineEdit()
        self.txt_quality.setPlaceholderText("e.g. Grade 1 Dry, White Maize, Clean 50kg, etc.")
        form.addRow("Quality / Spec:", self.txt_quality)

        # Quantity: Weight (Kg)
        self.txt_weight = QLineEdit()
        self.txt_weight.setPlaceholderText("e.g. 30000")
        self.txt_weight.textChanged.connect(self._recalc_total)
        self.row_weight_lbl = QLabel("Weight (Kg):")
        form.addRow(self.row_weight_lbl, self.txt_weight)

        # Quantity: Bags count
        self.txt_bags = QLineEdit()
        self.txt_bags.setPlaceholderText("e.g. 600")
        self.txt_bags.textChanged.connect(self._recalc_total)
        self.row_bags_lbl = QLabel("Bags Count:")
        form.addRow(self.row_bags_lbl, self.txt_bags)

        # Price
        self.txt_price = QLineEdit()
        self.txt_price.setPlaceholderText("0.00")
        self.txt_price.textChanged.connect(self._recalc_total)
        self.lbl_price_row = QLabel("Price (₦ per Kg):")
        form.addRow(self.lbl_price_row, self.txt_price)

        # Expected Delivery
        self.dt_expected = QDateEdit()
        self.dt_expected.setCalendarPopup(True)
        self.dt_expected.setDate(QDate.currentDate())
        form.addRow("Expected Date:", self.dt_expected)

        # Notes
        self.txt_notes = QLineEdit()
        self.txt_notes.setPlaceholderText("Optional order notes, truck plate number, etc.")
        form.addRow("Notes:", self.txt_notes)

        # Dispatch checkbox
        self.chk_dispatch = QCheckBox("Dispatch immediately (Mark on the road / in-transit)")
        self.chk_dispatch.setChecked(True)
        form.addRow("", self.chk_dispatch)

        main_lay.addLayout(form)

        # Live calculation preview
        self.calc_box = QFrame()
        self.calc_box.setObjectName("calc_box")
        self.calc_box.setStyleSheet("""
            QFrame#calc_box {
                background: #f8fafc;
                border: 1.5px solid #cbd5e1;
                border-radius: 6px;
            }
            QFrame#calc_box QLabel {
                border: none;
                background: transparent;
                padding: 0px;
            }
        """)
        c_lay = QHBoxLayout(self.calc_box)
        c_lay.setContentsMargins(12, 10, 12, 10)
        self.lbl_calc_desc = QLabel("Estimated Value:")
        self.lbl_calc_desc.setStyleSheet("font-weight: bold; color: #475569;")
        self.lbl_calc_val = QLabel("₦0.00")
        self.lbl_calc_val.setStyleSheet("font-size: 16px; font-weight: bold; color: #0f172a;")
        c_lay.addWidget(self.lbl_calc_desc)
        c_lay.addStretch()
        c_lay.addWidget(self.lbl_calc_val)
        main_lay.addWidget(self.calc_box)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_submit = QPushButton("Create Activity")
        self.btn_submit.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px;"
        )
        self.btn_submit.clicked.connect(self._submit)

        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_submit)
        main_lay.addLayout(btn_box)

        # Trigger product load
        self._on_product_changed()

    def _selected_product(self) -> Optional[Dict[str, Any]]:
        idx = self.cbo_product.currentIndex()
        if 0 <= idx < len(self.products):
            return self.products[idx]
        return None

    def _on_activity_changed(self):
        self._on_product_changed()

    def _on_product_changed(self):
        p = self._selected_product()
        if not p:
            return

        is_fert = p["type"] == "fertilizer"
        is_sale = self.rad_sale.isChecked()

        if is_fert:
            self.row_weight_lbl.setVisible(False)
            self.txt_weight.setVisible(False)
            self.txt_weight.setText("0")
            self.row_bags_lbl.setText("Bags Count:")
            self.lbl_price_row.setText("Price (₦ per Bag):")
            default_rate = p["sale_rate"] if is_sale else p["cost_rate"]
            if not self.txt_price.text() or self.txt_price.text() == "0.00":
                self.txt_price.setText(f"{default_rate:,.2f}" if default_rate else "")
        else:
            self.row_weight_lbl.setVisible(True)
            self.txt_weight.setVisible(True)
            self.row_bags_lbl.setText("Bags Count:")
            self.lbl_price_row.setText("Price (₦ per Kg):")
            default_rate = p["sale_rate"] if is_sale else p["cost_rate"]
            if not self.txt_price.text() or self.txt_price.text() == "0.00":
                self.txt_price.setText(f"{default_rate:,.2f}" if default_rate else "")

        self._recalc_total()

    def _recalc_total(self):
        p = self._selected_product()
        if not p:
            self.lbl_calc_val.setText("₦0.00")
            return

        is_fert = p["type"] == "fertilizer"
        price = _f(self.txt_price.text())
        if is_fert:
            bags = _f(self.txt_bags.text())
            total = bags * price
        else:
            kg = _f(self.txt_weight.text())
            total = kg * price

        self.lbl_calc_val.setText(f"₦{total:,.2f}")

    def _submit(self):
        p = self._selected_product()
        if not p:
            QMessageBox.warning(self, "Validation Error", "Please select a product.")
            return

        is_sale = self.rad_sale.isChecked()
        kind = "sales" if is_sale else "purchase"
        partner_id = self.company_id if is_sale else self.supplier_id
        if not partner_id:
            partner_id = self.supplier_id if is_sale else self.company_id

        if not partner_id:
            QMessageBox.critical(self, "Error", "Partner ID could not be resolved.")
            return

        is_fert = p["type"] == "fertilizer"
        bags = _f(self.txt_bags.text())
        weight = _f(self.txt_weight.text())
        price = _f(self.txt_price.text())

        if is_fert:
            if bags <= 0:
                QMessageBox.warning(self, "Validation Error", "Please enter the number of fertilizer bags.")
                self.txt_bags.setFocus()
                return
            weight = 0.0
        else:
            if weight <= 0:
                QMessageBox.warning(self, "Validation Error", "Please enter the total weight in kg.")
                self.txt_weight.setFocus()
                return
            if bags <= 0:
                QMessageBox.warning(self, "Validation Error", "Please enter the bags count.")
                self.txt_bags.setFocus()
                return

        if price <= 0:
            QMessageBox.warning(self, "Validation Error", "Please enter a valid price.")
            self.txt_price.setFocus()
            return

        quality = self.txt_quality.text().strip()
        qd = self.dt_expected.date()
        exp_date = date(qd.year(), qd.month(), qd.day())
        notes = self.txt_notes.text().strip()
        dispatch_now = self.chk_dispatch.isChecked()

        try:
            order_id = OrderProfileService.create_activity(
                kind=kind,
                partner_id=partner_id,
                product_id=p["id"],
                quality=quality,
                bags=bags,
                weight_kg=weight,
                price=price,
                expected_delivery=exp_date,
                notes=notes,
                dispatch_now=dispatch_now,
            )
            self.created_order_id = order_id
            self.created_kind = kind
            act_name = "Sale Order" if is_sale else "Purchase Order"
            QMessageBox.information(
                self, "Activity Created",
                f"Successfully created {act_name} #{order_id} for {self.partner_name}."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Create Activity", str(e))
