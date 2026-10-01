from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QMenu, QHeaderView,
    QAbstractItemView,
)
from PySide2.QtCore import Qt

from apps.product_management_app.app_state import state
from database.services.customer_directory_service import CustomerDirectoryService
from database.services.supplier_profile_service import SupplierProfileService
from shared.uix.widgets.cards import KpiCard, Segmented, avatar, chip, money
from shared.uix.widgets.dialogs import CustomerFormDialog
from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
from shared.uix.layout.scrollable import make_scrollable_page

COLS = ["ID", "Customer", "Phone", "Last Sale", "Owes Us", "Deposit Held", "Status"]
FILTERS = [("all", "All"), ("owing", "Owing"), ("deposit", "Has Deposit"), ("inactive", "Inactive")]

_TABLE_QSS = """
QTableWidget#CustomerTable { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px;
    gridline-color:transparent; outline:0; alternate-background-color:#ffffff; }
QTableWidget#CustomerTable::item { border-bottom:1px solid #f1f5f9; padding:4px 8px; color:#111827; }
QTableWidget#CustomerTable::item:selected { background:#ecfdf5; color:#065f46; }
QTableWidget#CustomerTable::item:hover { background:#f8fafc; }
QTableWidget#CustomerTable QHeaderView::section { background:#f8fafc; color:#6b7280; border:none;
    border-bottom:1px solid #e5e7eb; padding:10px 8px; font-size:11px; font-weight:700; }
"""


def _last_sale_text(days):
    if days is None:
        return "No sales yet"
    return "Today" if days == 0 else "Yesterday" if days == 1 else f"{days} days ago"


class CustomersListView(QWidget):
    """
    Registered customers only - Walk-ins live on their own screen (walkins_list.py).

    Directory layout: headline KPI cards, a search/filter bar, then one row per
    customer with an avatar, last-sale recency and colour-coded balance chips
    (what they owe us / deposits we hold for them). Double-click opens the profile.
    """
    def __init__(self, mode="admin"):
        super().__init__()
        self.mode = str(mode or "admin").strip().lower()

        layout = make_scrollable_page(self, margins=(16, 14, 16, 14))
        layout.setSpacing(12)

        # Header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Customers")
        title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        self.btn_add = QPushButton("+  Add Customer")
        self.btn_add.setProperty("role", "primary")
        self.btn_add.setMinimumHeight(34)
        head.addWidget(self.btn_add)
        layout.addLayout(head)

        # KPI cards
        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self.kpi_total = KpiCard("Customers", "0", "registered accounts", "blue")
        self.kpi_owing = KpiCard("Owed to us", "0.00", "across 0 customers", "red")
        self.kpi_deposit = KpiCard("Deposits held", "0.00", "customer advances", "green")
        self.kpi_active = KpiCard("Active", "0", "traded in the last 30 days", "amber")
        for k in (self.kpi_total, self.kpi_owing, self.kpi_deposit, self.kpi_active):
            kpis.addWidget(k, 1)
        layout.addLayout(kpis)

        # Search / filter bar
        top = QHBoxLayout()
        top.setSpacing(10)
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Search name, phone or email")
        self.search_in.setClearButtonEnabled(True)
        self.search_in.setMinimumHeight(34)
        top.addWidget(self.search_in, 1)
        self.filter_seg = Segmented(FILTERS)
        top.addWidget(self.filter_seg)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setMinimumHeight(34)
        top.addWidget(self.btn_refresh)
        layout.addLayout(top)

        # Table
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("CustomerTable")
        self.table.setStyleSheet(_TABLE_QSS)
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 56)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.table, 1)

        self.empty_lbl = QLabel("No customers match your search.")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet("color:#9ca3af; font-size:13px; padding:24px;")
        self.empty_lbl.setVisible(False)
        layout.addWidget(self.empty_lbl)

        # Actions
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.btn_open = QPushButton("Open Profile")
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_edit)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)
        hint = QLabel("Double-click a customer to open their profile  -  right-click for more")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        actions.addWidget(hint)

        # Cashiers may create customers but not edit or delete them.
        if self.mode == "cashier":
            self.btn_edit.setVisible(False)
            self.btn_delete.setVisible(False)

        # Wiring
        self.btn_refresh.clicked.connect(self.refresh)
        self.search_in.textChanged.connect(self.refresh)
        self.filter_seg.changed.connect(lambda _k: self.refresh())

        self.btn_add.clicked.connect(self.add_customer)
        self.btn_open.clicked.connect(self.open_profile)
        self.btn_edit.clicked.connect(self.edit_customer)
        self.btn_delete.clicked.connect(self.delete_customer)

        self.table.cellDoubleClicked.connect(lambda _r, _c: self.open_profile())
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        self._open_profiles = []
        self.refresh()

    def selected_customer_id(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        try:
            return int(self.table.item(r, 0).text())
        except Exception:
            return None

    # ----- rendering -------------------------------------------------------------
    def _name_cell(self, c):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)
        lay.addWidget(avatar(c.full_name, 34))
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(c.full_name)
        name.setStyleSheet("font-weight:600; font-size:13px; background:transparent;")
        sub = QLabel(c.email or c.address or "")
        sub.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        col.addWidget(name)
        if sub.text():
            col.addWidget(sub)
        lay.addLayout(col, 1)
        if c.is_supplier:
            lay.addWidget(chip("Supplier", "blue"))
        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        return w

    @staticmethod
    def _chip_cell(text, tone):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.addWidget(chip(text, tone), 0, Qt.AlignVCenter)
        lay.addStretch(1)
        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        return w

    def refresh(self):
        q = self.search_in.text().strip().lower()
        mode = self.filter_seg.key()
        all_c = list(state.customers)
        try:
            info = CustomerDirectoryService.summaries([c.id for c in all_c])
        except Exception:
            info = {}
        empty = CustomerDirectoryService.empty()

        def summary(c):
            return info.get(int(c.id), empty)

        def match(c):
            if q and q not in f"{c.full_name} {c.phone} {c.email}".lower():
                return False
            sm = summary(c)
            if mode == "owing":
                return sm["receivable"] > 0.005
            if mode == "deposit":
                return sm["deposit"] > 0.005
            if mode == "inactive":
                return str(c.status).lower() != "active"
            return True

        # headline numbers cover everyone, not just the filtered rows
        owing_n = sum(1 for c in all_c if summary(c)["receivable"] > 0.005)
        self.kpi_total.set(str(len(all_c)), f"{sum(1 for c in all_c if str(c.status).lower() == 'active')} marked active")
        self.kpi_owing.set(money(sum(summary(c)["receivable"] for c in all_c)),
                           f"across {owing_n} customer{'s' if owing_n != 1 else ''}")
        self.kpi_deposit.set(money(sum(summary(c)["deposit"] for c in all_c)), "customer advances not yet used")
        recent = 0
        for c in all_c:
            d = CustomerDirectoryService.days_since(summary(c)["last_sale_at"])
            if d is not None and d <= 30:
                recent += 1
        self.kpi_active.set(str(recent), "traded in the last 30 days")

        items = [c for c in all_c if match(c)]
        self.subtitle.setText(f"{len(items)} of {len(all_c)} customers shown")
        self.empty_lbl.setVisible(not items)
        self.table.setRowCount(0)
        for c in items:
            sm = summary(c)
            row = self.table.rowCount()
            self.table.insertRow(row)

            idi = QTableWidgetItem(str(c.id))
            idi.setTextAlignment(Qt.AlignCenter)
            idi.setForeground(Qt.gray)
            self.table.setItem(row, 0, idi)
            self.table.setItem(row, 1, QTableWidgetItem(""))
            self.table.setCellWidget(row, 1, self._name_cell(c))
            self.table.setItem(row, 2, QTableWidgetItem(c.phone or ""))
            days = CustomerDirectoryService.days_since(sm["last_sale_at"])
            last = QTableWidgetItem(_last_sale_text(days))
            if days is None:
                last.setForeground(Qt.gray)
            self.table.setItem(row, 3, last)

            self.table.setItem(row, 4, QTableWidgetItem(""))
            owe = sm["receivable"]
            self.table.setCellWidget(row, 4, self._chip_cell(
                money(owe) if owe > 0.005 else "Settled", "red" if owe > 0.005 else "grey"))
            self.table.setItem(row, 5, QTableWidgetItem(""))
            dep = sm["deposit"]
            self.table.setCellWidget(row, 5, self._chip_cell(
                money(dep) if dep > 0.005 else "-", "green" if dep > 0.005 else "grey"))
            self.table.setItem(row, 6, QTableWidgetItem(""))
            active = str(c.status).lower() == "active"
            self.table.setCellWidget(row, 6, self._chip_cell(
                "Active" if active else str(c.status).title(), "green" if active else "amber"))

    def add_customer(self):
        dlg = CustomerFormDialog(self, title="Add Customer")
        if dlg.exec_() == dlg.Accepted:
            payload = dlg.payload()

            err = CustomerFormDialog.validate(payload)
            if err:
                QMessageBox.critical(self, "Validation Error", err)
                return

            if state.customer_phone_exists(payload["phone"]):
                QMessageBox.critical(self, "Validation Error", "Phone already exists.")
                return

            state.create_customer(**payload)
            self.refresh()

    def edit_customer(self):
        cid = self.selected_customer_id()
        if not cid:
            QMessageBox.information(self, "Edit Customer", "Select a row first.")
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            QMessageBox.warning(self, "Edit Customer", "Customer not found.")
            return

        dlg = CustomerFormDialog(self, title="Edit Customer", initial={
            "full_name": c.full_name,
            "phone": c.phone,
            "email": c.email,
            "address": c.address,
            "status": c.status,
            "notes": c.notes,
        })
        if dlg.exec_() == dlg.Accepted:
            payload = dlg.payload()

            err = CustomerFormDialog.validate(payload)
            if err:
                QMessageBox.critical(self, "Validation Error", err)
                return

            if state.customer_phone_exists(payload["phone"], exclude_id=c.id):
                QMessageBox.critical(self, "Validation Error", "Phone already exists.")
                return

            state.update_customer(c.id, **payload)
            self.refresh()

    def delete_customer(self):
        cid = self.selected_customer_id()
        if not cid:
            QMessageBox.information(self, "Delete Customer", "Select a row first.")
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        ok = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete customer '{c.full_name}' (Phone: {c.phone})?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok == QMessageBox.Yes:
            state.delete_customer(cid)
            self.refresh()

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        cid = self.selected_customer_id()
        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        menu = QMenu(self)
        act_convert = menu.addAction("Convert to Supplier")
        act_convert.setEnabled(not c.is_supplier)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == act_convert:
            self.convert_to_supplier(c)

    def convert_to_supplier(self, c):
        ok = QMessageBox.question(
            self,
            "Convert to Supplier",
            f"Convert '{c.full_name}' to a supplier? They will keep appearing "
            f"in the Customers list and will also start appearing in the "
            f"Suppliers list.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok != QMessageBox.Yes:
            return

        try:
            SupplierProfileService.convert_to_supplier(c.id)
        except Exception as e:
            QMessageBox.critical(self, "Convert to Supplier", str(e))
            return

        state._load_customers_from_db()
        self.refresh()
        QMessageBox.information(self, "Converted", f"'{c.full_name}' is now also a supplier.")

    def open_profile(self):
        cid = self.selected_customer_id()
        if not cid:
            return

        c = next((x for x in state.customers if x.id == cid), None)
        if not c:
            return

        profile = CustomerProfileView(c, mode=self.mode)
        profile.setWindowTitle(f"Customer Profile - {c.full_name}")
        profile.resize(1100, 700)

        self._open_profiles.append(profile)
        profile.destroyed.connect(lambda: self._cleanup_profile(profile))
        profile.show()

    def _cleanup_profile(self, w):
        try:
            self._open_profiles = [x for x in self._open_profiles if x is not w]
        except Exception:
            pass