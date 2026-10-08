# apps/product_management_app/features/cashier/views/cashier_sales.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QMessageBox, QFrame,
    QSplitter, QHeaderView
)
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor

from apps.product_management_app.features.receipts.activity_receipt_printer import ActivityReceipt, ShopMeta
from apps.product_management_app.app_state import state, Product
from apps.product_management_app.features.cashier.dialogs.sale_checkout_dialog import SaleCheckoutDialog
from apps.product_management_app.features.cashier.dialogs.sale_item_dialog import SaleItemDialog
from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import SalePaymentDialog
from apps.product_management_app.features.receipts_v2 import (
    print_receipt, SALE, MODE_FULL, MODE_DELIVERY,
)
from database.services.sales_service import SaleService
from database.services.quick_purchase_service import QuickPurchaseService
from apps.product_management_app.features.cashier.dialogs.quick_purchase_dialog import (
    QuickPurchaseDialog, DECISION_QUICK, DECISION_BACKORDER, DECISION_AVAILABLE, DECISION_SAVE,
)
from database.models import Party
from database.session import get_session
from datetime import datetime
from shared.uix.widgets.cards import Segmented, chip, style_table


@dataclass
class CartItem:
    product_id: int
    product_type: str
    name: str
    unit_price: float
    unit_cost_at_sale: float
    qty_bags: int
    total_weight_kg: float

    @property
    def subtotal(self) -> float:
        ptype = (self.product_type or "").strip().lower()
        if ptype == "fertilizer":
            return float(self.qty_bags) * float(self.unit_price)
        return float(self.total_weight_kg) * float(self.unit_price)


class CashierSalesView(QWidget):

    def __init__(self):
        super().__init__()

        self.cart: List[CartItem] = []
        self.current_category: str = ""  # crops | fertilizer
        self._last_sale_id: int = 0      # for on-demand delivery note / receipt
        self._delivery_note_printed: bool = False  # gates the Print Receipt button

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ================= HEADER =================
        header = QHBoxLayout()
        root.addLayout(header)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Sales Console")
        title.setObjectName("Title")
        self.subtitle = QLabel("Pick a customer, add products, then finalize the sale")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        # Standalone Quick Purchase - for the case a product sits at ZERO
        # stock and is invisible in the normal Add Item picker. Disabled
        # until a customer is selected (the client's own requirement) -
        # there is no point buying stock for a sale that has no buyer yet.
        self.btn_quick_purchase = QPushButton("Quick Purchase")
        self.btn_quick_purchase.setEnabled(False)
        self.btn_quick_purchase.setMinimumHeight(38)
        header.addWidget(self.btn_quick_purchase)

        total_box = QVBoxLayout()
        total_box.setSpacing(0)
        cap = QLabel("CART TOTAL")
        cap.setStyleSheet("color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px;")
        cap.setAlignment(Qt.AlignRight)
        self.total_lbl = QLabel("₦0.00")
        self.total_lbl.setStyleSheet("color:#0f766e; font-size:22px; font-weight:700;")
        self.total_lbl.setAlignment(Qt.AlignRight)
        total_box.addWidget(cap)
        total_box.addWidget(self.total_lbl)
        header.addLayout(total_box)
        header.addSpacing(8)

        self.btn_checkout = QPushButton("Finalize Sale")
        self.btn_checkout.setObjectName("PrimaryButton")
        self.btn_checkout.setMinimumHeight(38)
        self.btn_checkout.setMinimumWidth(130)
        header.addWidget(self.btn_checkout)

        # ================= MAIN SPLIT =================
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # =====================================================
        # LEFT PANEL → CUSTOMER + PRODUCT SEARCH
        # =====================================================
        # =====================================================
        # LEFT PANEL → CUSTOMER ONLY
        # =====================================================
        left = QFrame()
        left.setObjectName("Card")
        l = QVBoxLayout(left)
        l.setContentsMargins(12, 12, 12, 12)
        l.setSpacing(10)

        # ---- Customer Section ----
        customer_label = QLabel("Customer")
        customer_label.setObjectName("SectionLabel")
        l.addWidget(customer_label)

        self.customer_cb = QComboBox()
        l.addWidget(self.customer_cb)

        self.walkin_name = QLineEdit()
        self.walkin_name.setPlaceholderText("New account name / Walk-in name")
        l.addWidget(self.walkin_name)

        # 🚀 NEW: Dynamic Point-of-Sale Account Registration Mode Dropdown Selector Toggle
        self.walkin_type_cb = QComboBox()
        self.walkin_type_cb.addItems([
            "Casual Walk-In (No Permanent Ledger Account)",
            "Permanent Regular Customer (Opens Lines of Credit/History)"
        ])
        for _cb in (self.customer_cb, self.walkin_type_cb):
            _cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
            _cb.setMinimumContentsLength(14)
        l.addWidget(self.walkin_type_cb)

        l.addStretch(1)
        splitter.addWidget(left)

        # =====================================================
        # MIDDLE PANEL → PRODUCTS - always visible, no more hidden
        # dropdown. This is the direct fix for "products list too small
        # to read clearly": every matching product is shown at once, in
        # its own row with its own columns, filtered by the SAME category
        # rule as before (locked once the cart has items).
        # =====================================================
        middle = QFrame()
        middle.setObjectName("Card")
        m = QVBoxLayout(middle)
        m.setContentsMargins(12, 12, 12, 12)
        m.setSpacing(10)

        products_label = QLabel("Products")
        products_label.setObjectName("SectionLabel")
        m.addWidget(products_label)

        # The combo stays the single source of truth (cart-lock logic, tests);
        # the segmented buttons are just its friendlier face.
        self.category_cb = QComboBox()
        self.category_cb.addItems(["Select Category", "Crops", "Fertilizer"])
        self.category_cb.setVisible(False)
        m.addWidget(self.category_cb)
        self.category_seg = Segmented([("Crops", "Crops"), ("Fertilizer", "Fertilizer")], default_first=False)
        m.addWidget(self.category_seg)

        self.prod_search = QLineEdit()
        self.prod_search.setPlaceholderText("Search product…")
        self.prod_search.setClearButtonEnabled(True)
        self.prod_search.setMinimumHeight(32)
        m.addWidget(self.prod_search)

        self.prod_table = QTableWidget(0, 4)
        self.prod_table.setHorizontalHeaderLabels(["Product", "SKU", "Stock", "Rate"])
        self.prod_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.prod_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.prod_table.horizontalHeader().setStretchLastSection(True)
        style_table(self.prod_table, 42)
        self.prod_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.prod_table.horizontalHeader().setMinimumSectionSize(60)
        self.prod_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        m.addWidget(self.prod_table, 1)
        self.prod_hint = QLabel("Choose Crops or Fertilizer to see what is in stock")
        self.prod_hint.setAlignment(Qt.AlignCenter)
        self.prod_hint.setStyleSheet("color:#9ca3af; font-size:12px; padding:14px;")
        m.addWidget(self.prod_hint)

        self.btn_add = QPushButton("Add Item")
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.setMinimumHeight(36)
        m.addWidget(self.btn_add)

        splitter.addWidget(middle)

        # =====================================================
        # RIGHT PANEL → CART
        # =====================================================
        right = QFrame()
        right.setObjectName("Card")
        r = QVBoxLayout(right)
        r.setContentsMargins(12, 12, 12, 12)
        r.setSpacing(8)

        cart_head = QHBoxLayout()
        cart_title = QLabel("Cart")
        cart_title.setObjectName("SectionLabel")
        cart_head.addWidget(cart_title)
        self.cart_count_chip = chip("0 items", "grey")
        cart_head.addWidget(self.cart_count_chip)
        cart_head.addStretch(1)
        r.addLayout(cart_head)

        self.cart_table = QTableWidget(0, 5)
        self.cart_table.setHorizontalHeaderLabels([
            "Product", "Rate", "Bags", "Kg", "Subtotal"
        ])
        self.cart_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cart_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.cart_table.horizontalHeader().setStretchLastSection(True)
        style_table(self.cart_table, 40)
        self.cart_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cart_table.horizontalHeader().setMinimumSectionSize(60)
        self.cart_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        r.addWidget(self.cart_table, 1)
        self.cart_hint = QLabel("The cart is empty.\nPick a product and press Add Item.")
        self.cart_hint.setAlignment(Qt.AlignCenter)
        self.cart_hint.setStyleSheet("color:#9ca3af; font-size:12px; padding:14px;")
        r.addWidget(self.cart_hint)
        self.cart_total_row = QLabel("")
        self.cart_total_row.setAlignment(Qt.AlignRight)
        self.cart_total_row.setStyleSheet("font-size:15px; font-weight:700; color:#0f766e; padding:6px 4px;")
        r.addWidget(self.cart_total_row)

        btn_row = QHBoxLayout()
        r.addLayout(btn_row)

        self.btn_remove = QPushButton("Remove Selected")
        self.btn_clear = QPushButton("Clear Cart")
        self.btn_delivery = QPushButton("Delivery Note")
        self.btn_delivery.setToolTip("Print a products-only delivery note for the last sale")
        self.btn_receipt = QPushButton("Print Receipt")
        self.btn_receipt.setToolTip(
            "Print the full receipt (amounts + payment) for the last sale.\n"
            "Print the Delivery Note first.")

        btn_row.addWidget(self.btn_remove)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_delivery)
        btn_row.addWidget(self.btn_receipt)
        btn_row.addStretch(1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)   # customer - narrow
        splitter.setStretchFactor(1, 4)   # products - the widest, most important panel now
        splitter.setStretchFactor(2, 4)   # cart

        self._splitter = splitter
        self._sized_once = False
        splitter.setChildrenCollapsible(False)

        # ================= WIRING =================
        self.category_cb.currentTextChanged.connect(self._on_category_changed)
        self.category_seg.changed.connect(self.category_cb.setCurrentText)
        self.prod_search.textChanged.connect(self._refresh_product_table)
        self.prod_table.cellDoubleClicked.connect(self._add_to_cart)
        self.btn_add.clicked.connect(self._add_to_cart)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear_cart)
        self.btn_delivery.clicked.connect(self._print_last_delivery)
        self.btn_receipt.clicked.connect(self._print_last_receipt)
        self.btn_checkout.clicked.connect(self._checkout)

        # Wire customer selection listener to manage modifier component properties dynamically
        self.customer_cb.currentIndexChanged.connect(self._sync_customer_ui)
        self.walkin_name.textChanged.connect(self._sync_customer_ui)
        self.btn_quick_purchase.clicked.connect(self._open_standalone_quick_purchase)

        # Initialize combobox dataset lists
        self._refresh_customer_combobox()
        self._refresh_product_table()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._sized_once:
            # the product list and cart need the room; the customer card is narrow
            self._sized_once = True
            w = max(self._splitter.width(), 900)
            self._splitter.setSizes([int(w * 0.25), int(w * 0.375), int(w * 0.375)])

    def _refresh_customer_combobox(self):
        """
        🔄 Populates/Refreshes the structural combo box selection with database regular accounts
        """
        self.customer_cb.blockSignals(True)
        self.customer_cb.clear()
        self.customer_cb.addItem("Walk-in customer / New Account", None)
        for c in getattr(state, "customers", []):
            self.customer_cb.addItem(f"{c.full_name} ({c.phone})", c.id)
        # Suppliers occasionally get sold to as well - offer them here too,
        # clearly labeled, so they're findable the FIRST time even though
        # they're not (yet) in state.customers. Once a sale actually happens
        # to them they earn is_customer=True and show up normally above.
        try:
            from database.services.supplier_profile_service import SupplierProfileService
            for sup in SupplierProfileService.supplier_only_for_picker():
                label = f"{sup['name']} ({sup['phone']}) - Supplier" if sup['phone'] \
                    else f"{sup['name']} - Supplier"
                self.customer_cb.addItem(label, sup["id"])
        except Exception:
            pass  # picker still works with just regular customers if this fails
        self.customer_cb.blockSignals(False)
        if self.customer_cb.lineEdit():
            self.customer_cb.lineEdit().setCursorPosition(0)

        # Make the picker type-to-search (matches on name OR phone).
        self._make_customer_cb_searchable()
        self._sync_customer_ui()

    def _make_customer_cb_searchable(self):
        from PySide2.QtWidgets import QCompleter
        self.customer_cb.setEditable(True)
        self.customer_cb.setInsertPolicy(QComboBox.NoInsert)
        self.customer_cb.lineEdit().setPlaceholderText("Search customer by name or phone…")
        comp = self.customer_cb.completer()
        if comp:
            comp.setCompletionMode(QCompleter.PopupCompletion)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            try:
                comp.setFilterMode(Qt.MatchContains)   # match anywhere, not just prefix
            except Exception:
                pass

    def _sync_customer_ui(self):
        """
        🛡️ Toggles the point-of-sale input controls context based on active dropdown parameters
        """
        is_walkin = (self.customer_cb.currentData() is None)
        self.walkin_name.setVisible(is_walkin)
        self.walkin_type_cb.setVisible(is_walkin)

        # Quick Purchase only makes sense once someone to sell to is known -
        # a real customer/supplier pick, OR a walk-in name has been typed.
        has_customer = (not is_walkin) or bool(self.walkin_name.text().strip())
        self.btn_quick_purchase.setEnabled(has_customer)
        if self.customer_cb.lineEdit():
            self.customer_cb.lineEdit().setCursorPosition(0)

    def _on_category_changed(self, text: str):
        cat = text.strip().lower()
        self.category_seg.clear() if cat not in ("crops", "fertilizer") else self.category_seg.set_key(text.strip())
        self.prod_hint.setVisible(cat not in ("crops", "fertilizer"))
        if cat not in ("crops", "fertilizer"):
            self.current_category = ""
            self.prod_table.setRowCount(0)
            return

        if self.cart:
            existing = self.cart[0].product_type
            if existing != cat:
                QMessageBox.warning(
                    self,
                    "Ledger Conflict",
                    "Cannot change category while cart contains items.\nClear cart first."
                )
                self.category_cb.blockSignals(True)
                self.category_cb.setCurrentText(existing.capitalize())
                self.category_cb.blockSignals(False)
                self.category_seg.set_key(existing.capitalize())
                return

        self.current_category = cat
        self._refresh_product_table()

    def _refresh_product_table(self):
        self.prod_table.setRowCount(0)
        if not self.current_category:
            return

        items = SaleService.list_products_for_sale(
            q=self.prod_search.text(),
            limit=500
        )

        rows = [p for p in items
               if str(p.get("product_type") or "").strip().lower() == self.current_category]

        self.prod_table.setRowCount(len(rows))
        for row, p in enumerate(rows):
            name_item = QTableWidgetItem(str(p["name"]))
            name_item.setData(Qt.UserRole, int(p["id"]))
            self.prod_table.setItem(row, 0, name_item)
            self.prod_table.setItem(row, 1, QTableWidgetItem(str(p["sku"])))
            stock_item = QTableWidgetItem(f"{p['stock']:,.2f}")
            stock_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if float(p["stock"] or 0) <= 5:
                stock_item.setForeground(QColor("#b91c1c"))
            self.prod_table.setItem(row, 2, stock_item)
            rate_item = QTableWidgetItem(f"₦{p['sale_rate']:,.2f}")
            rate_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rate_item.setForeground(QColor("#047857"))
            self.prod_table.setItem(row, 3, rate_item)

    def _add_to_cart(self):
        if not self.current_category:
            QMessageBox.information(self, "Sale", "Select category first.")
            return

        row = self.prod_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Sale", "Select a product.")
            return

        name_item = self.prod_table.item(row, 0)
        pid = name_item.data(Qt.UserRole) if name_item else None
        if pid is None:
            QMessageBox.information(self, "Sale", "Select a product.")
            return

        p = SaleService.get_product_for_sale(int(pid))
        if not p:
            QMessageBox.warning(self, "Sale", "Product unavailable.")
            return

        dlg = SaleItemDialog(
            self,
            product_id=int(p["id"]),
            product_name=str(p["name"]),
            product_type=self.current_category,
            unit_price=float(p.get("sale_rate") or 0.0),
        )

        if dlg.exec_() != dlg.Accepted:
            return

        payload = dlg.payload()
        variant = payload.get("variant", "Default")
        label = str(p["name"]) if variant.lower() == "default" else f"{p['name']} • {variant}"

        self.cart.append(CartItem(
            product_id=int(p["id"]),
            product_type=self.current_category,
            name=label,
            unit_price=float(payload["unit_price"]),
            unit_cost_at_sale=float(payload["unit_cost_at_sale"]),
            qty_bags=int(payload["qty_bags"]),
            total_weight_kg=float(payload["total_weight_kg"]),
        ))

        self._refresh_cart()

    def _open_standalone_quick_purchase(self):
        from apps.product_management_app.features.cashier.dialogs.standalone_quick_purchase_dialog import (
            StandaloneQuickPurchaseDialog,
        )
        dlg = StandaloneQuickPurchaseDialog(parent=self)
        if dlg.exec_() != dlg.Accepted:
            return

        # Planned, not yet committed - see the dialog's own _submit() for
        # the full reasoning (mirrors _maybe_quick_purchase()'s exact
        # pattern). Accumulated here since this button can be used more
        # than once before checkout; _checkout() commits every pending
        # entry, all at once, immediately before the sale itself.
        if not hasattr(self, "_pending_standalone_qp"):
            self._pending_standalone_qp: List[Dict[str, Any]] = []
        self._pending_standalone_qp.extend(dlg.pending_quick_purchases)

        # Added straight into the current cart at the product's normal
        # sale rate. The checkout screen's own editable Rate field is
        # still the place to correct this before the sale finalizes,
        # exactly like the in-cart shortfall flow already works.
        for line in dlg.new_cart_lines:
            p = SaleService.get_product_for_sale(int(line["product_id"]))
            sale_rate = float(p.get("sale_rate") or 0.0) if p else 0.0
            is_fert = line["product_type"] in ("fertilizer", "fertilizers")
            qty = float(line["qty"])
            self.cart.append(CartItem(
                product_id=int(line["product_id"]),
                product_type=line["product_type"],
                name=str(line["name"]),
                unit_price=sale_rate,
                unit_cost_at_sale=float(line["unit_cost"]),
                qty_bags=int(qty) if is_fert else 0,
                total_weight_kg=0.0 if is_fert else qty,
            ))

        self._refresh_cart()
        # NOT refreshing the product table here - stock genuinely hasn't
        # changed yet, only planned. It refreshes correctly once the sale
        # actually commits (or reverses cleanly if it doesn't).

    def _selected_cart_index(self) -> Optional[int]:
        r = self.cart_table.currentRow()
        if r < 0 or r >= len(self.cart):
            return None
        return r

    def _remove_selected(self):
        idx = self._selected_cart_index()
        if idx is None:
            QMessageBox.information(self, "Cart", "Select a cart row first.")
            return
        self.cart.pop(idx)
        self._refresh_cart()

    def _clear_cart(self):
        if not self.cart:
            return
        ok = QMessageBox.question(self, "Confirm", "Clear entire cart?", QMessageBox.Yes | QMessageBox.No)
        if ok == QMessageBox.Yes:
            self.cart.clear()
            # Any planned-but-not-yet-committed standalone Quick Purchase
            # only existed to support the cart items just cleared - never
            # let it survive to incorrectly commit against a later,
            # unrelated sale.
            self._pending_standalone_qp = []
            self._refresh_cart()

    def _refresh_cart(self):
        self.cart_table.setRowCount(0)
        for it in self.cart:
            row = self.cart_table.rowCount()
            self.cart_table.insertRow(row)

            vals = [
                it.name,
                f"{it.unit_price:,.2f}",
                str(int(it.qty_bags)),
                f"{it.total_weight_kg:,.2f}",
                f"{it.subtotal:,.2f}",
            ]

            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c in (1, 2, 3, 4):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cart_table.setItem(row, c, cell)

        total = sum(it.subtotal for it in self.cart)
        n = len(self.cart)
        self.total_lbl.setText(f"₦{total:,.2f}")
        self.cart_total_row.setText(f"Total  ₦{total:,.2f}" if n else "")
        self.cart_hint.setVisible(n == 0)
        self.cart_count_chip.setText(f"{n} item{'s' if n != 1 else ''}")

    def _maybe_quick_purchase(self):
        """
        Returns:
          None   -> everything in stock, proceed with the normal sale
          False  -> operator cancelled; abort checkout
          dict   -> {"sale_lines": [...], "decision": ..., "supplier_party_id"?, ...}

        For the Quick Purchase decision, this only PLANS the shortfall
        purchase - it does not commit it. The actual purchase (and the
        stock change it causes) only happens immediately before the sale
        itself is recorded, in _checkout() below - never here, and never
        held open across however long the checkout dialog takes to fill
        in. See _checkout()'s own comment at the commit point for why.
        """
        # 1. what does the cart need vs what is in stock?
        items = [{
            "product_id": x.product_id,
            "product_type": x.product_type,
            "name": x.name,   # so the interlude shows "NPK Golden", not "Product#2"
            "qty_bags": x.qty_bags,
            "total_weight_kg": x.total_weight_kg,
            "unit_price": x.unit_price,
        } for x in self.cart]

        # Anything already planned via the standalone "Quick Purchase"
        # button (but deliberately not yet committed - see that dialog's
        # own comments) counts as available here. Without this, this
        # interlude would see the exact same shortfall again and
        # redundantly re-prompt for products the operator already
        # handled just moments ago.
        pending_extra_by_pid: Dict[int, Dict[str, float]] = {}
        for plan in getattr(self, "_pending_standalone_qp", []) or []:
            for ln in plan.get("short_lines", []):
                pid = int(ln["product_id"])
                bucket = pending_extra_by_pid.setdefault(pid, {"qty_bags": 0, "total_weight_kg": 0.0})
                bucket["qty_bags"] += int(ln.get("qty_bags") or 0)
                bucket["total_weight_kg"] += float(ln.get("total_weight_kg") or 0)

        av = QuickPurchaseService.check_availability(items, pending_extra_by_pid)
        if av.get("all_available"):
            return None  # nothing to do - original flow untouched

        # 2. ask the operator: quick purchase / backorder / sell available
        dlg = QuickPurchaseDialog(av, parent=self)
        if dlg.exec_() != dlg.Accepted:
            return False
        choice = dlg.decision()
        decision = choice["decision"]

        if decision == DECISION_SAVE:
            # The supplier has not delivered: keep the shortfall as a PENDING Quick Purchase (no stock or
            # money change until the goods are received - see QuickPurchaseOrderService) and leave the
            # cart as it is so the sale can be finished once they arrive.
            from database.services.quick_purchase_order_service import QuickPurchaseOrderService
            dealer = choice["dealer_cost_by_pid"]
            lines = []
            for ln in choice["selected_short"]:
                fert = str(ln.get("product_type") or "").lower() in ("fertilizer", "fertilizers")
                lines.append({"product_id": int(ln["product_id"]),
                              "qty_bags": int(ln.get("short_qty_bags") or 0),
                              "total_weight_kg": 0.0 if fert else float(ln.get("short_weight_kg") or 0),
                              "unit_cost": float(dealer.get(int(ln["product_id"]), 0)),
                              "supplier_id": int(choice["supplier_party_id"])})
            cust = self.walkin_name.text().strip() or (self.customer_cb.currentText() if self.customer_cb.currentData()
                                                       else "")
            try:
                oid = QuickPurchaseOrderService.create(lines, customer_name=cust, send=True)
            except Exception as e:
                QMessageBox.warning(self, "Quick Purchase", f"Could not save it:\n{e}")
                return False
            QMessageBox.information(
                self, "Quick Purchase saved",
                f"Saved as pending (QP-{oid:05d}). Open Quick Purchases when the goods arrive - or route it to "
                "another supplier if they do not have them. The cart is kept so you can finish the sale.")
            return False

        if decision == DECISION_AVAILABLE:
            # sell only the in-stock lines; drop the short ones from the cart
            short_pids = {int(l["product_id"]) for l in av.get("short", [])}
            self.cart = [x for x in self.cart if int(x.product_id) not in short_pids]
            if not self.cart:
                QMessageBox.information(self, "Checkout",
                                        "Nothing left to sell after removing "
                                        "the out-of-stock items.")
                return False
            self._refresh_cart()
            # rebuild lines from the trimmed cart, at book cost
            av2 = QuickPurchaseService.check_availability([{
                "product_id": x.product_id, "product_type": x.product_type,
                "qty_bags": x.qty_bags, "total_weight_kg": x.total_weight_kg,
                "unit_price": x.unit_price,
            } for x in self.cart], pending_extra_by_pid)
            return {"decision": decision,
                    "sale_lines": QuickPurchaseService.build_sale_lines(av2, {})}

        if decision == DECISION_BACKORDER:
            # Sell what is in stock now; the missing goods become an owed
            # product-advance to the customer. Advance is recorded post-sale by
            # the caller (it needs the customer id + sale id). Here we only trim
            # the cart to the available portion and flag the shortfall.
            short_pids = {int(l["product_id"]) for l in av.get("short", [])}
            self.cart = [x for x in self.cart if int(x.product_id) not in short_pids]
            self._refresh_cart()
            av2 = QuickPurchaseService.check_availability([{
                "product_id": x.product_id, "product_type": x.product_type,
                "qty_bags": x.qty_bags, "total_weight_kg": x.total_weight_kg,
                "unit_price": x.unit_price,
            } for x in self.cart], pending_extra_by_pid) if self.cart else {"available": [], "short": []}
            return {"decision": decision,
                    "backorder_short": av.get("short", []),
                    "sale_lines": QuickPurchaseService.build_sale_lines(av2, {})
                                  if self.cart else []}

        # DECISION_QUICK: buy only the CHECKED products (see quick_purchase_dialog's
        # per-row checkboxes), then sell available + whatever was just bought.
        # Anything left unchecked is dropped from the cart entirely - same
        # treatment "Sell available" gives an out-of-stock line.
        supplier_id = int(choice["supplier_party_id"])
        dealer = choice["dealer_cost_by_pid"]
        selected = choice["selected_short"]
        unselected = choice["unselected_short"]

        if unselected:
            dropped_pids = {int(l["product_id"]) for l in unselected}
            self.cart = [x for x in self.cart if int(x.product_id) not in dropped_pids]
            self._refresh_cart()

        short_lines = []
        for ln in selected:
            pid = int(ln["product_id"])
            short_lines.append({**ln, "unit_cost": float(dealer.get(pid, 0))})

        # Deliberately NOT committed here - only planned. Stock must never
        # change for goods bought to fulfil a sale that hasn't happened
        # yet: committing now (as this used to) leaves the stock topped
        # up for however long the operator takes to fill in the checkout
        # dialog - seconds or minutes - even if they end up walking away
        # or noticing the real goods already arrived and don't need
        # this purchase at all. The actual purchase now only commits
        # immediately before the sale itself does, in _checkout() below -
        # back to back, not held open across the whole checkout dialog.
        av_for_sale = {"available": av.get("available", []), "short": selected}
        return {
            "decision": decision,
            "quick_purchase_pending": True,
            "supplier_party_id": supplier_id,
            "short_lines": short_lines,
            "sale_lines": QuickPurchaseService.build_sale_lines(av_for_sale, dealer),
        }

    def _checkout(self):
        if not self.cart:
            QMessageBox.information(self, "Checkout", "Cart is empty.")
            return

        types = set(str(x.product_type or "").strip().lower() for x in self.cart)
        types.discard("")
        if len(types) > 1:
            QMessageBox.warning(self, "Checkout", "Mixed carts not allowed.")
            return

        for x in self.cart:
            if int(x.qty_bags) <= 0:
                QMessageBox.warning(self, "Checkout", f"Missing Bags: {x.name}")
                return
            if x.product_type != "fertilizer" and float(x.total_weight_kg) <= 0:
                QMessageBox.warning(self, "Checkout", f"Missing Kg: {x.name}")
                return

        cid = self.customer_cb.currentData()
        walkin = self.walkin_name.text().strip()
        buyer_party_id = int(cid) if cid is not None else 0

        total_revenue = sum(x.subtotal for x in self.cart)

        # ---------------------------------------------------------------
        # QUICK PURCHASE INTERLUDE
        # Runs only if the cart is short on stock. If everything is available,
        # qp_sale_lines is None and the ORIGINAL flow below is untouched.
        # ---------------------------------------------------------------
        qp_plan = self._maybe_quick_purchase()
        if qp_plan is False:
            return  # operator cancelled the shortfall dialog
        qp_sale_lines = qp_plan.get("sale_lines") if qp_plan else None

        dlg = SaleCheckoutDialog(

            self,
            customer_id=buyer_party_id if buyer_party_id > 0 else None,
            customer_label=walkin if walkin else self.customer_cb.currentText(),
            walkin_name=walkin,
            total=float(total_revenue),
            total_cost=0.0,
            total_profit=0.0,
            lines=[{
                "product_id": x.product_id,
                "product_type": x.product_type,
                "name": x.name,
                "rate": x.unit_price,
                "bags": x.qty_bags,
                "kg": x.total_weight_kg,
                "subtotal": x.subtotal,
            } for x in self.cart],
        )
        if dlg.exec_() != dlg.Accepted:
            # Nothing to reverse here anymore - the Quick Purchase (if any)
            # was only ever planned, never committed, so walking away from
            # the sale at this point genuinely leaves stock untouched.
            # That's the actual fix: previously there was something to
            # reverse here specifically because the purchase had already
            # committed too early, before the operator had even seen this
            # dialog.
            return

        # ✅ CRITICAL ARCHITECTURAL EXTENSION: Handle Multi-Lane Point-Of-Sale Registrations
        # Deliberately done only AFTER the operator confirms checkout - creating
        # the party earlier left an orphan walk-in/permanent customer behind
        # every time the checkout (or the shortfall dialog) was cancelled.
        if buyer_party_id <= 0:
            target_name = walkin if walkin else "Walk-in Customer"

            # Read selection string index state from dropdown choice layout
            if self.walkin_type_cb.currentIndex() == 1:
                # 👑 STRATEGY PATH A: Instantiate a permanent registered customer configuration on the fly!
                new_cust = state.create_customer(full_name=target_name, phone="")
                buyer_party_id = int(new_cust.id) if new_cust else 0
            else:
                # 👑 STRATEGY PATH B: File under the casual temporary walkin lane database entity structure
                buyer_party_id = state.get_or_lazy_create_walkin(target_name, "")

            # Sync runtime cache pools concurrently to make new profiles immediately active
            state._load_customers_from_db()
            state._load_walkins_from_db()

            # Instantly re-hydrate self combobox component so the account option updates for future operations
            self._refresh_customer_combobox()

        checkout = dlg.payload()
        discount = float(checkout.get("discount") or 0.0)
        rate_overrides = checkout.get("rate_overrides") or {}
        discount_overrides = checkout.get("discount_overrides") or {}
        # The checkout dialog's grand subtotal reflects whatever rates the
        # operator finalised there - NOT the stale total computed before it
        # opened. That's the number that must drive the grand total.
        grand_subtotal = float(checkout.get("grand_subtotal") or total_revenue)
        grand_total = max(grand_subtotal - discount, 0.0)

        created_by = state.require_user_id()

        sale_items = qp_sale_lines if qp_sale_lines else [{
            "product_id": x.product_id,
            "product_type": x.product_type,
            "qty_bags": x.qty_bags,
            "total_weight_kg": x.total_weight_kg,
            "unit_price": x.unit_price,
        } for x in self.cart]

        # Apply the FINAL price by product_id - reaches BOTH cost lots of a
        # Quick-Purchased product (book-cost lot + dealer-cost lot), since
        # the customer pays one price per bag regardless of which lot it
        # physically came from.
        for item in sale_items:
            pid = int(item.get("product_id") or 0)
            if pid in rate_overrides:
                item["unit_price"] = float(rate_overrides[pid])

        # Apply the per-line discount the same way - but discount is a
        # LINE-TOTAL amount (not a per-unit rate), so if this product is
        # split across multiple rows (Quick Purchase's two cost lots), the
        # discount must be prorated by each row's share of the total
        # quantity, not applied in full to every matching row - otherwise
        # a split product's discount would silently double.
        if discount_overrides:
            qty_by_pid: Dict[int, float] = {}
            for item in sale_items:
                pid = int(item.get("product_id") or 0)
                is_fert = str(item.get("product_type") or "").lower() in ("fertilizer", "fertilizers")
                qty = float(item.get("qty_bags") or 0) if is_fert else float(item.get("total_weight_kg") or 0)
                qty_by_pid[pid] = qty_by_pid.get(pid, 0.0) + qty

            for item in sale_items:
                pid = int(item.get("product_id") or 0)
                if pid not in discount_overrides:
                    continue
                total_qty = qty_by_pid.get(pid, 0.0)
                if total_qty <= 0:
                    continue
                is_fert = str(item.get("product_type") or "").lower() in ("fertilizer", "fertilizers")
                own_qty = float(item.get("qty_bags") or 0) if is_fert else float(item.get("total_weight_kg") or 0)
                item["discount"] = round(discount_overrides[pid] * (own_qty / total_qty), 2)

        # This IS the fix: every planned Quick Purchase - whether from the
        # mid-checkout interlude above, or from one or more uses of the
        # standalone "Quick Purchase" button - commits here, immediately
        # before the sale, never back when its own dialog closed and
        # never held open across however long checkout takes. All of
        # them happen back to back, in the same breath as the sale.
        committed_qp_ids: List[int] = []
        standalone_plans = getattr(self, "_pending_standalone_qp", []) or []
        all_plans = list(standalone_plans)
        if qp_plan and qp_plan.get("quick_purchase_pending"):
            all_plans.append({
                "supplier_party_id": int(qp_plan["supplier_party_id"]),
                "short_lines": qp_plan["short_lines"],
            })

        for plan in all_plans:
            pur = QuickPurchaseService.buy_shortfall(
                supplier_party_id=int(plan["supplier_party_id"]),
                short_lines=plan["short_lines"],
                created_by_user_id=state.require_user_id(),
                supplier_note="Quick Purchase",
            )
            if not pur.get("ok"):
                # Reverse whatever already committed in THIS batch before
                # this failure - never leave a partial batch silently in
                # stock for a sale that hasn't happened yet.
                failed_reversals = []
                for already_id in committed_qp_ids:
                    try:
                        QuickPurchaseService.cancel_pending_quick_purchase(int(already_id))
                    except Exception as reversal_error:
                        failed_reversals.append((already_id, reversal_error))
                if failed_reversals:
                    details = "\n".join(f"#{cid}: {err}" for cid, err in failed_reversals)
                    QMessageBox.warning(
                        self, "Quick Purchase Not Reversed",
                        f"These earlier Quick Purchase(s) could not be "
                        f"automatically reversed:\n{details}\n\nPlease review "
                        f"them manually."
                    )
                QMessageBox.critical(self, "Quick Purchase",
                                     f"Could not buy the missing items:\n{pur.get('error')}")
                return
            committed_qp_ids.append(int(pur["purchase_id"]))

        # Consumed - cleared regardless of what happens next, so a later,
        # separate sale never accidentally re-commits an old plan.
        self._pending_standalone_qp = []

        try:
            res = SaleService.record({
                "buyer_party_id": int(buyer_party_id or 0),
                "walkin_name": walkin,
                "created_by_user_id": created_by,
                "items": sale_items,
                "discount": float(discount),
                "total": float(grand_total),
                "note": checkout.get("note") or "",
            })
        except Exception as e:
            # The sale itself failed right after the purchase(s) committed -
            # they only ever existed to support THIS sale, so reverse them
            # all immediately rather than leaving stock silently changed
            # for a sale that didn't happen after all.
            failed_reversals = []
            for committed_id in committed_qp_ids:
                try:
                    QuickPurchaseService.cancel_pending_quick_purchase(int(committed_id))
                except Exception as reversal_error:
                    failed_reversals.append((committed_id, reversal_error))
            if failed_reversals:
                details = "\n".join(f"#{cid}: {err}" for cid, err in failed_reversals)
                QMessageBox.warning(
                    self, "Quick Purchase Not Reversed",
                    f"The sale failed, but these Quick Purchase(s) could not "
                    f"be automatically reversed:\n{details}\n\nPlease review "
                    f"them manually."
                )
            QMessageBox.critical(self, "Sale Failed", str(e))
            return

        # The sale genuinely committed - now, and only now, offer to print
        # supplier documents for whatever was just, actually bought. Real
        # purchase ids only exist from this point on.
        if committed_qp_ids:
            resp = QMessageBox.question(
                self, "Print Receipts",
                "Print receipt(s) for the Quick Purchase(s) just made?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if resp == QMessageBox.Yes:
                from apps.product_management_app.features.sales_orders.dialogs.driver_picker_dialog import (
                    DriverPickerDialog,
                )
                driver_dlg = DriverPickerDialog(
                    parent=self,
                    skip_label="Print Without Driver",
                    hint_text="Optional - who's collecting these goods from the supplier(s)?",
                )
                driver_name = None
                if driver_dlg.exec_() == driver_dlg.Accepted:
                    driver_name = driver_dlg.selected_driver_name

                try:
                    from apps.product_management_app.features.receipts_v2.receipt_print import (
                        print_combined_delivery_note, print_quick_purchase_supplier_receipt,
                    )
                    if len(committed_qp_ids) > 1:
                        print_combined_delivery_note(self, committed_qp_ids)
                    for pid in committed_qp_ids:
                        print_quick_purchase_supplier_receipt(self, pid, driver_name)
                except Exception as e:
                    QMessageBox.warning(
                        self, "Printing Failed",
                        f"The purchase(s) were recorded correctly, but "
                        f"printing failed:\n{e}\n\nYou can reprint later "
                        f"from each supplier's own profile."
                    )

        ref = str(res.get("reference") or "")
        sale_id = int(res.get("sale_id") or 0)

        if sale_id > 0 and buyer_party_id > 0:
            with get_session() as s:
                party = s.query(Party).get(int(buyer_party_id))

            # Ask whether to apply the customer's advance (no auto deduction).
            from apps.product_management_app.features.profiling.dialogs.advance_choice_dialog import prompt_and_apply_advance
            prompt_and_apply_advance(
                self,
                party_id=int(buyer_party_id),
                entity_type="sale",
                entity_id=int(sale_id),
                balance=float(grand_total),
                user_id=created_by,
                party_name=(party.display_name if party else ""),
            )

            dlg = SalePaymentDialog(
                party=party,
                user_id=created_by,
                parent=self,
                print_slip=False
            )
            dlg.exec_()

        # --------------------------------------------------
        # No automatic printing here anymore (client's own request) - the
        # operator now prints the Delivery Note first, then the full Receipt,
        # each on demand via their own buttons below.
        # --------------------------------------------------
        self._last_sale_id = sale_id
        self._delivery_note_printed = False  # a fresh sale - neither has been printed yet

        QMessageBox.information(self, "Recorded", f"Sale recorded: {ref}")
        self.cart.clear()
        self._refresh_cart()
        self._refresh_product_table()

        # Reset the customer/walk-in selection for the NEXT sale - without
        # this, a walk-in's name could silently carry over into the next
        # customer's sale even after typing a brand new name, since the
        # combo box (checked first in this method) could still be pointing
        # at whoever was just served, and the text field itself was never
        # cleared either. Both reset here, plus the type toggle, so the
        # next sale genuinely starts from a blank slate.
        self.customer_cb.setCurrentIndex(0)
        self.walkin_name.clear()
        self.walkin_type_cb.setCurrentIndex(0)

    def _print_last_delivery(self):
        """Print a products-only delivery note for the most recent sale."""
        if self._last_sale_id <= 0:
            QMessageBox.information(
                self, "Delivery Note", "No recent sale to print a delivery note for."
            )
            return
        try:
            print_receipt(self, SALE, self._last_sale_id, MODE_DELIVERY)
            self._delivery_note_printed = True
        except Exception as e:
            QMessageBox.warning(self, "Print Error", f"Could not print delivery note:\n{e}")

    def _print_last_receipt(self):
        """Print the full receipt (amounts + payment) for the most recent
        sale - only after the Delivery Note has been printed for it first,
        matching the required order (goods leave with a delivery note before
        the payment receipt is handed over)."""
        if self._last_sale_id <= 0:
            QMessageBox.information(
                self, "Print Receipt", "No recent sale to print a receipt for."
            )
            return
        if not self._delivery_note_printed:
            QMessageBox.warning(
                self, "Print Receipt",
                "Print the Delivery Note first, then the Receipt."
            )
            return
        try:
            print_receipt(self, SALE, self._last_sale_id, MODE_FULL)
        except Exception as e:
            QMessageBox.warning(self, "Print Error", f"Could not print receipt:\n{e}")