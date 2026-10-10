# apps/product_management_app/features/cashier/dialogs/quick_purchase_confirm_dialog.py
"""The last checkout page of a Quick Purchase: selling price, did we pay the suppliers, did the customer pay."""
from __future__ import annotations

from typing import Any, Dict, List

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.quick_purchase_order_service import QuickPurchaseOrderService
from database.services.sales_service import SaleService

NAIRA = "\u20a6"
METHODS = ["cash", "transfer", "pos", "cheque"]
NOT_PAID, PAID_FULL, PART_PAID = "Not paid yet", "Paid in full", "Part payment"


def _money(spin: QDoubleSpinBox, maximum: float) -> None:
    spin.setRange(0, max(maximum, 0))
    spin.setDecimals(2)
    spin.setPrefix(NAIRA)
    spin.setGroupSeparatorShown(True)


class _PayRow:
    """Not paid yet / Paid in full / Part payment -> the amount paid now."""

    def __init__(self, total: float):
        self.total = round(total, 2)
        self.mode = QComboBox()
        self.mode.addItems([NOT_PAID, PAID_FULL, PART_PAID])
        self.amount = QDoubleSpinBox()
        _money(self.amount, self.total)
        self.method = QComboBox()
        self.method.addItems(METHODS)
        self.mode.currentTextChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_a):
        mode = self.mode.currentText()
        self.amount.setEnabled(mode == PART_PAID)
        if mode == NOT_PAID:
            self.amount.setValue(0)
        elif mode == PAID_FULL:
            self.amount.setValue(self.total)

    def paid(self) -> float:
        return float(self.amount.value())


class QuickPurchaseConfirmDialog(QDialog):
    def __init__(self, parent, order_id: int):
        super().__init__(parent)
        self.plan: Dict[str, Any] = QuickPurchaseOrderService.plan_confirm(order_id)
        self.setWindowTitle(f"Confirm {self.plan['order_no']}")
        self.setMinimumSize(900, 640)
        self._price_spins: Dict[int, QDoubleSpinBox] = {}
        self._supplier_rows: Dict[int, _PayRow] = {}
        self.customer_row = None

        root = QVBoxLayout(self)
        root.setSpacing(10)
        title = QLabel(f"Confirm {self.plan['order_no']}"
                       + (f" - {self.plan['customer_name']}" if self.plan["customer_name"] else ""))
        title.setStyleSheet("font-size:16px; font-weight:800;")
        root.addWidget(title)

        self.sale_total_lbl = QLabel("")
        if self.plan["has_sale"]:
            root.addWidget(self._section("1.  At what price do we sell to the customer?"))
            root.addWidget(self._build_prices())
        else:
            note = QLabel("This Quick Purchase has no sale attached - confirming records the goods as received "
                          "and the supplier payments.")
            note.setWordWrap(True)
            root.addWidget(note)

        root.addWidget(self._section(f"{'2' if self.plan['has_sale'] else '1'}.  Did we pay the suppliers?"))
        root.addWidget(self._build_suppliers())
        if self.plan["has_sale"]:
            root.addWidget(self._section("3.  Did the customer pay us?"))
            root.addWidget(self._build_customer())

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:8px;")
        root.addWidget(self.summary)
        root.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_back = QPushButton("Back")
        self.btn_ok = QPushButton("Confirm && Complete")
        self.btn_ok.setObjectName("PrimaryButton")
        self.btn_back.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._accept)
        row.addWidget(self.btn_back)
        row.addWidget(self.btn_ok)
        root.addLayout(row)
        self._refresh()

    # ------------------------------------------------------------------ sections
    @staticmethod
    def _section(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size:13px; font-weight:800; color:#0f766e; padding-top:4px;")
        return lbl

    def _table(self, headers: List[str], rows: int) -> QTableWidget:
        t = QTableWidget(rows, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionMode(QTableWidget.NoSelection)
        t.verticalHeader().setVisible(False)
        t.setFixedHeight(40 + 36 * max(rows, 1))
        return t

    def _build_prices(self) -> QTableWidget:
        lines = self.plan["sale_lines"]
        t = self._table(["Product", "Customer asked", "We can sell", "Short", "Our cost", "Sell price each",
                         "Amount"], len(lines))
        self.price_table = t
        for i, sl in enumerate(lines):
            unit = "bag" if str(sl["product_type"]).lower().startswith("fert") else "kg"
            blended = (sl["qp_part"] * sl["dealer_cost"] + sl["stock_part"] * sl["book_cost"]) / sl["sell_qty"] \
                if sl["sell_qty"] else 0.0
            for c, text in enumerate((sl["name"], f"{sl['wanted']:g} {unit}", f"{sl['sell_qty']:g} {unit}",
                                      f"{sl['short']:g} {unit}" if sl["short"] else "-", f"{blended:,.2f}")):
                it = QTableWidgetItem(text)
                if c == 3 and sl["short"]:
                    it.setForeground(Qt.red)
                t.setItem(i, c, it)
            spin = QDoubleSpinBox()
            _money(spin, 99_999_999)
            price = float(sl["price"])
            if price <= 0:
                try:
                    price = float((SaleService.get_product_for_sale(int(sl["product_id"])) or {}).get("sale_rate") or 0)
                except Exception:
                    price = 0.0
            spin.setValue(price)
            spin.valueChanged.connect(self._refresh)
            self._price_spins[int(sl["product_id"])] = spin
            t.setCellWidget(i, 5, spin)
            t.setItem(i, 6, QTableWidgetItem(""))
        return t

    def _build_suppliers(self) -> QTableWidget:
        sups = self.plan["suppliers"]
        t = self._table(["Supplier", "Goods total", "Did we pay?", "Paid now", "Method"], len(sups))
        for i, sp in enumerate(sups):
            row = _PayRow(sp["amount"])
            self._supplier_rows[int(sp["supplier_id"])] = row
            t.setItem(i, 0, QTableWidgetItem(sp["name"]))
            t.setItem(i, 1, QTableWidgetItem(f"{sp['amount']:,.2f}"))
            t.setCellWidget(i, 2, row.mode)
            t.setCellWidget(i, 3, row.amount)
            t.setCellWidget(i, 4, row.method)
            row.mode.currentTextChanged.connect(self._refresh)
            row.amount.valueChanged.connect(self._refresh)
        if not sups:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("Nothing left to buy - the goods are already received."))
            t.setFixedHeight(80)
        return t

    def _build_customer(self) -> QFrame:
        box = QFrame()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        self.customer_total_lbl = QLabel("")
        self.customer_row = _PayRow(0.0)
        for w in (self.customer_total_lbl, self.customer_row.mode, self.customer_row.amount, self.customer_row.method):
            h.addWidget(w)
        h.addStretch(1)
        self.customer_row.mode.currentTextChanged.connect(self._refresh)
        self.customer_row.amount.valueChanged.connect(self._refresh)
        return box

    # ------------------------------------------------------------------ numbers
    def _sale_total(self) -> float:
        return round(sum(float(sl["sell_qty"]) * self._price_spins[int(sl["product_id"])].value()
                         for sl in self.plan["sale_lines"]), 2)

    def _refresh(self, *_a):
        total = self._sale_total() if self.plan["has_sale"] else 0.0
        cost = sum(sl["qp_part"] * sl["dealer_cost"] + sl["stock_part"] * sl["book_cost"]
                   for sl in self.plan["sale_lines"])
        if self.plan["has_sale"]:
            for i, sl in enumerate(self.plan["sale_lines"]):
                amt = float(sl["sell_qty"]) * self._price_spins[int(sl["product_id"])].value()
                self.price_table.item(i, 6).setText(f"{amt:,.2f}")
            cr = self.customer_row
            cr.total = total
            _money(cr.amount, total)
            cr._sync()
            self.customer_total_lbl.setText(f"Sale total {NAIRA}{total:,.2f}   ")
        owed_sup = self.plan["open_total"] - sum(r.paid() for r in self._supplier_rows.values())
        lines = []
        if self.plan["has_sale"]:
            lines.append(f"Sale {NAIRA}{total:,.2f}  -  estimated profit {NAIRA}{total - cost:,.2f}  -  "
                         f"customer still owes {NAIRA}{total - self.customer_row.paid():,.2f}")
        lines.append(f"We still owe the suppliers {NAIRA}{owed_sup:,.2f}.  Anything unpaid stays in the Payments center.")
        self.summary.setText("\n".join(lines))

    def _accept(self):
        if self.plan["has_sale"]:
            for sl in self.plan["sale_lines"]:
                if sl["sell_qty"] > 0 and self._price_spins[int(sl["product_id"])].value() <= 0:
                    QMessageBox.warning(self, "Confirm", f"Set a selling price for {sl['name']}.")
                    return
        self.accept()

    def payload(self) -> Dict[str, Any]:
        cr = self.customer_row
        return {
            "prices": {pid: float(s.value()) for pid, s in self._price_spins.items()},
            "supplier_paid": {sid: r.paid() for sid, r in self._supplier_rows.items()},
            "supplier_method": {sid: r.method.currentText() for sid, r in self._supplier_rows.items()},
            "customer_paid": cr.paid() if cr else 0.0,
            "customer_method": cr.method.currentText() if cr else "cash",
        }
