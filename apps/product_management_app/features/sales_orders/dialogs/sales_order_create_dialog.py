# apps/product_management_app/features/sales_orders/dialogs/sales_order_create_dialog.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QDoubleSpinBox,
    QMessageBox, QHeaderView
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from database.session import get_session
from database.models.product import Product


def _is_fert(ptype: str) -> bool:
    return str(ptype or "").strip().lower() in ("fertilizer", "fertilizers")


class SalesOrderCreateDialog(QDialog):
    """
    Admin-only. Crops (by weight, kg) or fertilizer (by bags) - both
    selectable here, matching the same dual-unit convention used
    throughout this app. The quantity field itself switches between
    "Weight (kg)" and "Qty (Bags)" depending on which product is
    currently selected, so the operator is never asked to enter the
    wrong unit for a line. Saves as a draft: nothing about this touches
    stock or any party balance until it's explicitly dispatched from the
    main screen.
    """
    def __init__(self, parent=None, sales_order_id: int = None):
        super().__init__(parent)
        self.sales_order_id = sales_order_id
        self.setWindowTitle("Edit Draft Sales Order" if sales_order_id else "Create Sales Order")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(780, 560)

        layout = QVBoxLayout(self)

        # ---- buyer (Company - picked via the search-first/create dialog,
        # never a plain dropdown reusing Party) ----
        buyer_row = QHBoxLayout()
        buyer_row.addWidget(QLabel("Buyer (Company):"))
        self.buyer_display = QLineEdit()
        self.buyer_display.setReadOnly(True)
        self.buyer_display.setPlaceholderText("No company selected")
        buyer_row.addWidget(self.buyer_display, 1)
        self.btn_pick_buyer = QPushButton("Select…")
        buyer_row.addWidget(self.btn_pick_buyer)
        layout.addLayout(buyer_row)
        self._buyer_company_id = None

        # ---- expected delivery date ----
        from PySide2.QtWidgets import QDateEdit
        from PySide2.QtCore import QDate
        delivery_row = QHBoxLayout()
        delivery_row.addWidget(QLabel("Expected Delivery Date:"))
        self.delivery_date_in = QDateEdit()
        self.delivery_date_in.setCalendarPopup(True)
        self.delivery_date_in.setDate(QDate.currentDate())
        self.delivery_date_in.setDisplayFormat("yyyy-MM-dd")
        delivery_row.addWidget(self.delivery_date_in)
        delivery_row.addStretch(1)
        layout.addLayout(delivery_row)

        # ---- product line entry ----
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Category:"))
        self.category_cb = QComboBox()
        self.category_cb.addItem("All", "")
        self.category_cb.addItem("Crops", "crops")
        self.category_cb.addItem("Fertilizer", "fertilizer")
        self.category_cb.currentIndexChanged.connect(self._load_products)
        cat_row.addWidget(self.category_cb)
        cat_row.addStretch(1)
        layout.addLayout(cat_row)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Product:"))
        self.product_cb = QComboBox()
        add_row.addWidget(self.product_cb, 1)

        # Fertilizer is bags-only; crops needs BOTH weight and bags qty -
        # dual-tracked everywhere else in this app (see intake_crops_
        # dialog.py's own comment on this exact point) - this was simply
        # missing here before. Both fields stay in the layout; only the
        # weight one is hidden for fertilizer lines.
        self.weight_label = QLabel("Weight (kg):")
        add_row.addWidget(self.weight_label)
        self.weight_in = QDoubleSpinBox()
        self.weight_in.setDecimals(2)
        self.weight_in.setMaximum(1_000_000.0)
        add_row.addWidget(self.weight_in)

        self.bags_label = QLabel("Bags Qty:")
        add_row.addWidget(self.bags_label)
        self.bags_in = QDoubleSpinBox()
        self.bags_in.setDecimals(0)
        self.bags_in.setMaximum(1_000_000.0)
        add_row.addWidget(self.bags_in)

        add_row.addWidget(QLabel("My Price (\u20a6):"))
        self.price_in = QDoubleSpinBox()
        self.price_in.setDecimals(2)
        self.price_in.setMaximum(1_000_000.0)
        add_row.addWidget(self.price_in)

        self.btn_add_line = QPushButton("+ Add Line")
        add_row.addWidget(self.btn_add_line)
        layout.addLayout(add_row)

        # ---- lines table ----
        # Bags gets its own column - crop lines need it visible at a
        # glance alongside the kg figure, not just folded into the
        # Quantity text. Fertilizer rows show it blank since Quantity
        # there already IS the bag count (no redundant second track).
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Product", "Type", "Quantity", "Bags", "My Price", "Expected Amount"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btn_remove_row = QHBoxLayout()
        self.btn_remove_line = QPushButton("Remove Selected Line")
        btn_remove_row.addWidget(self.btn_remove_line)
        btn_remove_row.addStretch(1)
        layout.addLayout(btn_remove_row)

        # ---- notes ----
        layout.addWidget(QLabel("Notes (optional):"))
        self.notes_in = QLineEdit()
        layout.addWidget(self.notes_in)

        # ---- footer ----
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Save Changes" if sales_order_id else "Save as Draft")
        self.btn_save.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_cancel)
        footer.addWidget(self.btn_save)
        layout.addLayout(footer)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_pick_buyer.clicked.connect(self._pick_buyer)
        self.btn_add_line.clicked.connect(self._add_line)
        self.btn_remove_line.clicked.connect(self._remove_line)
        self.btn_save.clicked.connect(self._save)
        self.product_cb.currentIndexChanged.connect(self._sync_fields_for_selected_product)

        self._lines = []  # [{product_id, name, is_fert, weight_kg, qty_bags, price}]
        self._load_products()
        self._sync_fields_for_selected_product()

        if self.sales_order_id:
            self._load_existing_order()

    def _load_existing_order(self):
        from database.services.sales_order_service import SalesOrderService
        try:
            detail = SalesOrderService.get_order_detail(self.sales_order_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self.reject()
            return

        if detail["status"] != "draft":
            QMessageBox.warning(
                self, "Cannot Edit",
                f"This order is now '{detail['status']}' - only a draft "
                f"order can be edited this way."
            )
            self.reject()
            return

        from database.services.company_service import CompanyService
        self._buyer_company_id = detail["buyer_company_id"]
        company = CompanyService.get(detail["buyer_company_id"])
        self.buyer_display.setText(company["name"] if company else "Unknown company")

        self.notes_in.setText(detail["notes"] or "")

        if detail.get("expected_delivery_date"):
            from PySide2.QtCore import QDate
            d = detail["expected_delivery_date"]
            self.delivery_date_in.setDate(QDate(d.year, d.month, d.day))

        self._lines = [
            {
                "product_id": it["product_id"],
                "name": it["product_name"],
                "is_fert": _is_fert(it["product_type_snapshot"]),
                "weight_kg": it["dispatched_weight_kg"],
                "qty_bags": it["dispatched_qty_bags"],
                "price": it["my_price"],
            }
            for it in detail["items"]
        ]
        self._refresh_table()

    def _pick_buyer(self):
        from apps.product_management_app.features.sales_orders.dialogs.company_picker_dialog import (
            CompanyPickerDialog,
        )
        dlg = CompanyPickerDialog(parent=self)
        if dlg.exec_() == dlg.Accepted and dlg.selected_company_id:
            self._buyer_company_id = dlg.selected_company_id
            self.buyer_display.setText(dlg.selected_company_name or "")

    def _load_products(self, *_args):
        # Both crops and fertilizer are selectable here - a company can
        # buy either. Type is stored on the product itself and read fresh
        # each time a line gets added, never assumed.
        cat = self.category_cb.currentData()
        with get_session() as s:
            q = s.query(Product).filter(Product.is_active == True)  # noqa: E712
            if cat:
                q = q.filter(Product.product_type == cat)
            products = q.order_by(Product.name.asc()).all()
        self.product_cb.clear()
        for p in products:
            self.product_cb.addItem(p.name, {"id": p.id, "type": p.product_type})
        if not products:
            QMessageBox.warning(self, "No Products", "No active products found for this category.")
        self._sync_fields_for_selected_product()

    def _sync_fields_for_selected_product(self, *_args):
        data = self.product_cb.currentData()
        is_fert = bool(data and _is_fert(data.get("type")))
        # Fertilizer is bags-only - the weight field is meaningless for
        # it, so it's hidden entirely rather than shown-but-ignored.
        self.weight_label.setVisible(not is_fert)
        self.weight_in.setVisible(not is_fert)

    def _add_line(self):
        data = self.product_cb.currentData()
        if data is None:
            return
        weight = float(self.weight_in.value())
        bags = float(self.bags_in.value())
        price = float(self.price_in.value())
        is_fert = _is_fert(data.get("type"))

        if is_fert:
            if bags <= 0:
                QMessageBox.warning(self, "Add Line", "Enter a bag quantity greater than zero.")
                return
        else:
            # Crops needs BOTH - dual-tracked everywhere else in this
            # app, and this is genuinely how many bags physically get
            # loaded, not something derivable from weight alone.
            if weight <= 0:
                QMessageBox.warning(self, "Add Line", "Enter a weight greater than zero.")
                return
            if bags <= 0:
                QMessageBox.warning(self, "Add Line", "Enter a bags quantity greater than zero.")
                return
        if price <= 0:
            QMessageBox.warning(self, "Add Line", "Enter an expected price greater than zero.")
            return

        self._lines.append({
            "product_id": int(data["id"]),
            "name": self.product_cb.currentText(),
            "is_fert": is_fert,
            "weight_kg": 0.0 if is_fert else weight,
            "qty_bags": bags,
            "price": price,
        })
        self._refresh_table()
        self.weight_in.setValue(0.0)
        self.bags_in.setValue(0.0)
        self.price_in.setValue(0.0)

    def _remove_line(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self._lines):
            return
        del self._lines[r]
        self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(0)
        for ln in self._lines:
            r = self.table.rowCount()
            self.table.insertRow(r)
            if ln["is_fert"]:
                qty_display = f"{ln['qty_bags']:,.0f} bags"
                bags_display = "\u2014"  # already the primary unit - no redundant second track
                amount = ln["qty_bags"] * ln["price"]
            else:
                qty_display = f"{ln['weight_kg']:,.2f} kg"
                bags_display = f"{ln['qty_bags']:,.0f}"
                amount = ln["weight_kg"] * ln["price"]
            cells = [
                ln["name"], ("Fertilizer" if ln["is_fert"] else "Crops"),
                qty_display, bags_display, f"{ln['price']:,.2f}", f"{amount:,.2f}",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if c >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

    def _save(self):
        buyer_id = self._buyer_company_id
        if buyer_id is None:
            QMessageBox.warning(self, "Create Order", "Select a buyer first.")
            return
        if not self._lines:
            QMessageBox.warning(self, "Create Order", "Add at least one product line.")
            return

        expected_date = self.delivery_date_in.date().toPython()

        from database.services.sales_order_service import SalesOrderService
        try:
            items = [{
                "product_id": ln["product_id"],
                "dispatched_qty_bags": ln["qty_bags"],
                "dispatched_weight_kg": ln["weight_kg"],
                "my_price": ln["price"],
            } for ln in self._lines]

            if self.sales_order_id:
                SalesOrderService.update_draft(
                    sales_order_id=self.sales_order_id,
                    buyer_company_id=int(buyer_id),
                    items=items,
                    notes=self.notes_in.text().strip(),
                    expected_delivery_date=expected_date,
                )
                new_order_id = None
            else:
                new_order_id = SalesOrderService.create_draft(
                    buyer_company_id=int(buyer_id),
                    items=items,
                    created_by_user_id=state.require_user_id(),
                    notes=self.notes_in.text().strip(),
                    expected_delivery_date=expected_date,
                )
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", str(e))
            return

        QMessageBox.information(
            self, "Saved",
            "Sales Order updated." if self.sales_order_id else "Sales Order saved as draft."
        )

        # Only on genuine creation, not edits - an existing draft may
        # already have advances recorded against it via the payment
        # dialog once the order reaches that point.
        if new_order_id:
            self._maybe_prompt_advance_payment(new_order_id)

        self.accept()

    def _maybe_prompt_advance_payment(self, sales_order_id: int):
        answer = QMessageBox.question(
            self, "Advance Payment",
            "Did the company already pay anything toward this order?",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return

        from apps.product_management_app.features.purchase_orders.dialogs.order_advance_payment_dialog import (
            OrderAdvancePaymentDialog,
        )
        dlg = OrderAdvancePaymentDialog(
            order_type="sales_order", order_id=sales_order_id,
            order_label=self.buyer_display.text() or "this order", parent=self,
        )
        dlg.exec_()