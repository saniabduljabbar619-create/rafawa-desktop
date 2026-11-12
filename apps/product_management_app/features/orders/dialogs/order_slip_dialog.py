# apps/product_management_app/features/orders/dialogs/order_slip_dialog.py
from __future__ import annotations

from typing import Any, Dict

from PySide2.QtCore import Qt
from PySide2.QtGui import QFont
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from shared.uix.widgets.cards import money


class OrderSlipDialog(QDialog):
    """
    Thermal / printable slip preview for an Order activity.
    """

    def __init__(self, order_data: Dict[str, Any], partner_name: str, parent=None):
        super().__init__(parent)
        self.order = order_data
        self.partner_name = partner_name
        self.setWindowTitle(f"Order Slip • {self.order.get('order_no', '')}")
        self.setMinimumWidth(440)
        self.setMinimumHeight(520)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.txt_slip = QTextEdit()
        self.txt_slip.setReadOnly(True)
        font = QFont("Courier New", 10)
        font.setStyleHint(QFont.Monospace)
        self.txt_slip.setFont(font)
        self.txt_slip.setStyleSheet("background: #ffffff; color: #000000; border: 1px solid #cbd5e1;")

        slip_text = self._render_slip_text()
        self.txt_slip.setPlainText(slip_text)
        lay.addWidget(self.txt_slip, 1)

        # Buttons
        btn_box = QHBoxLayout()
        btn_copy = QPushButton("Copy Text")
        btn_copy.clicked.connect(self._copy_text)
        btn_print = QPushButton("Print Slip")
        btn_print.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 7px 18px; border-radius: 4px;"
        )
        btn_print.clicked.connect(self._print)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(btn_copy)
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        btn_box.addWidget(btn_print)
        lay.addLayout(btn_box)

    def _render_slip_text(self) -> str:
        o = self.order
        kind_label = "SALES ORDER (COMPANY)" if o.get("kind") == "sales" else "PURCHASE ORDER (SUPPLIER)"
        dt_str = o.get("created_at").strftime("%Y-%m-%d %H:%M") if hasattr(o.get("created_at"), "strftime") else str(o.get("created_at"))[:16]
        status = str(o.get("status", "")).upper()

        prod_name = o.get("product_name", "-")
        quality = o.get("quality") or "Standard"
        is_fert = str(o.get("product_type", "")).lower() in ("fertilizer", "fertilizers")

        init_bags = o.get("initial_qty_bags", 0)
        init_kg = o.get("initial_weight_kg", 0)
        init_price = o.get("initial_price", 0)

        comp_bags = o.get("company_qty_bags")
        comp_kg = o.get("company_weight_kg")
        comp_price = o.get("company_price")

        final_amt = o.get("final_total_amount")
        paid_amt = o.get("paid_amount", 0)
        balance = o.get("balance")

        w = 38
        sep = "=" * w
        dsep = "-" * w

        lines = [
            sep,
            "RAFAWA ENTERPRISES".center(w),
            "ORDER ACTIVITY SLIP".center(w),
            sep,
            f"Type:     {kind_label}",
            f"Order #:  {o.get('order_no', '')}",
            f"Date:     {dt_str}",
            f"Partner:  {self.partner_name}",
            f"Status:   {status}",
            dsep,
            f"Product:  {prod_name}",
            f"Quality:  {quality}",
            dsep,
            "INITIAL / DISPATCHED:".center(w),
        ]

        if is_fert:
            lines.append(f"  Bags:   {init_bags:,.0f} bags")
            lines.append(f"  Price:  ₦{init_price:,.2f}/bag")
            lines.append(f"  Est:    ₦{(init_bags * init_price):,.2f}")
        else:
            lines.append(f"  Weight: {init_kg:,.2f} kg ({init_bags:,.0f} bags)")
            lines.append(f"  Price:  ₦{init_price:,.2f}/kg")
            lines.append(f"  Est:    ₦{(init_kg * init_price):,.2f}")

        if status in ("CONFIRMED", "FINALIZED") or comp_price is not None:
            lines.append(dsep)
            lines.append("COMPANY / FINAL VERIFICATION:".center(w))
            if is_fert:
                lines.append(f"  Bags:   {comp_bags:,.0f} bags" if comp_bags is not None else "  Bags:   -")
                lines.append(f"  Price:  ₦{comp_price:,.2f}/bag" if comp_price is not None else "  Price:  -")
            else:
                lines.append(f"  Weight: {comp_kg:,.2f} kg" if comp_kg is not None else "  Weight: -")
                lines.append(f"  Price:  ₦{comp_price:,.2f}/kg" if comp_price is not None else "  Price:  -")

            lines.append(dsep)
            lines.append(f"TOTAL AMOUNT:  ₦{(final_amt or 0):,.2f}")
            lines.append(f"PAID / ADV:    ₦{(paid_amt or 0):,.2f}")
            bal_str = f"₦{(balance or 0):,.2f}" if balance is not None else "-"
            lines.append(f"BALANCE DUE:   {bal_str}")

        lines.append(sep)
        lines.append("Thank you for your business!".center(w))
        lines.append(sep)

        return "\n".join(lines)

    def _copy_text(self):
        from PySide2.QtWidgets import QApplication
        QApplication.clipboard().setText(self.txt_slip.toPlainText())
        QMessageBox.information(self, "Copied", "Order slip copied to clipboard.")

    def _print(self):
        try:
            from PySide2.QtPrintSupport import QPrinter, QPrintDialog
            printer = QPrinter()
            dlg = QPrintDialog(printer, self)
            if dlg.exec_() == QDialog.Accepted:
                self.txt_slip.print_(printer)
        except Exception as e:
            QMessageBox.warning(self, "Print Warning", f"Could not send to printer: {e}")
