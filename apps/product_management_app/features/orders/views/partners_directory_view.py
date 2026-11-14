# apps/product_management_app/features/orders/views/partners_directory_view.py
"""
Orders Partner Directory (Companies and Suppliers):
  - Visual design and architecture matching the Customers Directory.
  - Dedicated KPI Cards: Total Partners, Outstanding Balance, Advance IN, Advance OUT, Active Orders.
  - Fast search, segmented switching (All, Companies, Suppliers) and balance filters.
  - Avatar initials icons, badges, and color-coded status/balance chips.
  - Prominent "+ New Company" and "+ New Supplier" buttons that immediately open the profile.
  - Double-click or "Open Profile" opens the dedicated PartnerProfilePage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt, QTimer, Signal
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from database.services.company_service import CompanyService
from database.services.order_profile_service import OrderProfileService
from database.services.order_sheet_service import OrderSheetService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from shared.auth.session import require_user_id
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented, money
from shared.uix.widgets.table_cells import ChipDelegate, NameDelegate, ROLE_BADGE, ROLE_SUB, ROLE_TONE
from apps.product_management_app.features.orders.views.partners_page import PartnerDialog

COLS = [
    "ID", "Partner", "Phone", "Total Orders",
    "Outstanding Balance", "Advance IN", "Advance OUT", "Active Orders", "Status"
]

TYPE_FILTERS = [
    ("all", "All Partners"),
    ("sales", "Companies (Sales)"),
    ("purchase", "Suppliers (Purchases)"),
]

STATUS_FILTERS = [
    ("all", "All"),
    ("debt", "Has Balance"),
    ("adv_in", "Has Advance IN"),
    ("adv_out", "Has Advance OUT"),
    ("active", "Has Active Orders"),
]

_TABLE_QSS = """
QTableWidget#PartnerTable { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px;
    gridline-color:transparent; outline:0; alternate-background-color:#ffffff; }
QTableWidget#PartnerTable::item { border-bottom:1px solid #f1f5f9; padding:4px 8px; color:#111827; }
QTableWidget#PartnerTable::item:selected { background:#ecfdf5; color:#065f46; }
QTableWidget#PartnerTable::item:hover { background:#f8fafc; }
QTableWidget#PartnerTable QHeaderView::section { background:#f8fafc; color:#6b7280; border:none;
    border-bottom:1px solid #e5e7eb; padding:10px 8px; font-size:11px; font-weight:700; }
"""


class PartnersDirectoryView(QWidget):
    open_partner = Signal(str, int)  # (kind, partner_id)

    def __init__(self, is_admin: bool = True, parent=None):
        super().__init__(parent)
        self.is_admin = is_admin
        self._all_partners: List[Dict[str, Any]] = []

        layout = make_scrollable_page(self, margins=(16, 14, 16, 14))
        layout.setSpacing(12)

        # Header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Orders • Partner Directory")
        title.setObjectName("Title")
        self.subtitle = QLabel("Companies we sell to & Suppliers we buy from. Open a profile to view activities or record advances.")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)

        # Buttons
        self.btn_new_company = QPushButton("+  New Company (Sales)")
        self.btn_new_company.setProperty("role", "primary")
        self.btn_new_company.setMinimumHeight(36)
        self.btn_new_company.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 6px 14px; border-radius: 6px;"
        )

        self.btn_new_supplier = QPushButton("+  New Supplier (Purchases)")
        self.btn_new_supplier.setMinimumHeight(36)
        self.btn_new_supplier.setStyleSheet(
            "background-color: #2563eb; color: white; font-weight: bold; padding: 6px 14px; border-radius: 6px;"
        )

        self.btn_new_company.setVisible(is_admin)
        self.btn_new_supplier.setVisible(is_admin)

        head.addWidget(self.btn_new_company)
        head.addWidget(self.btn_new_supplier)
        layout.addLayout(head)

        # KPI Cards
        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self.kpi_total = KpiCard("Partners", "0", "companies & suppliers", "blue")
        self.kpi_debt = KpiCard("Outstanding Balance", "0.00", "unpaid on orders", "red")
        self.kpi_adv_in = KpiCard("Advance IN", "0.00", "deposits held from companies", "green")
        self.kpi_adv_out = KpiCard("Advance OUT", "0.00", "advances given to suppliers", "purple")
        self.kpi_active = KpiCard("Active Orders", "0", "draft / on the road", "amber")

        for k in (self.kpi_total, self.kpi_debt, self.kpi_adv_in, self.kpi_adv_out, self.kpi_active):
            kpis.addWidget(k, 1)
        layout.addLayout(kpis)

        # Search & Filter bar
        top = QHBoxLayout()
        top.setSpacing(10)
        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("Search company or supplier name, contact person or phone")
        self.search_in.setClearButtonEnabled(True)
        self.search_in.setMinimumHeight(34)
        top.addWidget(self.search_in, 1)

        self.type_seg = Segmented(TYPE_FILTERS)
        top.addWidget(self.type_seg)

        self.filter_seg = Segmented(STATUS_FILTERS)
        top.addWidget(self.filter_seg)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setMinimumHeight(34)
        top.addWidget(self.btn_refresh)
        layout.addLayout(top)

        # Table
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("PartnerTable")
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

        # delegate columns size to content
        for col in (4, 5, 6, 7, 8):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setMinimumSectionSize(88)

        self.table.setItemDelegateForColumn(1, NameDelegate(self.table))
        chips = ChipDelegate(self.table)
        for col in (4, 5, 6, 7, 8):
            self.table.setItemDelegateForColumn(col, chips)

        layout.addWidget(self.table, 1)

        self.empty_lbl = QLabel("No partners match your search.")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setStyleSheet("color:#9ca3af; font-size:13px; padding:24px;")
        self.empty_lbl.setVisible(False)
        layout.addWidget(self.empty_lbl)

        # Actions
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.btn_open = QPushButton("Open Profile")
        self.btn_open.setStyleSheet(
            "background-color: #0f766e; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_delete = QPushButton("Delete Partner")
        self.btn_delete.setProperty("role", "danger")
        self.btn_delete.setVisible(is_admin)

        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)

        hint = QLabel("Double-click a partner to open their dedicated profile  —  right-click or use buttons above")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        actions.addWidget(hint)

        # Connections
        self.btn_new_company.clicked.connect(lambda: self._new_partner("sales"))
        self.btn_new_supplier.clicked.connect(lambda: self._new_partner("purchase"))
        self.btn_open.clicked.connect(self._open_selected)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_refresh.clicked.connect(self.refresh)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh)
        self.search_in.textChanged.connect(lambda _t: self._search_timer.start())
        self.type_seg.changed.connect(lambda _k: self.refresh())
        self.filter_seg.changed.connect(lambda _k: self.refresh())

        self.table.cellDoubleClicked.connect(lambda _r, _c: self._open_selected())

        self.refresh()

    def refresh(self):
        q = self.search_in.text().strip().lower()
        type_key = self.type_seg.key()
        flt_key = self.filter_seg.key()

        try:
            all_p = OrderProfileService.all_partners_summary(kind=type_key, search="")
        except Exception:
            all_p = []

        self._all_partners = all_p

        # KPI calculations across all partners
        tot_cnt = len(all_p)
        tot_debt = sum(p["outstanding"] for p in all_p)
        tot_adv_in = sum(p["advance_in"] for p in all_p)
        tot_adv_out = sum(p["advance_out"] for p in all_p)
        tot_active_orders = sum(p["active"] for p in all_p)

        debt_n = sum(1 for p in all_p if p["outstanding"] > 0.005)
        adv_in_n = sum(1 for p in all_p if p["advance_in"] > 0.005)
        adv_out_n = sum(1 for p in all_p if p["advance_out"] > 0.005)

        self.kpi_total.set(str(tot_cnt), f"{sum(1 for p in all_p if p['orders'] > 0)} with orders")
        self.kpi_debt.set(money(tot_debt), f"across {debt_n} partner{'s' if debt_n != 1 else ''}")
        self.kpi_adv_in.set(money(tot_adv_in), f"across {adv_in_n} company{'ies' if adv_in_n != 1 else ''}")
        self.kpi_adv_out.set(money(tot_adv_out), f"across {adv_out_n} supplier{'s' if adv_out_n != 1 else ''}")
        self.kpi_active.set(str(tot_active_orders), "orders in progress")

        def match(p):
            if q and q not in f"{p['name']} {p['contact_person']} {p['phone']} {p['address']}".lower():
                return False
            if flt_key == "debt":
                return p["outstanding"] > 0.005
            if flt_key == "adv_in":
                return p["advance_in"] > 0.005
            if flt_key == "adv_out":
                return p["advance_out"] > 0.005
            if flt_key == "active":
                return p["active"] > 0
            return True

        items = [p for p in all_p if match(p)]
        self.subtitle.setText(f"{len(items)} of {tot_cnt} partners shown")
        self.empty_lbl.setVisible(not items)

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            self.table.setRowCount(len(items))
            for row, p in enumerate(items):
                idi = QTableWidgetItem(str(p["id"]))
                idi.setTextAlignment(Qt.AlignCenter)
                idi.setForeground(Qt.gray)
                self.table.setItem(row, 0, idi)

                name_it = QTableWidgetItem(p["name"])
                sub_parts = [p["contact_person"], p["address"]]
                sub_text = " • ".join(x for x in sub_parts if x)
                name_it.setData(ROLE_SUB, sub_text)
                name_it.setData(ROLE_BADGE, p["badge"])
                self.table.setItem(row, 1, name_it)

                self.table.setItem(row, 2, QTableWidgetItem(p["phone"] or "—"))

                ord_text = f"{p['orders']} order{'s' if p['orders'] != 1 else ''}" if p['orders'] > 0 else "0"
                self.table.setItem(row, 3, QTableWidgetItem(ord_text))

                debt = p["outstanding"]
                adv_in = p["advance_in"]
                adv_out = p["advance_out"]
                active_cnt = p["active"]

                for col, text, tone in (
                    (4, money(debt) if debt > 0.005 else "Settled", "red" if debt > 0.005 else "grey"),
                    (5, money(adv_in) if adv_in > 0.005 else "—", "green" if adv_in > 0.005 else "grey"),
                    (6, money(adv_out) if adv_out > 0.005 else "—", "purple" if adv_out > 0.005 else "grey"),
                    (7, str(active_cnt), "amber" if active_cnt > 0 else "grey"),
                    (8, "Active", "green"),
                ):
                    it = QTableWidgetItem(text)
                    it.setData(ROLE_TONE, tone)
                    self.table.setItem(row, col, it)
        finally:
            self.table.setUpdatesEnabled(True)

    def _selected(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            return None
        try:
            pid = int(self.table.item(row, 0).text())
            pname = self.table.item(row, 1).text()
            return next((p for p in self._all_partners if p["id"] == pid and p["name"] == pname),
                        next((p for p in self._all_partners if p["id"] == pid), None))
        except Exception:
            return None

    def _open_selected(self):
        p = self._selected()
        if not p:
            QMessageBox.information(self, "Partner Profile", "Please select a partner first.")
            return
        self.open_partner.emit(p["kind"], p["id"])

    def _new_partner(self, kind: str):
        dlg = PartnerDialog(kind, self)
        if dlg.exec_() == dlg.Accepted and dlg.saved_id:
            self.refresh()
            self.open_partner.emit(kind, int(dlg.saved_id))

    def _delete_selected(self):
        p = self._selected()
        if not p:
            QMessageBox.information(self, "Delete Partner", "Please select a partner first.")
            return

        ok = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to remove '{p['name']}' from counterparties?\nPast order records will be preserved.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok == QMessageBox.Yes:
            try:
                OrderSheetService.delete_partner(p["kind"], int(p["id"]))
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error Deleting Partner", str(e))
