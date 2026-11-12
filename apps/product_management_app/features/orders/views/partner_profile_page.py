# apps/product_management_app/features/orders/views/partner_profile_page.py
"""
Dedicated Company / Supplier Profile:
  - All orders & activities in a single-row sheet.
  - Advance recordings (IN for Sale Orders, OUT for Purchase Orders) held safely.
  - "New Activity" button to create Sale Order or Purchase Order.
  - Single-row order tracking & finalization (Company Price, Company Weight, etc.).
  - "Use Advance" action on finalization and on rows with balance.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QColor, QFont
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QStackedWidget, QTabBar, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QFrame,
)

from apps.product_management_app.features.debt_center.sheet_style import STYLE
from apps.product_management_app.features.debt_center.views.people_page import kpi
from apps.product_management_app.features.orders.dialogs.apply_advance_dialog import ApplyAdvanceDialog
from apps.product_management_app.features.orders.dialogs.finalize_order_dialog import FinalizeOrderDialog
from apps.product_management_app.features.orders.dialogs.new_activity_dialog import NewActivityDialog
from apps.product_management_app.features.orders.dialogs.order_slip_dialog import OrderSlipDialog
from apps.product_management_app.features.orders.dialogs.record_advance_dialog import RecordAdvanceDialog
from database.services.order_money_service import OrderMoneyService
from database.services.order_profile_service import OrderProfileService
from database.services.order_sheet_service import OrderSheetService
from shared.uix.widgets.cards import avatar, chip, money

COLS = [
    "Order #", "Date", "Activity", "Product & Quality",
    "Initial / Ordered", "Company Verified", "Final Amount",
    "Status", "Settlement", "Actions"
]

ADV_COLS = ["ID", "Date", "Direction", "Amount", "Method", "Notes / Ref", "Status", "Recorded By"]

STATUS_COLOR = {
    "draft": "#64748b",
    "in_transit": "#2563eb",
    "confirmed": "#059669",
    "cancelled": "#dc2626",
}


def _day(dt) -> str:
    if not dt:
        return "-"
    if hasattr(dt, "strftime"):
        return dt.strftime("%d/%m/%Y")
    return str(dt)[:10]


class PartnerProfilePage(QWidget):
    back = Signal()
    open_order = Signal(int)
    new_order = Signal()

    def __init__(self, kind: str, partner_id: int, is_admin: bool = True):
        super().__init__()
        self.kind = "sales" if str(kind).lower() in ("sales", "company") else "purchase"
        self.partner_id = int(partner_id)
        self.is_admin = is_admin

        # Resolve partner across counterparties
        self.info = OrderProfileService.resolve_partner(self.partner_id, self.kind)
        self.partner_name = self.info["name"]
        self.company_id = self.info["company_id"]
        self.supplier_id = self.info["supplier_id"]

        self._orders: List[Dict[str, Any]] = []
        self._advances: List[Dict[str, Any]] = []

        self.setObjectName("dcRoot")
        self.setStyleSheet(STYLE)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        # Header Bar
        head = QHBoxLayout()
        self.btn_back = QPushButton("<  Back to Orders")
        self.btn_back.setObjectName("dcBack")
        self.btn_back.clicked.connect(self.back.emit)
        head.addWidget(self.btn_back)

        head.addWidget(avatar(self.partner_name, 44))

        box = QVBoxLayout()
        box.setSpacing(1)
        name = QLabel(self.partner_name)
        name.setStyleSheet("font-size: 19px; font-weight: 800; color: #0f172a;")
        bits = [
            self.info.get("contact_person") and f"Contact: {self.info['contact_person']}",
            self.info.get("phone"),
            self.info.get("address"),
        ]
        sub = QLabel("  •  ".join(b for b in bits if b) or "Company / Supplier Profile")
        sub.setObjectName("dcSub")
        box.addWidget(name)
        box.addWidget(sub)

        holder = QWidget()
        holder.setLayout(box)
        head.addWidget(holder)
        head.addStretch(1)

        # Action Buttons
        self.btn_new_activity = QPushButton("+  New Activity")
        self.btn_new_activity.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; font-size: 13px; "
            "padding: 8px 18px; border-radius: 5px;"
        )
        self.btn_new_activity.clicked.connect(self._open_new_activity)
        head.addWidget(self.btn_new_activity)

        self.btn_record_adv = QPushButton("+  Record Advance")
        self.btn_record_adv.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; font-size: 13px; "
            "padding: 8px 16px; border-radius: 5px;"
        )
        self.btn_record_adv.clicked.connect(self._open_record_advance)
        head.addWidget(self.btn_record_adv)

        root.addLayout(head)

        # KPI Summary Cards
        kp = QHBoxLayout()
        kp.setSpacing(10)
        self.k_adv_in = kpi("Available Advance (IN) • Sales", dark=True)
        self.k_adv_out = kpi("Available Advance (OUT) • Purchases", dark=True)
        self.k_active = kpi("Active Orders (In Progress)")
        self.k_value = kpi("Delivered & Finalized Value")
        self.k_bal = kpi("Outstanding Balance Due")

        for k in (self.k_adv_in, self.k_adv_out, self.k_active, self.k_value, self.k_bal):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        # Tab Bar
        self.tabs = QTabBar()
        self.tabs.setObjectName("msSheets")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Orders & Activities")
        self.tabs.addTab("Advances Ledger")
        root.addWidget(self.tabs)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Page 1: Orders Table
        orders_page = QWidget()
        ol = QVBoxLayout(orders_page)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)

        # Filter bar
        filter_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by order #, product, or quality...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(32)
        self.search.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self.search, 1)

        self.btn_filter_all = QPushButton("All Activities")
        self.btn_filter_active = QPushButton("Pending / In Transit")
        self.btn_filter_done = QPushButton("Finalized")
        self.btn_refresh = QPushButton("Refresh")

        filter_bar.addWidget(self.btn_filter_all)
        filter_bar.addWidget(self.btn_filter_active)
        filter_bar.addWidget(self.btn_filter_done)
        filter_bar.addWidget(self.btn_refresh)
        ol.addLayout(filter_bar)

        self.btn_filter_all.clicked.connect(lambda: self._set_status_filter("all"))
        self.btn_filter_active.clicked.connect(lambda: self._set_status_filter("active"))
        self.btn_filter_done.clicked.connect(lambda: self._set_status_filter("done"))
        self.btn_refresh.clicked.connect(self.reload)
        self._current_filter = "all"

        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("dcList")
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setShowGrid(False)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)  # Product & Quality stretches
        hh.setSectionResizeMode(9, QHeaderView.ResizeToContents)  # Actions
        ol.addWidget(self.table, 1)

        # Page 2: Advances Ledger
        adv_page = QWidget()
        al = QVBoxLayout(adv_page)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)

        adv_notice = QLabel(
            "<b>Notice:</b> Advances recorded here will <b>NEVER</b> be used automatically. "
            "They remain safely held in this profile until you explicitly click <b>Use Advance</b> during or after finalization."
        )
        adv_notice.setStyleSheet("background: #f0fdf4; border: 1px solid #86efac; border-radius: 4px; padding: 8px; color: #166534;")
        adv_notice.setWordWrap(True)
        al.addWidget(adv_notice)

        self.adv_table = QTableWidget(0, len(ADV_COLS))
        self.adv_table.setObjectName("dcList")
        self.adv_table.setHorizontalHeaderLabels(ADV_COLS)
        self.adv_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.adv_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.adv_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.adv_table.verticalHeader().setVisible(False)
        self.adv_table.verticalHeader().setDefaultSectionSize(40)
        self.adv_table.setShowGrid(False)
        self.adv_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        al.addWidget(self.adv_table, 1)

        adv_foot = QHBoxLayout()
        btn_adv_new = QPushButton("+ Record Advance")
        btn_adv_new.setStyleSheet("background: #0284c7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        btn_adv_new.clicked.connect(self._open_record_advance)
        adv_foot.addWidget(btn_adv_new)
        adv_foot.addStretch()
        al.addLayout(adv_foot)

        self.stack.addWidget(orders_page)
        self.stack.addWidget(adv_page)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)

    def _set_status_filter(self, flt: str):
        self._current_filter = flt
        self._apply_filter()

    def _open_new_activity(self):
        dlg = NewActivityDialog(
            partner_name=self.partner_name,
            company_id=self.company_id,
            supplier_id=self.supplier_id,
            default_activity=self.kind,
            parent=self,
        )
        if dlg.exec_() == dlg.Accepted:
            self.reload()

    def _open_record_advance(self):
        dlg = RecordAdvanceDialog(
            partner_name=self.partner_name,
            company_id=self.company_id,
            supplier_id=self.supplier_id,
            default_direction="in" if self.kind == "sales" else "out",
            parent=self,
        )
        if dlg.exec_() == dlg.Accepted:
            self.reload()

    def refresh(self):
        self.reload()

    def reload(self):
        # Refresh partner info
        self.info = OrderProfileService.resolve_partner(self.partner_id, self.kind)
        self.company_id = self.info["company_id"]
        self.supplier_id = self.info["supplier_id"]

        # Advance balances
        adv_bals = OrderProfileService.get_advance_balances(self.company_id, self.supplier_id)
        adv_in = adv_bals["advance_in"]
        adv_out = adv_bals["advance_out"]
        self.k_adv_in.val.setText(money(adv_in))
        self.k_adv_out.val.setText(money(adv_out))

        # Orders list
        self._orders = OrderProfileService.list_partner_orders(self.company_id, self.supplier_id)

        # KPIs
        active_cnt = sum(1 for o in self._orders if o["status"] in ("draft", "in_transit"))
        final_val = sum((o["final_total_amount"] or 0) for o in self._orders if o["status"] == "confirmed")
        tot_bal = sum((o["balance"] or 0) for o in self._orders if o["balance"] is not None)

        self.k_active.val.setText(str(active_cnt))
        self.k_value.val.setText(money(final_val))
        self.k_bal.val.setText(money(tot_bal))

        self._apply_filter()
        self._reload_advances()

    def _reload_advances(self):
        adv_list = []
        if self.company_id:
            adv_list.extend(OrderMoneyService.list_money("sales", partner_id=self.company_id))
        if self.supplier_id:
            adv_list.extend(OrderMoneyService.list_money("purchase", partner_id=self.supplier_id))

        self._advances = adv_list
        self.adv_table.setRowCount(len(adv_list))

        for i, a in enumerate(adv_list):
            dir_label = "IN (Sale Order Advance)" if a["direction"] == "in" else "OUT (Purchase Order Advance)"
            status_str = "Available" if a["status"] == "completed" else a["status"].title()
            if a.get("order_no"):
                status_str = f"Applied to {a['order_no']}"

            vals = [
                str(a["id"]),
                _day(a["date"]),
                dir_label,
                money(a["amount"]),
                (a.get("method") or "-").title(),
                a.get("note") or "-",
                status_str,
                a.get("by") or "-",
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 6:
                    if a["status"] == "completed":
                        it.setForeground(QColor("#059669"))
                    elif a["status"] == "applied":
                        it.setForeground(QColor("#2563eb"))
                    elif a["status"] == "voided":
                        it.setForeground(QColor("#dc2626"))
                self.adv_table.setItem(i, c, it)

    def _apply_filter(self):
        term = self.search.text().strip().lower()
        filtered = []

        for o in self._orders:
            # Status filter
            if self._current_filter == "active" and o["status"] not in ("draft", "in_transit"):
                continue
            if self._current_filter == "done" and o["status"] != "confirmed":
                continue

            # Search filter
            if term:
                text_blob = f"{o['order_no']} {o['product_name']} {o['quality']} {o['notes']}".lower()
                if term not in text_blob:
                    continue

            filtered.append(o)

        self._render_orders_table(filtered)

    def _render_orders_table(self, rows: List[Dict[str, Any]]):
        self.table.setRowCount(len(rows))

        # Check available advance pools
        adv_bals = OrderProfileService.get_advance_balances(self.company_id, self.supplier_id)
        has_adv_in = adv_bals["advance_in"] > 0.005
        has_adv_out = adv_bals["advance_out"] > 0.005

        for i, o in enumerate(rows):
            is_sale = o["kind"] == "sales"
            is_fert = str(o["product_type"]).lower() in ("fertilizer", "fertilizers")

            # 1. Order #
            it_no = QTableWidgetItem(o["order_no"])
            f = it_no.font()
            f.setBold(True)
            it_no.setFont(f)
            self.table.setItem(i, 0, it_no)

            # 2. Date
            self.table.setItem(i, 1, QTableWidgetItem(_day(o["created_at"])))

            # 3. Activity
            act_str = "Sale Order" if is_sale else "Purchase Order"
            it_act = QTableWidgetItem(act_str)
            it_act.setForeground(QColor("#059669" if is_sale else "#2563eb"))
            self.table.setItem(i, 2, it_act)

            # 4. Product & Quality
            q_str = f" ({o['quality']})" if o.get("quality") else ""
            self.table.setItem(i, 3, QTableWidgetItem(f"{o['product_name']}{q_str}"))

            # 5. Initial / Ordered
            if is_fert:
                init_str = f"{o['initial_qty_bags']:,.0f} bags @ {money(o['initial_price'])}/bag"
            else:
                init_str = f"{o['initial_weight_kg']:,.0f} kg ({o['initial_qty_bags']:,.0f} bags) @ {money(o['initial_price'])}/kg"
            self.table.setItem(i, 4, QTableWidgetItem(init_str))

            # 6. Company Verified
            comp_p = o.get("company_price")
            comp_w = o.get("company_weight_kg")
            comp_b = o.get("company_qty_bags")
            if comp_p is not None:
                if is_fert:
                    comp_str = f"{comp_b:,.0f} bags @ {money(comp_p)}/bag"
                else:
                    comp_str = f"{comp_w:,.0f} kg @ {money(comp_p)}/kg"
            else:
                comp_str = "— Awaiting —"
            it_comp = QTableWidgetItem(comp_str)
            if comp_p is None:
                it_comp.setForeground(QColor("#94a3b8"))
            self.table.setItem(i, 5, it_comp)

            # 7. Final Amount
            amt_val = o.get("final_total_amount")
            if amt_val is not None:
                amt_str = money(amt_val)
            else:
                amt_str = f"{money(o.get('estimated_total', 0))} (est.)"
            it_amt = QTableWidgetItem(amt_str)
            it_amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 6, it_amt)

            # 8. Status
            status_str = "Delivered" if o["status"] == "confirmed" else o["status"].replace("_", " ").title()
            it_st = QTableWidgetItem(status_str)
            it_st.setForeground(QColor(STATUS_COLOR.get(o["status"], "#0f172a")))
            f_st = it_st.font()
            f_st.setBold(True)
            it_st.setFont(f_st)
            self.table.setItem(i, 7, it_st)

            # 9. Settlement
            bal = o.get("balance")
            paid = o.get("paid_amount", 0)
            if o["status"] == "confirmed":
                if bal is not None and bal <= 0.005:
                    settle_str = "✓ Fully Paid"
                    color_settle = "#059669"
                elif paid > 0:
                    settle_str = f"Part: {money(paid)} • Bal: {money(bal)}"
                    color_settle = "#b45309"
                else:
                    settle_str = f"Unpaid: {money(bal)}"
                    color_settle = "#dc2626"
            else:
                settle_str = f"Advance: {money(paid)}" if paid > 0 else "—"
                color_settle = "#64748b"

            it_settle = QTableWidgetItem(settle_str)
            it_settle.setForeground(QColor(color_settle))
            self.table.setItem(i, 8, it_settle)

            # 10. Actions (CellWidget with row buttons)
            cell = QWidget()
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(4, 2, 4, 2)
            cell_lay.setSpacing(6)

            # Finalize / Edit button
            btn_fin = QPushButton("Finalize / Edit")
            btn_fin.setStyleSheet(
                "background-color: #0f766e; color: white; font-weight: 600; padding: 4px 8px; border-radius: 4px; font-size: 11px;"
            )
            btn_fin.clicked.connect(lambda *_, row_data=o: self._on_finalize_clicked(row_data))
            cell_lay.addWidget(btn_fin)

            # Use Advance button (Point 9: only if system knows/detects there is recorded advance in profile)
            has_adv_for_kind = has_adv_in if is_sale else has_adv_out
            has_bal = bal is not None and bal > 0.005

            if has_adv_for_kind and has_bal and o["status"] == "confirmed":
                btn_adv = QPushButton("Use Advance")
                btn_adv.setStyleSheet(
                    "background-color: #0284c7; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 11px;"
                )
                btn_adv.clicked.connect(lambda *_, row_data=o: self._on_use_advance_clicked(row_data))
                cell_lay.addWidget(btn_adv)

            # Slip button
            btn_slip = QPushButton("Slip")
            btn_slip.setStyleSheet(
                "background-color: #475569; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px;"
            )
            btn_slip.clicked.connect(lambda *_, row_data=o: self._on_slip_clicked(row_data))
            cell_lay.addWidget(btn_slip)

            # Delete button (for draft or cancelled orders)
            if self.is_admin and o["status"] in ("draft", "cancelled"):
                btn_del = QPushButton("Delete")
                btn_del.setStyleSheet(
                    "background-color: #ef4444; color: white; padding: 4px 6px; border-radius: 4px; font-size: 11px;"
                )
                btn_del.clicked.connect(lambda *_, row_data=o: self._on_delete_clicked(row_data))
                cell_lay.addWidget(btn_del)

            self.table.setCellWidget(i, 9, cell)

    def _on_finalize_clicked(self, order_data: Dict[str, Any]):
        dlg = FinalizeOrderDialog(order_data, self.partner_name, parent=self)
        if dlg.exec_() == dlg.Accepted:
            self.reload()

    def _on_use_advance_clicked(self, order_data: Dict[str, Any]):
        dlg = ApplyAdvanceDialog(order_data, self.partner_name, parent=self)
        if dlg.exec_() == dlg.Accepted:
            self.reload()

    def _on_slip_clicked(self, order_data: Dict[str, Any]):
        dlg = OrderSlipDialog(order_data, self.partner_name, parent=self)
        dlg.exec_()

    def _on_delete_clicked(self, order_data: Dict[str, Any]):
        ok = QMessageBox.question(
            self, "Delete Order",
            f"Are you sure you want to permanently delete order {order_data['order_no']}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ok == QMessageBox.Yes:
            try:
                OrderSheetService.delete_order(order_data["kind"], order_data["id"])
                self.reload()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
