# apps/product_management_app/features/orders/dialogs/edit_activity_dialog.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from database.models.product import Product
from database.services.order_profile_service import OrderProfileService
from database.session import get_session
from shared.uix.widgets.cards import money


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class EditActivityDialog(QDialog):
    """
    Comprehensive Edit Dialog for Sale and Purchase Orders:
      - Allows editing initial specs: Product, Quality, Price, Bags count, Weight (Kg for crops).
      - Allows editing company verified specs: Company Price, Company Weight, Company Bags, Accepted, Rejected.
      - Expected delivery date and notes.
      - Live recalculation of totals.
    """

    def __init__(self, order_data: Dict[str, Any], partner_name: str, parent=None):
        super().__init__(parent)
        self.order = dict(order_data)
        self.partner_name = partner_name
        self.kind = self.order.get("kind", "sales")
        self.is_sale = self.kind == "sales"
        self.order_id = int(self.order["id"])

        self.products: List[Dict[str, Any]] = []
        self._load_products()

        title_kind = "Sale Order" if self.is_sale else "Purchase Order"
        self.setWindowTitle(f"Edit {title_kind} • {self.order.get('order_no', '')}")
        self.setMinimumWidth(560)
        self._build_ui()

    def _load_products(self):
        with get_session() as s:
            rows = s.query(Product).filter(Product.is_active == True).order_by(Product.name).all()  # noqa: E712
            for p in rows:
                self.products.append({
                    "id": int(p.id),
                    "name": p.name,
                    "type": "fertilizer" if _is_fert(p.product_type) else "crops",
                    "sale_rate": float(p.default_sale_rate or 0.0),
                    "cost_rate": float(p.default_cost_rate or 0.0),
                })

    def _build_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(12)

        # Header Info Box
        info_box = QFrame()
        info_box.setObjectName("info_box")
        info_box.setStyleSheet("""
            QFrame#info_box {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 10px;
            }
            QFrame#info_box QLabel {
                border: none;
                background: transparent;
                padding: 0px;
            }
        """)
        ib_lay = QVBoxLayout(info_box)
        ib_lay.setSpacing(4)

        t_lbl = QLabel(f"<b>Edit Order {self.order.get('order_no', '')}</b> • {self.partner_name}")
        t_lbl.setStyleSheet("font-size: 15px; color: #0f172a;")
        ib_lay.addWidget(t_lbl)

        st = str(self.order.get("status", "draft")).replace("_", " ").title()
        sub_lbl = QLabel(f"Activity: <b>{'Sale Order' if self.is_sale else 'Purchase Order'}</b>  |  Status: <b>{st}</b>")
        sub_lbl.setStyleSheet("color: #475569; font-size: 12px;")
        ib_lay.addWidget(sub_lbl)
        main_lay.addWidget(info_box)

        # Form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)

        # 1. Product
        self.cbo_product = QComboBox()
        self.cbo_product.setMinimumHeight(30)
        curr_pid = self.order.get("product_id")
        sel_idx = 0
        for i, p in enumerate(self.products):
            t_label = "Fertilizer" if p["type"] == "fertilizer" else "Crops"
            self.cbo_product.addItem(f"{p['name']} ({t_label})", p["id"])
            if curr_pid and p["id"] == curr_pid:
                sel_idx = i
        self.cbo_product.setCurrentIndex(sel_idx)
        self.cbo_product.currentIndexChanged.connect(self._on_product_changed)
        form.addRow("<b>Product:</b>", self.cbo_product)

        # 2. Quality
        self.txt_quality = QLineEdit(self.order.get("quality") or "")
        self.txt_quality.setPlaceholderText("Grade, quality specs, variety, etc.")
        form.addRow("Quality / Spec:", self.txt_quality)

        # 3. Initial Price
        init_price = self.order.get("initial_price") or 0.0
        self.txt_initial_price = QLineEdit(f"{init_price:,.2f}" if init_price else "")
        self.txt_initial_price.textChanged.connect(self._recalc)
        self.lbl_initial_price = QLabel("Initial Price (₦):")
        form.addRow(self.lbl_initial_price, self.txt_initial_price)

        # 4. Bags Count
        init_bags = self.order.get("initial_qty_bags") or 0.0
        self.txt_bags = QLineEdit(f"{init_bags:,.0f}" if init_bags else "")
        self.txt_bags.textChanged.connect(self._recalc)
        form.addRow("Bags Count:", self.txt_bags)

        # 5. Weight Kg (Crops)
        init_kg = self.order.get("initial_weight_kg") or 0.0
        self.txt_weight = QLineEdit(f"{init_kg:,.2f}" if init_kg else "")
        self.txt_weight.textChanged.connect(self._recalc)
        self.row_weight_lbl = QLabel("Weight (Kg):")
        form.addRow(self.row_weight_lbl, self.txt_weight)

        # Section: Company Verified (if applicable or confirmed)
        self.comp_box = QFrame()
        self.comp_box.setObjectName("comp_box")
        self.comp_box.setStyleSheet("""
            QFrame#comp_box {
                background: #f1f5f9;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px;
            }
            QFrame#comp_box QLabel {
                background: transparent;
                border: none;
            }
        """)
        c_form = QFormLayout(self.comp_box)
        c_form.setLabelAlignment(Qt.AlignRight)
        c_form.setSpacing(6)

        hdr_comp = QLabel("<b>Company / Supplier Verified Specifications (Finalization):</b>")
        hdr_comp.setStyleSheet("color: #0f766e; font-size: 12px;")
        c_form.addRow(hdr_comp)

        comp_p = self.order.get("company_price")
        self.txt_comp_price = QLineEdit(f"{comp_p:,.2f}" if comp_p is not None else "")
        self.txt_comp_price.textChanged.connect(self._recalc)
        self.lbl_comp_price = QLabel("Company Price (₦):")
        c_form.addRow(self.lbl_comp_price, self.txt_comp_price)

        comp_b = self.order.get("company_qty_bags")
        self.txt_comp_bags = QLineEdit(f"{comp_b:,.0f}" if comp_b is not None else "")
        self.txt_comp_bags.textChanged.connect(self._recalc)
        c_form.addRow("Company Bags:", self.txt_comp_bags)

        comp_w = self.order.get("company_weight_kg")
        self.txt_comp_weight = QLineEdit(f"{comp_w:,.2f}" if comp_w is not None else "")
        self.txt_comp_weight.textChanged.connect(self._recalc)
        self.row_comp_w_lbl = QLabel("Company Weight (Kg):")
        c_form.addRow(self.row_comp_w_lbl, self.txt_comp_weight)

        acc_val = self.order.get("accepted_qty_bags") if _is_fert(self.order.get("product_type")) else self.order.get("accepted_weight_kg")
        rej_val = self.order.get("rejected_qty_bags") if _is_fert(self.order.get("product_type")) else self.order.get("rejected_weight_kg")
        self.txt_accepted = QLineEdit(f"{acc_val:,.2f}" if acc_val is not None else "")
        self.txt_accepted.textChanged.connect(self._recalc)
        self.txt_rejected = QLineEdit(f"{rej_val:,.2f}" if rej_val is not None else "")
        self.txt_rejected.textChanged.connect(self._recalc)
        self.lbl_acc = QLabel("Accepted Quantity:")
        self.lbl_rej = QLabel("Rejected Quantity:")
        c_form.addRow(self.lbl_acc, self.txt_accepted)
        c_form.addRow(self.lbl_rej, self.txt_rejected)

        form.addRow(self.comp_box)

        # Expected Delivery
        self.dt_expected = QDateEdit()
        self.dt_expected.setCalendarPopup(True)
        exp = self.order.get("expected_delivery_date")
        if exp and hasattr(exp, "year"):
            self.dt_expected.setDate(QDate(exp.year, exp.month, exp.day))
        else:
            self.dt_expected.setDate(QDate.currentDate())
        form.addRow("Expected Date:", self.dt_expected)

        # Notes
        self.txt_notes = QLineEdit(self.order.get("notes") or "")
        self.txt_notes.setPlaceholderText("Order notes, truck plate, driver name, etc.")
        form.addRow("Notes:", self.txt_notes)

        main_lay.addLayout(form)

        # Live calculation box
        self.calc_box = QFrame()
        self.calc_box.setObjectName("calc_box")
        self.calc_box.setStyleSheet("""
            QFrame#calc_box {
                background: #f8fafc;
                border: 1.5px solid #cbd5e1;
                border-radius: 6px;
                padding: 10px;
            }
            QFrame#calc_box QLabel {
                border: none;
                background: transparent;
                padding: 0px;
            }
        """)
        cb_lay = QHBoxLayout(self.calc_box)
        self.lbl_calc_desc = QLabel("Total Calculation:")
        self.lbl_calc_desc.setStyleSheet("font-weight: bold; color: #475569;")
        self.lbl_calc_val = QLabel("₦0.00")
        self.lbl_calc_val.setStyleSheet("font-size: 16px; font-weight: bold; color: #047857;")
        cb_lay.addWidget(self.lbl_calc_desc)
        cb_lay.addStretch()
        cb_lay.addWidget(self.lbl_calc_val)
        main_lay.addWidget(self.calc_box)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("Save Changes")
        self.btn_save.setStyleSheet(
            "background-color: #0f766e; color: white; font-weight: bold; padding: 8px 24px; border-radius: 4px;"
        )
        self.btn_save.clicked.connect(self._save)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_save)
        main_lay.addLayout(btn_box)

        self._on_product_changed()

    def _selected_product(self) -> Optional[Dict[str, Any]]:
        idx = self.cbo_product.currentIndex()
        if 0 <= idx < len(self.products):
            return self.products[idx]
        return None

    def _on_product_changed(self):
        p = self._selected_product()
        if not p:
            return

        is_fert = p["type"] == "fertilizer"
        if is_fert:
            self.row_weight_lbl.setVisible(False)
            self.txt_weight.setVisible(False)
            self.lbl_initial_price.setText("Price (₦ per Bag):")
            self.row_comp_w_lbl.setVisible(False)
            self.txt_comp_weight.setVisible(False)
            self.lbl_acc.setText("Accepted Bags:")
            self.lbl_rej.setText("Rejected Bags:")
        else:
            self.row_weight_lbl.setVisible(True)
            self.txt_weight.setVisible(True)
            self.lbl_initial_price.setText("Price (₦ per Kg):")
            self.row_comp_w_lbl.setVisible(True)
            self.txt_comp_weight.setVisible(True)
            self.lbl_acc.setText("Accepted Weight (Kg):")
            self.lbl_rej.setText("Rejected Weight (Kg):")

        self._recalc()

    def _recalc(self):
        p = self._selected_product()
        if not p:
            return
        is_fert = p["type"] == "fertilizer"

        init_p = _f(self.txt_initial_price.text())
        init_b = _f(self.txt_bags.text())
        init_w = _f(self.txt_weight.text())

        comp_p = _f(self.txt_comp_price.text())
        acc_q = _f(self.txt_accepted.text())

        # If company values are entered, calculate verified final amount
        if comp_p > 0 and acc_q > 0:
            total = acc_q * comp_p
            self.lbl_calc_desc.setText("Final Verified Amount:")
            self.lbl_calc_val.setText(f"₦{total:,.2f}")
        else:
            # Estimated amount from initial
            qty = init_b if is_fert else init_w
            est = qty * init_p
            self.lbl_calc_desc.setText("Estimated Initial Total:")
            self.lbl_calc_val.setText(f"₦{est:,.2f}")

    def _save(self):
        p = self._selected_product()
        if not p:
            QMessageBox.warning(self, "Validation", "Select a product.")
            return

        is_fert = p["type"] == "fertilizer"
        init_p = _f(self.txt_initial_price.text())
        init_b = _f(self.txt_bags.text())
        init_w = _f(self.txt_weight.text())

        if init_p <= 0:
            QMessageBox.warning(self, "Validation", "Please enter a valid price.")
            return

        if is_fert and init_b <= 0:
            QMessageBox.warning(self, "Validation", "Please enter the number of bags.")
            return

        if not is_fert:
            if init_w <= 0:
                QMessageBox.warning(self, "Validation", "Please enter the weight in kg.")
                return
            if init_b <= 0:
                QMessageBox.warning(self, "Validation", "Please enter the bags count.")
                return

        exp_qdate = self.dt_expected.date()
        exp_date = exp_qdate.toPython()

        comp_p = _f(self.txt_comp_price.text()) if self.txt_comp_price.text().strip() else None
        comp_b = _f(self.txt_comp_bags.text()) if self.txt_comp_bags.text().strip() else None
        comp_w = _f(self.txt_comp_weight.text()) if self.txt_comp_weight.text().strip() else None
        acc_q = _f(self.txt_accepted.text()) if self.txt_accepted.text().strip() else None
        rej_q = _f(self.txt_rejected.text()) if self.txt_rejected.text().strip() else None

        try:
            OrderProfileService.edit_activity(
                kind=self.kind,
                order_id=self.order_id,
                product_id=p["id"],
                quality=self.txt_quality.text(),
                initial_bags=init_b,
                initial_weight_kg=init_w,
                initial_price=init_p,
                expected_delivery=exp_date,
                notes=self.txt_notes.text(),
                company_price=comp_p,
                company_weight_kg=comp_w,
                company_qty_bags=comp_b,
                accepted_qty=acc_q,
                rejected_qty=rej_q,
            )
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Changes", str(e))
            return

        self.accept()
