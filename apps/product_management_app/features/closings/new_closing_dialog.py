# apps/product_management_app/features/closings/new_closing_dialog.py
# -*- coding: utf-8 -*-
"""
Dialog to calculate, preview, and record a new Customer Closing period.
"""
from __future__ import annotations

from typing import Optional
from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame, QMessageBox,
)

from database.services.closing_service import ClosingService
from apps.product_management_app.features.receipts_v2.closing_slip_dialog import open_closing_slip


def _money(val: float) -> str:
    return f"\u20a6{float(val or 0):,.2f}"


class NewClosingDialog(QDialog):
    def __init__(self, party_id: int, user_id: int, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.user_id = int(user_id)
        self.calc_data = {}
        self.recorded_result = None

        self.setWindowTitle("Record Customer Closing Period")
        self.setMinimumWidth(620)
        self.setMinimumHeight(480)

        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(12)

        # Header
        self.lbl_head = QLabel("<b>Calculating live unclosed activity...</b>")
        self.lbl_head.setStyleSheet("font-size: 15px; color: #0f172a;")
        main_lay.addWidget(self.lbl_head)

        # Metrics display box
        self.metrics_box = QFrame()
        self.metrics_box.setObjectName("metrics_box")
        self.metrics_box.setStyleSheet("""
            QFrame#metrics_box {
                background-color: #f8fafc;
                border: 1.5px solid #cbd5e1;
                border-radius: 8px;
            }
            QFrame#metrics_box QLabel {
                border: none;
                background: transparent;
                padding: 0px;
            }
        """)
        self.box_lay = QVBoxLayout(self.metrics_box)
        self.box_lay.setContentsMargins(14, 12, 14, 12)
        main_lay.addWidget(self.metrics_box)

        # Notes input
        lbl_notes = QLabel("<b>Closing Notes (optional):</b>")
        main_lay.addWidget(lbl_notes)
        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("e.g. Evening closing round-up, agreed reconciliation...")
        self.txt_notes.setMaximumHeight(65)
        main_lay.addWidget(self.txt_notes)

        # Buttons
        btn_lay = QHBoxLayout()
        self.btn_confirm = QPushButton("Confirm && Record Closing")
        self.btn_confirm.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 18px; border-radius: 4px;"
        )
        self.btn_confirm.clicked.connect(self._do_record)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_lay.addStretch()
        btn_lay.addWidget(btn_cancel)
        btn_lay.addWidget(self.btn_confirm)
        main_lay.addLayout(btn_lay)

        self._load_computation()

    def _load_computation(self):
        try:
            self.calc_data = ClosingService.compute_closing(self.party_id)
        except Exception as e:
            QMessageBox.critical(self, "Calculation Error", f"Failed to compute closing: {e}")
            self.reject()
            return

        name = self.calc_data.get("party_name", "Customer")
        p_start = self.calc_data.get("period_start")
        p_str = p_start.strftime("%Y-%m-%d %H:%M") if hasattr(p_start, "strftime") else str(p_start)[:16]
        self.lbl_head.setText(f"<b>Closing Calculation for {name}</b><br><span style='font-size: 12px; color: #475569;'>Period since: {p_str} &rarr; Now</span>")

        # Clear old widgets
        while self.box_lay.count():
            item = self.box_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        grid_lay = QVBoxLayout()
        grid_lay.setSpacing(8)

        def add_line(label: str, val_str: str, bold: bool = False, color: str = "#0f172a"):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            f_size = "14px" if bold else "13px"
            f_weight = "bold" if bold else "normal"
            l = QLabel(label)
            l.setStyleSheet(f"font-size: {f_size}; font-weight: {f_weight}; color: {color}; border: none; background: transparent;")
            r = QLabel(val_str)
            r.setStyleSheet(f"font-size: {f_size}; font-weight: {f_weight}; color: {color}; border: none; background: transparent;")
            r.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(l)
            row.addStretch()
            row.addWidget(r)
            grid_lay.addLayout(row)

        open_bal = self.calc_data.get("opening_balance", 0)
        purch = self.calc_data.get("total_purchases", 0)
        sales = self.calc_data.get("total_sales", 0)
        adv_out = self.calc_data.get("total_advance_out", 0)
        adv_in = self.calc_data.get("total_advance_in", 0)
        pay_in = self.calc_data.get("total_payments_in", 0)
        pay_out = self.calc_data.get("total_payments_out", 0)
        net_chg = self.calc_data.get("net_period_change", 0)
        close_bal = self.calc_data.get("closing_balance", 0)

        add_line("Opening Balance (Bude Balance):", _money(open_bal))
        add_line("Purchases Delivered (Sayayya):", _money(purch))
        add_line("Sales Bought (Sayarwa):", _money(sales))
        add_line("Advance OUT (Kudin Gaba da aka Ba shi):", _money(adv_out))
        add_line("Advance IN (Kudin Gaba da aka Karba):", _money(adv_in))
        add_line("Payments IN (Kudin da aka Karba):", _money(pay_in))
        add_line("Payments OUT (Kudin da aka Biya shi):", _money(pay_out))

        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setFrameShadow(QFrame.Plain)
        div.setStyleSheet("border: none; border-top: 1px solid #cbd5e1; margin: 4px 0px;")
        grid_lay.addWidget(div)

        add_line("Net Period Activity (Canji):", _money(net_chg), bold=True, color="#1e3a8a")

        bal_color = "#dc2626" if close_bal > 0.005 else ("#059669" if close_bal < -0.005 else "#0f172a")
        bal_text = _money(close_bal) + (" (Customer Owes)" if close_bal > 0.005 else (" (We Owe)" if close_bal < -0.005 else " (Settled)"))
        add_line("Final Closing Balance (Rufe Balance):", bal_text, bold=True, color=bal_color)

        self.box_lay.addLayout(grid_lay)

    def _do_record(self):
        notes = self.txt_notes.toPlainText().strip()
        try:
            self.recorded_result = ClosingService.record_closing(
                party_id=self.party_id,
                user_id=self.user_id,
                notes=notes,
            )
        except Exception as e:
            QMessageBox.critical(self, "Recording Error", f"Failed to record closing: {e}")
            return

        box = QMessageBox(self)
        box.setWindowTitle("Closing Recorded")
        box.setIcon(QMessageBox.Information)
        box.setText(
            f"Closing <b>{self.recorded_result.get('closing_no')}</b> recorded successfully!\n\n"
            f"Closing Balance: {_money(self.recorded_result.get('closing_balance', 0))}\n\n"
            "Would you like to print the 80mm thermal closing slip now?"
        )
        b_print = box.addButton("Print 80mm Slip", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec_()

        if box.clickedButton() is b_print:
            open_closing_slip(self, self.recorded_result)

        self.accept()
