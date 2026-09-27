# apps/product_management_app/features/cashier/views/cashier_payments.py

from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QTabWidget,
    QDoubleSpinBox, QTextEdit, QGroupBox, QSplitter
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
from database.services.sale_adjustment_service import SaleAdjustmentService


class CashierPaymentsView(QWidget):
    """
    Cashier → Payments (Upgraded)

    This screen maps regular accounts and casual walk-ins under independent tabs.
    Double-clicking an item routes data directly to the CustomerProfileView financial layer.
    """

    def __init__(self):
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header
        header = QHBoxLayout()
        root.addLayout(header)

        title = QLabel("Payments Ledger Console")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)

        # Unified Search Tool (Filters both tabs concurrently)
        search_row = QHBoxLayout()
        root.addLayout(search_row)

        search_row.addWidget(QLabel("Search Entity:"))
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Type name or phone parameter to filter profiles…")
        search_row.addWidget(self.search_in, 1)

        self.btn_refresh = QPushButton("Refresh")
        search_row.addWidget(self.btn_refresh)

        # 🚀 NEW: Navigation Tab Container Layout
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # ---------------------------------------------------------
        # TAB 1: REGISTERED CUSTOMERS
        # ---------------------------------------------------------
        self.customers_tab = QWidget()
        cust_layout = QVBoxLayout(self.customers_tab)
        cust_layout.setContentsMargins(4, 8, 4, 4)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Customer Name", "Phone Contact"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        cust_layout.addWidget(self.table)
        
        self.tabs.addTab(self.customers_tab, "Standard Customers")

        # ---------------------------------------------------------
        # TAB 2: CASUAL WALK-INS / TRADERS
        # ---------------------------------------------------------
        self.walkins_tab = QWidget()
        walkin_layout = QVBoxLayout(self.walkins_tab)
        walkin_layout.setContentsMargins(4, 8, 4, 4)

        self.walkins_table = QTableWidget(0, 3)
        self.walkins_table.setHorizontalHeaderLabels(["ID", "Walk-in Trader Name", "Phone Contact"])
        self.walkins_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.walkins_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.walkins_table.horizontalHeader().setStretchLastSection(True)
        self.walkins_table.setAlternatingRowColors(True)
        walkin_layout.addWidget(self.walkins_table)

        self.tabs.addTab(self.walkins_tab, "Casual Walk-ins")

        # ---------------------------------------------------------
        # TAB 3: DISCOUNT CALCULATOR ("lazy calculator" - search a
        # customer, track their sales, apply a flat discount to a
        # specific partial/unpaid sale, print a fresh receipt)
        # ---------------------------------------------------------
        self.discount_tab = QWidget()
        disc_layout = QVBoxLayout(self.discount_tab)
        disc_layout.setContentsMargins(4, 8, 4, 4)
        disc_layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        disc_layout.addWidget(splitter, 1)

        # Left: customer search + list
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Customers"))
        self.disc_customer_search = QLineEdit()
        self.disc_customer_search.setPlaceholderText("Search by name or phone…")
        left_layout.addWidget(self.disc_customer_search)
        self.disc_customer_table = QTableWidget(0, 2)
        self.disc_customer_table.setHorizontalHeaderLabels(["ID", "Customer Name"])
        self.disc_customer_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.disc_customer_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.disc_customer_table.horizontalHeader().setStretchLastSection(True)
        self.disc_customer_table.setAlternatingRowColors(True)
        left_layout.addWidget(self.disc_customer_table, 1)
        splitter.addWidget(left_box)

        # Right: this customer's sales, and the discount panel
        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        self.disc_sales_label = QLabel("Select a customer to see their sales")
        self.disc_sales_label.setStyleSheet("font-weight: 700;")
        right_layout.addWidget(self.disc_sales_label)

        self.disc_sales_table = QTableWidget(0, 6)
        self.disc_sales_table.setHorizontalHeaderLabels(
            ["Sale No", "Date", "Total", "Paid", "Balance", "Status"])
        self.disc_sales_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.disc_sales_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.disc_sales_table.horizontalHeader().setStretchLastSection(True)
        self.disc_sales_table.setAlternatingRowColors(True)
        right_layout.addWidget(self.disc_sales_table, 1)

        apply_box = QGroupBox("Apply Discount to Selected Sale")
        apply_box.setStyleSheet("QGroupBox { font-weight: 700; }")
        apply_layout = QVBoxLayout(apply_box)

        self.disc_selected_label = QLabel("No sale selected")
        self.disc_selected_label.setStyleSheet("color: #555;")
        apply_layout.addWidget(self.disc_selected_label)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Discount amount (\u20a6):"))
        self.disc_amount_spin = QDoubleSpinBox()
        self.disc_amount_spin.setDecimals(2)
        self.disc_amount_spin.setMaximum(999999999.0)
        self.disc_amount_spin.setMinimumWidth(160)
        row1.addWidget(self.disc_amount_spin)
        row1.addStretch(1)
        apply_layout.addLayout(row1)

        apply_layout.addWidget(QLabel("Reason (optional):"))
        self.disc_reason = QLineEdit()
        self.disc_reason.setPlaceholderText("e.g. loyalty discount, goodwill adjustment…")
        apply_layout.addWidget(self.disc_reason)

        btn_row = QHBoxLayout()
        self.btn_apply_discount = QPushButton("Apply Discount")
        self.btn_apply_discount.setMinimumHeight(34)
        self.btn_apply_discount.setStyleSheet("font-weight: 700;")
        self.btn_apply_discount.setEnabled(False)
        self.btn_print_new_receipt = QPushButton("Print New Receipt")
        self.btn_print_new_receipt.setMinimumHeight(34)
        self.btn_print_new_receipt.setEnabled(False)
        btn_row.addWidget(self.btn_apply_discount)
        btn_row.addWidget(self.btn_print_new_receipt)
        apply_layout.addLayout(btn_row)

        right_layout.addWidget(apply_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.tabs.addTab(self.discount_tab, "Discount Calculator")

        self._disc_selected_sale_id = 0
        self._disc_last_receipt_sale_id = 0

        # ---------------------------------------------------------
        # WIRING SIGNALS
        # ---------------------------------------------------------
        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        
        # Grid routing connections
        self.table.cellDoubleClicked.connect(self.open_profile)
        self.walkins_table.cellDoubleClicked.connect(self.open_walkin_profile)

        # Discount Calculator tab wiring
        self.disc_customer_search.textChanged.connect(self._refresh_disc_customers)
        self.disc_customer_table.cellClicked.connect(self._disc_load_customer_sales)
        self.disc_sales_table.cellClicked.connect(self._disc_select_sale)
        self.btn_apply_discount.clicked.connect(self._disc_apply_discount)
        self.btn_print_new_receipt.clicked.connect(self._disc_print_receipt)

        self._refresh_disc_customers()

        self.refresh()

    def refresh(self):
        """
        🔄 Synchronized Cache Loader
        Pulls data out of state memory matrices and matches search parameters.
        """
        q = self.search_in.text().strip().lower()

        # 1. Hydrate Standard Regular Customer Grid Tab
        customers = getattr(state, "customers", [])
        if q:
            customers = [
                c for c in customers
                if q in c.full_name.lower() or q in c.phone.lower()
            ]

        self.table.setRowCount(0)
        for c in customers:
            row = self.table.rowCount()
            self.table.insertRow(row)

            id_item = QTableWidgetItem(str(c.id))
            id_item.setTextAlignment(Qt.AlignCenter)
            
            self.table.setItem(row, 0, id_item)
            self.table.setItem(row, 1, QTableWidgetItem(c.full_name))
            self.table.setItem(row, 2, QTableWidgetItem(c.phone or ""))

        # 2. Hydrate Casual Walk-in Profile Grid Tab Concurrently
        walkins = getattr(state, "walkins", [])
        if q:
            walkins = [
                w for w in walkins
                if q in w.full_name.lower() or q in w.phone.lower()
            ]

        self.walkins_table.setRowCount(0)
        for w in walkins:
            row = self.walkins_table.rowCount()
            self.walkins_table.insertRow(row)

            id_item = QTableWidgetItem(str(w.id))
            id_item.setTextAlignment(Qt.AlignCenter)

            self.walkins_table.setItem(row, 0, id_item)
            self.walkins_table.setItem(row, 1, QTableWidgetItem(w.full_name))
            self.walkins_table.setItem(row, 2, QTableWidgetItem(w.phone or ""))

    def open_profile(self, row, column):
        """
        Pop open financial ledger timelines for regular clients
        """
        try:
            cid = int(self.table.item(row, 0).text())
        except Exception:
            return

        customer = next((c for c in state.customers if c.id == cid), None)
        if not customer:
            QMessageBox.warning(self, "Error", "Customer profile record unavailable.")
            return

        # Retain inline window memory bounds securely
        self.profile_view = CustomerProfileView(customer, mode="cashier")
        self.profile_view.setWindowTitle(f"Cash Register Statement - {customer.full_name}")
        self.profile_view.show()

    def open_walkin_profile(self, row, column):
        """
        🚀 NEW: Pop open financial ledger timelines for casual walk-ins
        """
        try:
            wid = int(self.walkins_table.item(row, 0).text())
        except Exception:
            return

        walkin = next((w for w in state.walkins if w.id == wid), None)
        if not walkin:
            QMessageBox.warning(self, "Error", "Walk-in profile record unavailable.")
            return

        # Pass casual matrix straight down into the accounting engine
        self.profile_view = CustomerProfileView(walkin, mode="cashier")
        self.profile_view.setWindowTitle(f"Walk-in Cash Register Statement - {walkin.full_name}")
        self.profile_view.show()

    # -------------------------------------------------------------
    # Discount Calculator tab
    # -------------------------------------------------------------
    def _refresh_disc_customers(self):
        q = self.disc_customer_search.text().strip().lower()
        customers = getattr(state, "customers", [])
        if q:
            customers = [
                c for c in customers
                if q in c.full_name.lower() or q in (c.phone or "").lower()
            ]

        self.disc_customer_table.setRowCount(0)
        for c in customers:
            row = self.disc_customer_table.rowCount()
            self.disc_customer_table.insertRow(row)
            id_item = QTableWidgetItem(str(c.id))
            id_item.setTextAlignment(Qt.AlignCenter)
            self.disc_customer_table.setItem(row, 0, id_item)
            self.disc_customer_table.setItem(row, 1, QTableWidgetItem(c.full_name))

    def _disc_load_customer_sales(self, row, _col):
        try:
            party_id = int(self.disc_customer_table.item(row, 0).text())
            party_name = self.disc_customer_table.item(row, 1).text()
        except Exception:
            return

        self.disc_sales_label.setText(f"Sales for {party_name}")
        self._disc_party_id = party_id
        self._disc_selected_sale_id = 0
        self.btn_apply_discount.setEnabled(False)
        self.btn_print_new_receipt.setEnabled(False)
        self.disc_selected_label.setText("No sale selected")
        self.disc_amount_spin.setValue(0.0)
        self.disc_reason.clear()

        try:
            sales = SaleAdjustmentService.list_customer_sales(party_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load sales: {e}")
            return

        self._disc_sales_cache = {r["sale_id"]: r for r in sales}

        self.disc_sales_table.setRowCount(0)
        for r in sales:
            row_idx = self.disc_sales_table.rowCount()
            self.disc_sales_table.insertRow(row_idx)
            when = r["created_at"]
            when_s = when.strftime("%Y-%m-%d") if when else ""
            cells = [
                r["sale_no"], when_s, f"{r['total_amount']:,.2f}",
                f"{r['paid']:,.2f}", f"{r['balance']:,.2f}", r["status"].upper(),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.UserRole, r["sale_id"])
                if not r["selectable"]:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                    item.setForeground(Qt.gray)
                self.disc_sales_table.setItem(row_idx, col, item)

    def _disc_select_sale(self, row, _col):
        item = self.disc_sales_table.item(row, 0)
        if not item:
            return
        sale_id = item.data(Qt.UserRole)
        info = getattr(self, "_disc_sales_cache", {}).get(sale_id)
        if not info or not info["selectable"]:
            self._disc_selected_sale_id = 0
            self.btn_apply_discount.setEnabled(False)
            self.disc_selected_label.setText(
                "This sale is already fully paid - no discount can be applied to it."
                if info else "No sale selected"
            )
            return

        self._disc_selected_sale_id = sale_id
        self.disc_amount_spin.setMaximum(float(info["balance"]))
        self.disc_amount_spin.setValue(0.0)
        self.disc_selected_label.setText(
            f"{info['sale_no']} - current balance: \u20a6{info['balance']:,.2f} "
            f"(discount cannot exceed this)")
        self.btn_apply_discount.setEnabled(True)
        self.btn_print_new_receipt.setEnabled(False)

    def _disc_apply_discount(self):
        if not self._disc_selected_sale_id:
            QMessageBox.information(self, "Discount", "Select a sale first.")
            return

        amount = float(self.disc_amount_spin.value())
        if amount <= 0:
            QMessageBox.warning(self, "Discount", "Enter a discount amount greater than zero.")
            return

        confirm = QMessageBox.question(
            self, "Apply Discount",
            f"Apply a \u20a6{amount:,.2f} discount to this sale? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            SaleAdjustmentService.apply_discount(
                sale_id=self._disc_selected_sale_id,
                amount=amount,
                created_by_user_id=state.require_user_id(),
                reason=self.disc_reason.text().strip(),
            )
        except Exception as e:
            # Covers the exact race condition this was designed to catch -
            # e.g. the sale became fully paid in the gap between opening
            # this screen and clicking Apply.
            QMessageBox.critical(self, "Could Not Apply Discount", str(e))
            self._disc_load_customer_sales_current()
            return

        self._disc_last_receipt_sale_id = self._disc_selected_sale_id
        QMessageBox.information(self, "Discount Applied", "Discount applied successfully.")
        self.btn_print_new_receipt.setEnabled(True)
        self.btn_apply_discount.setEnabled(False)
        self._disc_load_customer_sales_current()

    def _disc_load_customer_sales_current(self):
        """Reloads the currently-viewed customer's sales, refreshing balances."""
        party_id = getattr(self, "_disc_party_id", 0)
        if not party_id:
            return
        for row in range(self.disc_customer_table.rowCount()):
            try:
                if int(self.disc_customer_table.item(row, 0).text()) == party_id:
                    self._disc_load_customer_sales(row, 0)
                    return
            except Exception:
                continue

    def _disc_print_receipt(self):
        if not self._disc_last_receipt_sale_id:
            QMessageBox.information(self, "Print", "No recently-discounted sale to print.")
            return
        try:
            from apps.product_management_app.features.receipts_v2 import (
                print_receipt, SALE, MODE_FULL
            )
            print_receipt(self, SALE, self._disc_last_receipt_sale_id, MODE_FULL)
        except Exception as e:
            QMessageBox.critical(self, "Print Failed", str(e))