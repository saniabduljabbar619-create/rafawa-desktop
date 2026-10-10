# apps/product_management_app/features/cashier/dialogs/quick_purchase_order_dialog.py
"""Create / edit a Quick Purchase order (draft), and pick a supplier to route lines to."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from database.services.quick_purchase_order_service import QuickPurchaseOrderService
from database.services.sales_service import SaleService
from database.services.supplier_profile_service import SupplierProfileService

NEW = "__new__"
NAIRA = "₦"


def fill_supplier_combo(combo: QComboBox, exclude: Optional[int] = None, select: Optional[int] = None,
                        none_text: Optional[str] = None, none_data=None) -> None:
    if none_text is None:
        none_text, none_data = getattr(combo, "_none", ("Pick a supplier...", None))
    combo._none = (none_text, none_data)
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(none_text, none_data)
    for sup in SupplierProfileService.active_suppliers():
        if exclude is not None and int(sup["id"]) == int(exclude):
            continue
        combo.addItem(sup["name"] + (f"  ({sup['phone']})" if sup.get("phone") else ""), int(sup["id"]))
    combo.addItem("+ New supplier...", NEW)
    if select is not None:
        i = combo.findData(int(select))
        if i >= 0:
            combo.setCurrentIndex(i)
    combo.blockSignals(False)


def on_supplier_pick(parent, combo: QComboBox, exclude: Optional[int] = None) -> None:
    """If '+ New supplier...' was picked, ask for the details and select the created supplier."""
    if combo.currentData() != NEW:
        return
    name, ok = QInputDialog.getText(parent, "New Supplier", "Supplier name:")
    name = (name or "").strip()
    if not ok or not name:
        combo.setCurrentIndex(0)
        return
    phone, _ = QInputDialog.getText(parent, "New Supplier", "Phone (optional, press OK to skip):")
    try:
        sid = SupplierProfileService.get_or_lazy_create_supplier(name, (phone or "").strip())
    except Exception as e:
        QMessageBox.critical(parent, "New Supplier", str(e))
        combo.setCurrentIndex(0)
        return
    fill_supplier_combo(combo, exclude, sid)


class RouteDialog(QDialog):
    """Ask for the other supplier that should supply the lines the first one does not have."""

    def __init__(self, parent, product_names: List[str], current_supplier: str, exclude: Optional[int]):
        super().__init__(parent)
        self.setWindowTitle("Route to another supplier")
        self.setMinimumWidth(460)
        self._exclude = exclude
        v = QVBoxLayout(self)
        head = QLabel(f"{current_supplier or 'The current supplier'} does not have:\n  " + "\n  ".join(product_names))
        head.setWordWrap(True)
        v.addWidget(head)
        form = QFormLayout()
        self.supplier = QComboBox()
        fill_supplier_combo(self.supplier, exclude)
        self.supplier.currentIndexChanged.connect(lambda _i: on_supplier_pick(self, self.supplier, self._exclude))
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0, 99_999_999)
        self.cost.setDecimals(2)
        self.cost.setPrefix(NAIRA)
        self.cost.setSpecialValueText("keep the same cost")
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional - why it is being moved")
        form.addRow("Ask this supplier:", self.supplier)
        form.addRow("Dealer cost each:", self.cost)
        form.addRow("Note:", self.note)
        v.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, self.btn_ok = QPushButton("Cancel"), QPushButton("Route")
        self.btn_ok.setObjectName("PrimaryButton")
        cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(self.btn_ok)
        v.addLayout(row)

    def _accept(self):
        if not isinstance(self.supplier.currentData(), int):
            QMessageBox.warning(self, "Route", "Pick the supplier to route to.")
            return
        self.accept()

    def values(self) -> Dict[str, Any]:
        return {"supplier_id": int(self.supplier.currentData()), "note": self.note.text().strip(),
                "unit_cost": float(self.cost.value()) or None}


class LineEditDialog(QDialog):
    """Fix one line: what the supplier really has, the price they quoted, or a different supplier."""

    def __init__(self, parent, line: Dict[str, Any]):
        super().__init__(parent)
        self.line = line
        self.setWindowTitle(f"Edit line - {line['product']}")
        self.setMinimumWidth(480)
        fert = line["unit"] == "bag"
        v = QVBoxLayout(self)
        asked = f"{line['qty']:g} {line['unit']}"
        head = QLabel(f"{line['product']} - asked: {asked} @ {line['unit_cost']:,.2f} from "
                      f"{line['supplier'] or 'nobody yet'}")
        head.setWordWrap(True)
        v.addWidget(head)
        form = QFormLayout()
        if fert:
            self.qty = QSpinBox()
            self.qty.setRange(1, 1_000_000)
            self.qty.setValue(int(line["qty"]))
        else:
            self.qty = QDoubleSpinBox()
            self.qty.setRange(0.01, 1_000_000_000)
            self.qty.setDecimals(2)
            self.qty.setValue(float(line["qty"]))
        self.qty.setSuffix(f" {line['unit']}")
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0.01, 99_999_999)
        self.cost.setDecimals(2)
        self.cost.setPrefix(NAIRA)
        self.cost.setValue(float(line["unit_cost"]) or 0.01)
        self.supplier = QComboBox()
        fill_supplier_combo(self.supplier, none_text="Not assigned yet", none_data=0,
                            select=line["supplier_id"])
        self.supplier.currentIndexChanged.connect(lambda _i: on_supplier_pick(self, self.supplier))
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional - e.g. 'no stock', 'price changed'")
        form.addRow("Quantity available:", self.qty)
        form.addRow("Dealer cost each:", self.cost)
        form.addRow("Supplier:", self.supplier)
        form.addRow("Note:", self.note)
        v.addLayout(form)
        hint = QLabel("Supplier only has part of it? Lower the quantity - the rest stays as its own line to give "
                      "to another supplier (or drop). Another supplier has it, or a different price? Change the "
                      "supplier and cost here.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        v.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, self.btn_ok = QPushButton("Cancel"), QPushButton("Save line")
        self.btn_ok.setObjectName("PrimaryButton")
        cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.btn_ok)
        v.addLayout(row)

    def values(self) -> Dict[str, Any]:
        sid = self.supplier.currentData()
        return {"qty": float(self.qty.value()), "unit_cost": float(self.cost.value()),
                "supplier_id": sid if isinstance(sid, int) else None, "note": self.note.text().strip()}


class QuickPurchaseOrderDialog(QDialog):
    def __init__(self, parent=None, order_id: Optional[int] = None):
        super().__init__(parent)
        self.order_id = order_id
        self.saved_id: Optional[int] = None
        self.lines: List[Dict[str, Any]] = []
        self.setWindowTitle("Edit Quick Purchase" if order_id else "New Quick Purchase")
        self.setMinimumSize(780, 560)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.customer = QLineEdit()
        self.customer.setPlaceholderText("Optional - who the goods are for")
        self.notes = QLineEdit()
        form.addRow("For customer:", self.customer)
        form.addRow("Note:", self.notes)
        root.addLayout(form)

        add = QHBoxLayout()
        self.product = QComboBox()
        self.product.setMinimumWidth(200)
        self.bags = QSpinBox()
        self.bags.setRange(0, 1_000_000)
        self.bags.setPrefix("bags ")
        self.kg = QDoubleSpinBox()
        self.kg.setRange(0, 1_000_000_000)
        self.kg.setDecimals(2)
        self.kg.setPrefix("kg ")
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0, 99_999_999)
        self.cost.setDecimals(2)
        self.cost.setPrefix(NAIRA)
        self.supplier = QComboBox()
        self.supplier.setMinimumWidth(180)
        self.btn_add = QPushButton("Add line")
        for w in (self.product, self.bags, self.kg, self.cost, self.supplier, self.btn_add):
            add.addWidget(w)
        root.addLayout(add)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Product", "Quantity", "Dealer cost each", "Supplier", "Amount"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)
        self.total = QLabel("")
        root.addWidget(self.total)
        hint = QLabel("A draft changes nothing. Sending it makes it pending; stock and the supplier's account "
                      "only change when the goods are received.")
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        bar = QHBoxLayout()
        self.btn_remove = QPushButton("Remove line")
        bar.addWidget(self.btn_remove)
        bar.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_draft = QPushButton("Save Draft")
        self.btn_send = QPushButton("Save && Send")
        self.btn_send.setObjectName("PrimaryButton")
        for b in (self.btn_cancel, self.btn_draft, self.btn_send):
            bar.addWidget(b)
        root.addLayout(bar)

        try:
            self._products = SaleService.list_products_for_sale(limit=500, include_zero_stock=True)
        except Exception as e:
            QMessageBox.warning(self, "Products", f"Could not load products:\n{e}")
            self._products = []
        for p in self._products:
            self.product.addItem(f"{p['name']}  ({p['stock']:g})", p)
        fill_supplier_combo(self.supplier)
        self.supplier.currentIndexChanged.connect(lambda _i: on_supplier_pick(self, self.supplier))
        self.product.currentIndexChanged.connect(self._product_changed)
        self.btn_add.clicked.connect(self._add_line)
        self.btn_remove.clicked.connect(self._remove_line)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_draft.clicked.connect(lambda: self._save(send=False))
        self.btn_send.clicked.connect(lambda: self._save(send=True))
        self._product_changed()

        if order_id:
            o = QuickPurchaseOrderService.get(order_id)
            self.customer.setText(o["customer_name"])
            self.notes.setText(o["notes"])
            for ln in o["lines"]:
                self.lines.append({k: ln[k] for k in ("product_id", "qty_bags", "total_weight_kg", "unit_cost",
                                                      "supplier_id", "product", "supplier", "unit", "qty")})
        self._refresh()

    def _fert(self) -> bool:
        p = self.product.currentData() or {}
        return str(p.get("product_type") or "").lower() in ("fertilizer", "fertilizers")

    def _product_changed(self, *_a):
        fert = self._fert()
        self.kg.setEnabled(not fert)
        self.kg.setValue(0)
        self.bags.setValue(0)

    def _add_line(self):
        p = self.product.currentData()
        if not p:
            return
        fert = self._fert()
        qty = self.bags.value() if fert else self.kg.value()
        if qty <= 0:
            QMessageBox.warning(self, "Quick Purchase", f"Enter how many {'bags' if fert else 'kg'} of {p['name']}.")
            return
        sid = self.supplier.currentData()
        self.lines.append({
            "product_id": int(p["id"]), "product": p["name"], "unit": "bag" if fert else "kg", "qty": qty,
            "qty_bags": self.bags.value() if fert else max(self.bags.value(), 1),
            "total_weight_kg": 0.0 if fert else self.kg.value(), "unit_cost": self.cost.value(),
            "supplier_id": sid if isinstance(sid, int) else None,
            "supplier": self.supplier.currentText() if isinstance(sid, int) else ""})
        self.bags.setValue(0)
        self.kg.setValue(0)
        self._refresh()

    def _remove_line(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.lines):
            self.lines.pop(r)
            self._refresh()

    def _refresh(self):
        self.table.setRowCount(len(self.lines))
        total = 0.0
        for i, ln in enumerate(self.lines):
            amount = float(ln["qty"]) * float(ln["unit_cost"] or 0)
            total += amount
            for c, text in enumerate((ln["product"], f"{ln['qty']:g} {ln['unit']}", f"{ln['unit_cost']:,.2f}",
                                      ln["supplier"] or "- not chosen -", f"{amount:,.2f}")):
                self.table.setItem(i, c, QTableWidgetItem(text))
        self.total.setText(f"{len(self.lines)} line(s)  -  total {NAIRA}{total:,.2f}")

    def _save(self, send: bool):
        try:
            payload = [{k: ln[k] for k in ("product_id", "qty_bags", "total_weight_kg", "unit_cost", "supplier_id")}
                       for ln in self.lines]
            if self.order_id:
                QuickPurchaseOrderService.update_draft(self.order_id, payload, self.customer.text(),
                                                       self.notes.text())
                self.saved_id = self.order_id
                if send:
                    QuickPurchaseOrderService.send(self.order_id)
            else:
                self.saved_id = QuickPurchaseOrderService.create(payload, self.customer.text(), self.notes.text(),
                                                                 send=send)
        except Exception as e:
            QMessageBox.warning(self, "Quick Purchase", str(e))
            if self.saved_id and not self.order_id:
                self.order_id = self.saved_id          # saved as a draft but could not be sent: keep editing it
            return
        self.accept()
