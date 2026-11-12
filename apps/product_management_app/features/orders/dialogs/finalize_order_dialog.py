# apps/product_management_app/features/orders/dialogs/finalize_order_dialog.py
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QFrame,
)

from database.services.order_money_service import OrderMoneyService
from database.services.order_profile_service import OrderProfileService


def _f(v) -> float:
    try:
        return float(str(v or 0).replace(",", ""))
    except Exception:
        return 0.0


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class FinalizeOrderDialog(QDialog):
    """
    Single-row Finalization Dialog for Sale and Purchase Orders:
      - Enter Company / Supplier Price, Company Weight/Bags, Accepted/Rejected breakdown.
      - Live calculation of Final Total Amount.
      - Automatically detects available advances in the profile and offers one-click settlement.
    """

    def __init__(self, order_data: Dict[str, Any], partner_name: str, parent=None):
        super().__init__(parent)
        self.order = order_data
        self.partner_name = partner_name
        self.kind = self.order.get("kind", "sales")
        self.is_sale = self.kind == "sales"
        self.is_fert = _is_fert(self.order.get("product_type", "crops"))
        self.partner_id = int(self.order.get("partner_id") or 0)

        # Look up available advance for this partner
        direction = "in" if self.is_sale else "out"
        self.avail_advance = OrderMoneyService.get_available_advance_balance(
            kind=self.kind,
            partner_id=self.partner_id,
            direction=direction,
        )

        title_kind = "Sale Order" if self.is_sale else "Purchase Order"
        self.setWindowTitle(f"Finalize {title_kind} • {self.order.get('order_no', '')}")
        self.setMinimumWidth(560)
        self._build_ui()

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

        order_no = self.order.get("order_no", "")
        prod_name = self.order.get("product_name", "-")
        quality = self.order.get("quality") or "Standard"
        init_bags = self.order.get("initial_qty_bags", 0)
        init_kg = self.order.get("initial_weight_kg", 0)
        init_price = self.order.get("initial_price", 0)

        t_lbl = QLabel(f"<b>Finalizing {order_no}</b> • {self.partner_name}")
        t_lbl.setStyleSheet("font-size: 15px; color: #0f172a;")
        ib_lay.addWidget(t_lbl)

        qty_desc = f"{init_bags:,.0f} bags" if self.is_fert else f"{init_kg:,.2f} kg ({init_bags:,.0f} bags)"
        unit_str = "bag" if self.is_fert else "kg"
        sub_lbl = QLabel(
            f"Product: <b>{prod_name}</b> ({quality})  |  Initial: <b>{qty_desc}</b> @ ₦{init_price:,.2f}/{unit_str}"
        )
        sub_lbl.setStyleSheet("color: #475569; font-size: 13px;")
        ib_lay.addWidget(sub_lbl)
        main_lay.addWidget(info_box)

        # Form Inputs
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # Prepopulate values
        comp_price_init = self.order.get("company_price") or init_price
        comp_kg_init = self.order.get("company_weight_kg") or init_kg
        comp_bags_init = self.order.get("company_qty_bags") or init_bags

        # Company / Actual Price
        price_word = "Company Price" if self.is_sale else "Supplier Price"
        self.txt_price = QLineEdit(f"{comp_price_init:,.2f}" if comp_price_init else "")
        self.txt_price.textChanged.connect(self._recalc)
        form.addRow(f"<b>{price_word} (₦):</b>", self.txt_price)

        if not self.is_fert:
            self.txt_weight = QLineEdit(f"{comp_kg_init:,.2f}" if comp_kg_init else "")
            self.txt_weight.textChanged.connect(self._on_rcv_changed)
            form.addRow("<b>Company Weight (Kg):</b>", self.txt_weight)

        self.txt_bags = QLineEdit(f"{comp_bags_init:,.0f}" if comp_bags_init else "")
        if self.is_fert:
            self.txt_bags.textChanged.connect(self._on_rcv_changed)
            form.addRow("<b>Company Bags:</b>", self.txt_bags)
        else:
            form.addRow("Company Bags (Count):", self.txt_bags)

        # Accepted & Rejected
        self.txt_accepted = QLineEdit()
        self.txt_accepted.textChanged.connect(self._recalc)
        self.txt_rejected = QLineEdit("0.00")
        self.txt_rejected.textChanged.connect(self._on_rej_changed)

        acc_label = "Accepted Bags:" if self.is_fert else "Accepted Weight (Kg):"
        rej_label = "Rejected Bags:" if self.is_fert else "Rejected Weight (Kg):"
        form.addRow(acc_label, self.txt_accepted)
        form.addRow(rej_label, self.txt_rejected)

        main_lay.addLayout(form)

        # Live calculation box
        self.calc_box = QFrame()
        self.calc_box.setObjectName("calc_box")
        self.calc_box.setStyleSheet("""
            QFrame#calc_box {
                background: #f1f5f9;
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
        c_lay = QVBoxLayout(self.calc_box)
        c_lay.setSpacing(4)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("<b>Final Total Amount:</b>"))
        r1.addStretch()
        self.lbl_final_amount = QLabel("₦0.00")
        self.lbl_final_amount.setStyleSheet("font-size: 16px; font-weight: bold; color: #047857;")
        r1.addWidget(self.lbl_final_amount)
        c_lay.addLayout(r1)

        self.lbl_variance = QLabel("")
        self.lbl_variance.setStyleSheet("color: #64748b; font-size: 12px;")
        c_lay.addWidget(self.lbl_variance)
        main_lay.addWidget(self.calc_box)

        # Advance Detection Section
        self.adv_box = QFrame()
        self.adv_box.setObjectName("adv_box")
        adv_lay = QVBoxLayout(self.adv_box)
        adv_lay.setContentsMargins(10, 8, 10, 8)

        if self.avail_advance > 0.005:
            self.adv_box.setStyleSheet("""
                QFrame#adv_box {
                    background: #ecfdf5;
                    border: 1.5px solid #10b981;
                    border-radius: 6px;
                }
                QFrame#adv_box QLabel, QFrame#adv_box QCheckBox {
                    border: none;
                    background: transparent;
                }
            """)
            direction_name = "received from company" if self.is_sale else "paid to supplier"
            adv_hdr = QLabel(f"💰 <b>Available Advance Detected: ₦{self.avail_advance:,.2f}</b> ({direction_name})")
            adv_hdr.setStyleSheet("color: #065f46; font-size: 13px;")
            adv_lay.addWidget(adv_hdr)

            self.chk_use_adv = QCheckBox("Use Advance for settlement of this order")
            self.chk_use_adv.setChecked(True)
            self.chk_use_adv.setStyleSheet("font-weight: bold; color: #047857;")
            self.chk_use_adv.toggled.connect(self._recalc)
            adv_lay.addWidget(self.chk_use_adv)

            f_adv = QHBoxLayout()
            f_adv.addWidget(QLabel("Amount to Apply from Advance (₦):"))
            self.txt_adv_amount = QLineEdit()
            self.txt_adv_amount.setMaximumWidth(160)
            f_adv.addWidget(self.txt_adv_amount)
            f_adv.addStretch()
            adv_lay.addLayout(f_adv)
        else:
            self.adv_box.setStyleSheet("""
                QFrame#adv_box {
                    background: #f8fafc;
                    border: 1px dashed #cbd5e1;
                    border-radius: 6px;
                }
                QFrame#adv_box QLabel {
                    border: none;
                    background: transparent;
                }
            """)
            no_adv = QLabel("ℹ️ <i>No unapplied advance in profile. Payment can be settled anytime later.</i>")
            no_adv.setStyleSheet("color: #64748b; font-size: 12px;")
            adv_lay.addWidget(no_adv)
            self.chk_use_adv = None
            self.txt_adv_amount = None

        main_lay.addWidget(self.adv_box)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_confirm = QPushButton("Confirm && Finalize Order")
        self.btn_confirm.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px;"
        )
        self.btn_confirm.clicked.connect(self._confirm)

        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(self.btn_confirm)
        main_lay.addLayout(btn_box)

        # Initialize defaults
        self._on_rcv_changed()

    def _on_rcv_changed(self):
        rcv = _f(self.txt_bags.text()) if self.is_fert else _f(self.txt_weight.text())
        rej = _f(self.txt_rejected.text())
        acc = max(0.0, rcv - rej)
        self.txt_accepted.setText(f"{acc:,.2f}" if not self.is_fert else f"{acc:,.0f}")
        self._recalc()

    def _on_rej_changed(self):
        rcv = _f(self.txt_bags.text()) if self.is_fert else _f(self.txt_weight.text())
        rej = _f(self.txt_rejected.text())
        acc = max(0.0, rcv - rej)
        self.txt_accepted.setText(f"{acc:,.2f}" if not self.is_fert else f"{acc:,.0f}")
        self._recalc()

    def _recalc(self):
        price = _f(self.txt_price.text())
        acc = _f(self.txt_accepted.text())
        total = acc * price
        self.lbl_final_amount.setText(f"₦{total:,.2f}")

        # Variance
        init_price = self.order.get("initial_price", 0)
        init_qty = self.order.get("initial_qty_bags", 0) if self.is_fert else self.order.get("initial_weight_kg", 0)
        init_total = init_qty * init_price
        diff = total - init_total
        diff_str = f"+₦{diff:,.2f}" if diff >= 0 else f"-₦{abs(diff):,.2f}"
        self.lbl_variance.setText(f"Variance vs Initial: {diff_str}")

        # Update default advance amount
        if self.txt_adv_amount and self.chk_use_adv and self.chk_use_adv.isChecked():
            to_apply = min(self.avail_advance, total)
            self.txt_adv_amount.setText(f"{to_apply:,.2f}")

    def _confirm(self):
        price = _f(self.txt_price.text())
        if price <= 0:
            QMessageBox.warning(self, "Validation Error", "Please enter the confirmed price.")
            self.txt_price.setFocus()
            return

        rcv_qty = _f(self.txt_bags.text()) if self.is_fert else _f(self.txt_weight.text())
        if rcv_qty <= 0:
            msg = "Please enter the received bags." if self.is_fert else "Please enter the confirmed weight (kg)."
            QMessageBox.warning(self, "Validation Error", msg)
            return

        acc_qty = _f(self.txt_accepted.text())
        rej_qty = _f(self.txt_rejected.text())
        weight_kg = None if self.is_fert else rcv_qty
        bags_qty = rcv_qty if self.is_fert else _f(self.txt_bags.text())

        use_adv = bool(self.chk_use_adv and self.chk_use_adv.isChecked())
        adv_amt = _f(self.txt_adv_amount.text()) if (use_adv and self.txt_adv_amount) else None

        try:
            res = OrderProfileService.finalize_order(
                kind=self.kind,
                order_id=self.order["id"],
                company_price=price,
                company_weight_kg=weight_kg,
                company_qty_bags=bags_qty,
                accepted_qty=acc_qty,
                rejected_qty=rej_qty,
                use_advance=use_adv,
                advance_amount=adv_amt,
            )
            final_val = res.get("final_total_amount", 0.0)
            msg = f"Order {self.order.get('order_no')} has been finalized!\nFinal Total: ₦{final_val:,.2f}"
            if res.get("applied_payment_id"):
                msg += f"\nAdvance payment of ₦{adv_amt:,.2f} applied successfully."
            QMessageBox.information(self, "Order Finalized", msg)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Finalize Order", str(e))
