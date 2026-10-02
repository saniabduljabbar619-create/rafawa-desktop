# apps/product_management_app/features/cashier/dialogs/swap_dialog.py
"""
Goods swap: a customer brings goods and takes goods in the same visit.

    He brings us (we BUY)  ─┐
                            ├─ set against each other ─▶  only the difference is left to pay either way
    We give him (we SELL)  ─┘
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from database.services.offset_service import SwapService
from shared.auth.permissions import PermissionDenied
from shared.uix.widgets.cards import money, style_table
from shared.uix.widgets.product_select import add_select_placeholder
from apps.product_management_app.features.cashier.dialogs.intake_cart_dialog import capture_intake_line


class GiveItemDialog(QDialog):
    """One line of goods we hand over: product (with stock shown), bags, kg and price."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Goods we give")
        self.setMinimumWidth(400)
        self._products = {p["id"]: p for p in SwapService.products_to_give()}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.product = QComboBox()
        for p in self._products.values():
            stock = f"{p['bags']:g} bags" + (f", {p['kg']:,.0f} kg" if p["type"] == "crops" else "")
            self.product.addItem(f"{p['name']}   ({stock} in stock)", p["id"])
        add_select_placeholder(self.product)
        self.bags = QSpinBox()
        self.bags.setRange(1, 100000)
        self.kg = QDoubleSpinBox()
        self.kg.setRange(0, 10_000_000)
        self.kg.setDecimals(2)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 1_000_000_000)
        self.price.setDecimals(2)
        self.price.setGroupSeparatorShown(True)
        self.lbl_price = QLabel("Price")
        self.lbl_total = QLabel("0.00")
        self.lbl_total.setStyleSheet("font-weight:700;")
        form.addRow("Product", self.product)
        form.addRow("Bags", self.bags)
        form.addRow("Weight (kg)", self.kg)
        form.addRow(self.lbl_price, self.price)
        form.addRow("Line total", self.lbl_total)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        ok = QPushButton("Add")
        ok.setObjectName("PrimaryButton")
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        self.product.currentIndexChanged.connect(self._on_product)
        for w in (self.bags, self.kg, self.price):
            w.valueChanged.connect(self._recalc)
        self._on_product()

    def _current(self) -> Optional[Dict[str, Any]]:
        return self._products.get(self.product.currentData())

    def _on_product(self, *_a) -> None:
        p = self._current()
        crops = bool(p and p["type"] == "crops")
        self.kg.setEnabled(crops)
        if not crops:
            self.kg.setValue(0)
        self.lbl_price.setText("Price per kg" if crops else "Price per bag")
        if p:
            self.price.setValue(p["price"])
        self._recalc()

    def _recalc(self, *_a) -> None:
        p = self._current()
        if not p:
            self.lbl_total.setText("0.00")
            return
        qty = self.kg.value() if p["type"] == "crops" else self.bags.value()
        self.lbl_total.setText(money(qty * self.price.value()))

    def _accept(self) -> None:
        p = self._current()
        if not p:
            QMessageBox.warning(self, "Item", "Select Product first.")
            return
        if self.price.value() <= 0:
            QMessageBox.warning(self, "Item", "Enter a price.")
            return
        if p["type"] == "crops" and self.kg.value() <= 0:
            QMessageBox.warning(self, "Item", "Enter the weight in kg.")
            return
        self.accept()

    def line(self) -> Dict[str, Any]:
        p = self._current()
        return {"product_id": p["id"], "name": p["name"], "type": p["type"], "qty_bags": self.bags.value(),
                "total_weight_kg": self.kg.value() if p["type"] == "crops" else 0.0,
                "unit_price": self.price.value()}


def _buy_total(it: Dict[str, Any]) -> float:
    return it["qty_bags"] * it["unit_cost"] if it["product_type"] == "fertilizer" else it["total_weight_kg"] * it["unit_cost"]


def _sell_total(it: Dict[str, Any]) -> float:
    return it["qty_bags"] * it["unit_price"] if it["type"] == "fertilizer" else it["total_weight_kg"] * it["unit_price"]


class SwapDialog(QDialog):
    def __init__(self, parent=None, party_id: int = 0, party_name: str = ""):
        super().__init__(parent)
        self.party_id, self.party_name = int(party_id), party_name
        self.result_data: Optional[Dict[str, Any]] = None
        self._recv: List[Dict[str, Any]] = []
        self._give: List[Dict[str, Any]] = []
        self.setWindowTitle("Swap / Exchange")
        self.resize(900, 560)

        root = QVBoxLayout(self)
        title = QLabel(f"Swap with {party_name or 'selected party'}")
        title.setObjectName("Title")
        root.addWidget(title)
        sub = QLabel("He brings goods and takes goods in one visit. Both sides are recorded, set against each "
                     "other, and only the difference is left to pay.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#6b7280; font-size:12px;")
        root.addWidget(sub)

        cols = QHBoxLayout()
        root.addLayout(cols, 1)
        self.tbl_recv, self.btn_add_recv, self.btn_rm_recv = self._side(
            cols, "He brings us  (we buy)", ["Product", "Bags", "Kg", "Price", "Total"])
        self.tbl_give, self.btn_add_give, self.btn_rm_give = self._side(
            cols, "We give him  (we sell)", ["Product", "Bags", "Kg", "Price", "Total"])

        self.lbl_recv = QLabel("")
        self.lbl_give = QLabel("")
        self.lbl_result = QLabel("")
        self.lbl_result.setStyleSheet("font-size:16px; font-weight:800;")
        for w in (self.lbl_recv, self.lbl_give):
            w.setStyleSheet("font-weight:600; color:#374151;")
        tot = QHBoxLayout()
        tot.addWidget(self.lbl_recv)
        tot.addWidget(self.lbl_give)
        tot.addStretch(1)
        tot.addWidget(self.lbl_result)
        root.addLayout(tot)

        self.note = QLineEdit()
        self.note.setPlaceholderText("Note (optional), e.g. 'maize for NPK'")
        root.addWidget(self.note)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Record Swap")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.setMinimumHeight(38)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_save)
        root.addLayout(foot)

        self.btn_add_recv.clicked.connect(self._add_recv)
        self.btn_add_give.clicked.connect(self._add_give)
        self.btn_rm_recv.clicked.connect(lambda: self._remove(self._recv, self.tbl_recv))
        self.btn_rm_give.clicked.connect(lambda: self._remove(self._give, self.tbl_give))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)
        self._render()

    def _side(self, parent_layout, title, headers):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        style_table(tbl, 34)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(tbl)
        row = QHBoxLayout()
        add = QPushButton("+ Add item")
        rm = QPushButton("Remove")
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)
        parent_layout.addWidget(box, 1)
        return tbl, add, rm

    # ------------------------------------------------------------------ editing
    def _add_recv(self) -> None:
        kind, ok = QInputDialog.getItem(self, "Goods he brings", "Type", ["Crops", "Fertilizer"], 0, False)
        if not ok:
            return
        line = capture_intake_line(self, kind.lower())
        if line is not None:
            self._recv.append(line)
            self._render()

    def _add_give(self) -> None:
        dlg = GiveItemDialog(self)
        if dlg.exec_() == dlg.Accepted:
            self._give.append(dlg.line())
            self._render()

    def _remove(self, lst, tbl) -> None:
        r = tbl.currentRow()
        if 0 <= r < len(lst):
            del lst[r]
            self._render()

    def _fill(self, tbl, rows) -> None:
        tbl.setRowCount(len(rows))
        for i, vals in enumerate(rows):
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl.setItem(i, c, it)

    def totals(self):
        return (round(sum(_buy_total(i) for i in self._recv), 2), round(sum(_sell_total(i) for i in self._give), 2))

    def _render(self) -> None:
        self._fill(self.tbl_recv, [[i["product_name"], str(i["qty_bags"]),
                                    f"{i['total_weight_kg']:,.2f}" if i["product_type"] == "crops" else "-",
                                    money(i["unit_cost"]), money(_buy_total(i))] for i in self._recv])
        self._fill(self.tbl_give, [[i["name"], str(i["qty_bags"]),
                                    f"{i['total_weight_kg']:,.2f}" if i["type"] == "crops" else "-",
                                    money(i["unit_price"]), money(_sell_total(i))] for i in self._give])
        recv, give = self.totals()
        self.lbl_recv.setText(f"He brings: {money(recv)}")
        self.lbl_give.setText(f"We give: {money(give)}")
        diff = round(recv - give, 2)
        if not self._recv or not self._give:
            self.lbl_result.setText("")
        elif diff > 0:
            self.lbl_result.setText(f"We owe him {money(diff)}")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#b91c1c;")
        elif diff < 0:
            self.lbl_result.setText(f"He owes us {money(-diff)}")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#047857;")
        else:
            self.lbl_result.setText("Even - nothing to pay")
            self.lbl_result.setStyleSheet("font-size:16px; font-weight:800; color:#374151;")

    def _save(self) -> None:
        if not self._recv or not self._give:
            QMessageBox.warning(self, "Swap", "Add the goods he brings AND the goods we give him.")
            return
        given = [{k: v for k, v in i.items() if k in ("product_id", "qty_bags", "total_weight_kg", "unit_price")}
                 for i in self._give]
        try:
            self.result_data = SwapService.record(self.party_id, list(self._recv), given, self.note.text())
        except PermissionDenied as e:
            QMessageBox.warning(self, "Not allowed", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Swap not recorded", str(e))
            return
        self.accept()
