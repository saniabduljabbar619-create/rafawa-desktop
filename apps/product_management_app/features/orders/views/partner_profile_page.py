# apps/product_management_app/features/orders/views/partner_profile_page.py
"""
Dedicated Company / Supplier Profile:
  - Exact design match with Customer Profile (_PROFILE_QSS, #ProfileBanner, #SumGroup, #ProfileTabs).
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
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from apps.product_management_app.features.orders.dialogs.apply_advance_dialog import ApplyAdvanceDialog
from apps.product_management_app.features.orders.dialogs.edit_activity_dialog import EditActivityDialog
from apps.product_management_app.features.orders.dialogs.finalize_order_dialog import FinalizeOrderDialog
from apps.product_management_app.features.orders.dialogs.new_activity_dialog import NewActivityDialog
from apps.product_management_app.features.orders.dialogs.order_slip_dialog import OrderSlipDialog
from apps.product_management_app.features.orders.dialogs.record_advance_dialog import RecordAdvanceDialog
from database.services.order_money_service import OrderMoneyService
from database.services.order_profile_service import OrderProfileService
from database.services.order_sheet_service import OrderSheetService
from shared.uix.widgets.cards import avatar, chip, money

_PROFILE_QSS = """
#ProfileBanner { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0b2a24, stop:1 #115e59); border-radius:14px; }
#ProfileBanner QLabel { background:transparent; }
#ProfileName { color:#ffffff; font-size:22px; font-weight:700; }
#ProfileMeta { color:rgba(255,255,255,0.72); font-size:12px; }
#ProfileBalCaption { color:rgba(255,255,255,0.6); font-size:10px; font-weight:700; letter-spacing:2px; }
#ProfileBalance { color:#ffffff; font-size:26px; font-weight:700; }
#SumGroup { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; }
#SumHeading { color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px; background:transparent; }
#SumCaption { color:#9ca3af; font-size:11px; background:transparent; }
QTabWidget#ProfileTabs::pane { border:none; border-top:1px solid #e5e7eb; background:transparent; }
QTabWidget#ProfileTabs QTabBar::tab { background:transparent; color:#6b7280; padding:9px 14px; border:none;
    border-bottom:2px solid transparent; font-weight:600; }
QTabWidget#ProfileTabs QTabBar::tab:hover { color:#111827; }
QTabWidget#ProfileTabs QTabBar::tab:selected { color:#0f766e; border-bottom:2px solid #0f766e; }
#ProfileRoot QTableWidget { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px;
    gridline-color:transparent; alternate-background-color:#f9fafb; outline:0; }
#ProfileRoot QTableWidget::item { padding:4px 8px; border-bottom:1px solid #f1f5f9; }
#ProfileRoot QTableWidget::item:selected { background:#ecfdf5; color:#065f46; }
#ProfileRoot QTableWidget QHeaderView::section { background:#f8fafc; color:#6b7280; border:none;
    border-bottom:1px solid #e5e7eb; padding:8px; font-size:11px; font-weight:700; }
"""

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
        self._current_filter = "all"

        self.setObjectName("ProfileRoot")
        self.setStyleSheet(_PROFILE_QSS)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ---------------------------------------------------------
        # BACK BUTTON
        # ---------------------------------------------------------
        top_nav = QHBoxLayout()
        self.btn_back = QPushButton("<  Back to Orders")
        self.btn_back.setStyleSheet(
            "QPushButton { background: transparent; color: #4b5563; font-weight: 600; font-size: 13px; border: none; padding: 4px 8px; }"
            "QPushButton:hover { color: #111827; background: #e5e7eb; border-radius: 4px; }"
        )
        self.btn_back.clicked.connect(self.back.emit)
        top_nav.addWidget(self.btn_back)
        top_nav.addStretch(1)
        root.addLayout(top_nav)

        # ---------------------------------------------------------
        # 1. HEADER BANNER (Dark Teal Gradient, Avatar, Name, Meta, Balance & Buttons)
        # ---------------------------------------------------------
        banner = QFrame()
        banner.setObjectName("ProfileBanner")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(16)

        bl.addWidget(avatar(self.partner_name, 56, on_dark=True))

        ident = QVBoxLayout()
        ident.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        title = QLabel(self.partner_name)
        title.setObjectName("ProfileName")
        name_row.addWidget(title)
        name_row.addWidget(chip("Company & Supplier", "blue"))
        name_row.addWidget(chip("Active", "green"))
        name_row.addStretch(1)
        ident.addLayout(name_row)

        bits = []
        if self.company_id:
            bits.append(f"Company ID: {self.company_id}")
        if self.supplier_id:
            bits.append(f"Supplier ID: {self.supplier_id}")
        if self.info.get("phone"):
            bits.append(self.info["phone"])
        if self.info.get("contact_person"):
            bits.append(f"Contact: {self.info['contact_person']}")
        if self.info.get("address"):
            bits.append(self.info["address"])
        meta = QLabel("   •   ".join(bits) or "Dedicated Orders Profile")
        meta.setObjectName("ProfileMeta")
        ident.addWidget(meta)
        bl.addLayout(ident, 1)

        # Balance display
        bal_box = QVBoxLayout()
        bal_box.setSpacing(0)
        cap = QLabel("OUTSTANDING BALANCE")
        cap.setObjectName("ProfileBalCaption")
        cap.setAlignment(Qt.AlignRight)
        self.banner_balance = QLabel("₦0.00")
        self.banner_balance.setObjectName("ProfileBalance")
        self.banner_balance.setAlignment(Qt.AlignRight)
        bal_box.addWidget(cap)
        bal_box.addWidget(self.banner_balance)
        bl.addLayout(bal_box)

        # Action Buttons in Banner
        act_box = QVBoxLayout()
        act_box.setSpacing(6)
        self.btn_new_activity = QPushButton("+  New Activity")
        self.btn_new_activity.setStyleSheet(
            "background-color: #059669; color: white; font-weight: 700; font-size: 13px; "
            "padding: 6px 16px; border-radius: 6px; border: none;"
        )
        self.btn_new_activity.setMinimumHeight(34)
        self.btn_new_activity.clicked.connect(self._open_new_activity)

        self.btn_record_adv = QPushButton("+  Record Advance")
        self.btn_record_adv.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: 700; font-size: 13px; "
            "padding: 6px 16px; border-radius: 6px; border: none;"
        )
        self.btn_record_adv.setMinimumHeight(34)
        self.btn_record_adv.clicked.connect(self._open_record_advance)

        act_box.addWidget(self.btn_new_activity)
        act_box.addWidget(self.btn_record_adv)
        bl.addLayout(act_box)

        root.addWidget(banner)

        # ---------------------------------------------------------
        # 2. SUMMARY BAR (Themed #SumGroup boxes matching Customer Profile)
        # ---------------------------------------------------------
        self._build_summary_bar(root)

        # ---------------------------------------------------------
        # 3. TABS (QTabWidget#ProfileTabs)
        # ---------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ProfileTabs")
        self.tabs.setDocumentMode(True)
        _f = self.tabs.tabBar().font()
        _f.setWeight(QFont.DemiBold)
        self.tabs.tabBar().setFont(_f)

        # --- Tab 1: Orders & Activities ---
        tab_orders = QWidget()
        ol = QVBoxLayout(tab_orders)
        ol.setContentsMargins(0, 10, 0, 0)
        ol.setSpacing(10)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

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

        for b in (self.btn_filter_all, self.btn_filter_active, self.btn_filter_done, self.btn_refresh):
            b.setMinimumHeight(32)
            filter_bar.addWidget(b)

        self.btn_filter_all.clicked.connect(lambda: self._set_status_filter("all"))
        self.btn_filter_active.clicked.connect(lambda: self._set_status_filter("active"))
        self.btn_filter_done.clicked.connect(lambda: self._set_status_filter("done"))
        self.btn_refresh.clicked.connect(self.reload)
        ol.addLayout(filter_bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setShowGrid(False)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(9, QHeaderView.ResizeToContents)
        ol.addWidget(self.table, 1)

        # Selected Order Detail Summary Box
        self.order_detail_box = QLabel("Click an order row to see detailed breakdown and notes here.")
        self.order_detail_box.setWordWrap(True)
        self.order_detail_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.order_detail_box.setMinimumHeight(68)
        self.order_detail_box.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.order_detail_box.setStyleSheet(
            "background:#f6f7fb; border:1px solid #dfe3ee; border-radius:8px; padding:10px; color:#1c2333; font-size:12px;"
        )
        ol.addWidget(self.order_detail_box)
        self.table.currentCellChanged.connect(self._show_order_detail)
        self.table.cellDoubleClicked.connect(self._on_table_double_clicked)

        self.tabs.addTab(tab_orders, "Orders & Activities")

        # --- Tab 2: Advances Ledger ---
        tab_adv = QWidget()
        al = QVBoxLayout(tab_adv)
        al.setContentsMargins(0, 10, 0, 0)
        al.setSpacing(10)

        adv_notice = QLabel(
            "<b>Notice:</b> Advances recorded here will <b>NEVER</b> be used automatically. "
            "They remain safely held in this profile until you explicitly click <b>Use Advance</b> during or after finalization."
        )
        adv_notice.setStyleSheet(
            "background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 10px; color: #166534; font-size: 12.5px;"
        )
        adv_notice.setWordWrap(True)
        al.addWidget(adv_notice)

        self.adv_table = QTableWidget(0, len(ADV_COLS))
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
        btn_adv_new.setStyleSheet(
            "background: #0284c7; color: white; font-weight: bold; padding: 7px 16px; border-radius: 5px; border: none;"
        )
        btn_adv_new.clicked.connect(self._open_record_advance)
        adv_foot.addWidget(btn_adv_new)
        adv_foot.addStretch()
        al.addLayout(adv_foot)

        self.tabs.addTab(tab_adv, "Advances Ledger")
        root.addWidget(self.tabs, 1)

    def _build_summary_bar(self, parent_layout):
        row = QHBoxLayout()
        row.setSpacing(12)

        def group(heading, cards):
            box = QFrame()
            box.setObjectName("SumGroup")
            gl = QVBoxLayout(box)
            gl.setContentsMargins(14, 10, 14, 10)
            gl.setSpacing(6)
            h = QLabel(heading.upper())
            h.setObjectName("SumHeading")
            gl.addWidget(h)
            inner = QHBoxLayout()
            inner.setSpacing(18)
            labels = []
            for caption, color in cards:
                col = QVBoxLayout()
                col.setSpacing(0)
                c = QLabel(caption)
                c.setObjectName("SumCaption")
                v = QLabel("0.00")
                v.setStyleSheet(f"font-size:18px; font-weight:700; color:{color}; background:transparent;")
                col.addWidget(c)
                col.addWidget(v)
                inner.addLayout(col)
                labels.append(v)
            gl.addLayout(inner)
            row.addWidget(box, 1)
            return labels

        self.sum_adv_in, self.sum_adv_out = group("Advances", [("Available In (Sales)", "#059669"), ("Available Out (Purchases)", "#2563eb")])
        self.sum_orders_active, self.sum_orders_done = group("Orders", [("Active / On Road", "#d97706"), ("Delivered / Finalized", "#0f766e")])
        self.sum_paid_total, self.sum_balance_total = group("Settlement", [("Paid to Date", "#047857"), ("Outstanding Balance", "#dc2626")])
        parent_layout.addLayout(row)

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
        # Refresh partner identity
        self.info = OrderProfileService.resolve_partner(self.partner_id, self.kind)
        self.company_id = self.info["company_id"]
        self.supplier_id = self.info["supplier_id"]

        # Advance balances
        adv_bals = OrderProfileService.get_advance_balances(self.company_id, self.supplier_id)
        adv_in = adv_bals["advance_in"]
        adv_out = adv_bals["advance_out"]
        self.sum_adv_in.setText(money(adv_in))
        self.sum_adv_out.setText(money(adv_out))

        # Orders list
        self._orders = OrderProfileService.list_partner_orders(self.company_id, self.supplier_id)

        # Figures
        active_cnt = sum(1 for o in self._orders if o["status"] in ("draft", "in_transit"))
        final_val = sum((o["final_total_amount"] or 0) for o in self._orders if o["status"] == "confirmed")
        tot_paid = sum((o["paid_amount"] or 0) for o in self._orders)
        tot_bal = sum((o["balance"] or 0) for o in self._orders if o["balance"] is not None)

        self.sum_orders_active.setText(str(active_cnt))
        self.sum_orders_done.setText(money(final_val))
        self.sum_paid_total.setText(money(tot_paid))
        self.sum_balance_total.setText(money(tot_bal))
        self.banner_balance.setText(money(tot_bal))

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
            if self._current_filter == "active" and o["status"] not in ("draft", "in_transit"):
                continue
            if self._current_filter == "done" and o["status"] != "confirmed":
                continue

            if term:
                text_blob = f"{o['order_no']} {o['product_name']} {o['quality']} {o['notes']}".lower()
                if term not in text_blob:
                    continue

            filtered.append(o)

        self._render_orders_table(filtered)

    def _render_orders_table(self, rows: List[Dict[str, Any]]):
        self.table.setRowCount(len(rows))

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

            # 10. Actions (CellWidget)
            cell = QWidget()
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(4, 2, 4, 2)
            cell_lay.setSpacing(6)

            # Edit button (always available to edit product, quality, price, bags, kg, expected date, notes)
            btn_edit = QPushButton("Edit")
            btn_edit.setStyleSheet(
                "background-color: #334155; color: white; font-weight: 600; padding: 4px 8px; border-radius: 4px; font-size: 11px; border: none;"
            )
            btn_edit.clicked.connect(lambda *_, row_data=o: self._on_edit_clicked(row_data))
            cell_lay.addWidget(btn_edit)

            # Finalize button (for draft or in_transit orders awaiting company verification)
            if o["status"] != "confirmed":
                btn_fin = QPushButton("Finalize")
                btn_fin.setStyleSheet(
                    "background-color: #0f766e; color: white; font-weight: 600; padding: 4px 8px; border-radius: 4px; font-size: 11px; border: none;"
                )
                btn_fin.clicked.connect(lambda *_, row_data=o: self._on_finalize_clicked(row_data))
                cell_lay.addWidget(btn_fin)

            # Use Advance button (Point 9: only if system knows/detects there is recorded advance in profile)
            has_adv_for_kind = has_adv_in if is_sale else has_adv_out
            has_bal = bal is not None and bal > 0.005

            if has_adv_for_kind and has_bal and o["status"] == "confirmed":
                btn_adv = QPushButton("Use Advance")
                btn_adv.setStyleSheet(
                    "background-color: #0284c7; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 11px; border: none;"
                )
                btn_adv.clicked.connect(lambda *_, row_data=o: self._on_use_advance_clicked(row_data))
                cell_lay.addWidget(btn_adv)

            # Slip button
            btn_slip = QPushButton("Slip")
            btn_slip.setStyleSheet(
                "background-color: #475569; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; border: none;"
            )
            btn_slip.clicked.connect(lambda *_, row_data=o: self._on_slip_clicked(row_data))
            cell_lay.addWidget(btn_slip)

            # Delete button (for draft or cancelled orders)
            if self.is_admin and o["status"] in ("draft", "cancelled"):
                btn_del = QPushButton("Delete")
                btn_del.setStyleSheet(
                    "background-color: #ef4444; color: white; padding: 4px 6px; border-radius: 4px; font-size: 11px; border: none;"
                )
                btn_del.clicked.connect(lambda *_, row_data=o: self._on_delete_clicked(row_data))
                cell_lay.addWidget(btn_del)

            self.table.setCellWidget(i, 9, cell)

    def _show_order_detail(self, row: int, _col: int):
        if 0 <= row < len(self._orders):
            o = self._orders[row]
            is_sale = o["kind"] == "sales"
            act_name = "Sale Order (Selling to Company)" if is_sale else "Purchase Order (Buying from Supplier)"
            dt_str = o["created_at"].strftime("%A %d %B %Y - %I:%M %p") if hasattr(o["created_at"], "strftime") else str(o["created_at"])
            prod = o["product_name"]
            qual = o.get("quality") or "Standard"
            is_fert = str(o["product_type"]).lower() in ("fertilizer", "fertilizers")

            if is_fert:
                qty_s = f"Initial: <b>{o['initial_qty_bags']:,.0f} bags</b> @ ₦{o['initial_price']:,.2f}/bag"
            else:
                qty_s = f"Initial: <b>{o['initial_weight_kg']:,.0f} kg ({o['initial_qty_bags']:,.0f} bags)</b> @ ₦{o['initial_price']:,.2f}/kg"

            if o["status"] == "confirmed":
                comp_p = o.get("company_price") or 0.0
                comp_w = o.get("company_weight_kg") or 0.0
                comp_b = o.get("company_qty_bags") or 0.0
                if is_fert:
                    res_s = f"Company Verified: <b>{comp_b:,.0f} bags</b> @ ₦{comp_p:,.2f}/bag  |  Final: <b>{money(o['final_total_amount'])}</b>"
                else:
                    res_s = f"Company Verified: <b>{comp_w:,.0f} kg</b> @ ₦{comp_p:,.2f}/kg  |  Final: <b>{money(o['final_total_amount'])}</b>"
            else:
                res_s = "Status: <b>Awaiting Company Verification / Finalization</b>"

            notes_s = f"Notes: {o['notes']}" if o.get("notes") else "No additional notes."
            self.order_detail_box.setText(
                f"<b>{o['order_no']}</b>  •  {act_name}  •  {dt_str}\n"
                f"Product: <b>{prod}</b> (Quality: {qual})  |  {qty_s}\n"
                f"{res_s}  |  Paid: {money(o['paid_amount'])}  |  Balance: {money(o['balance']) if o['balance'] is not None else '-'}\n"
                f"{notes_s}"
            )

    def _on_table_double_clicked(self, row: int, _col: int):
        if 0 <= row < len(self._orders):
            self._on_edit_clicked(self._orders[row])

    def _on_edit_clicked(self, order_data: Dict[str, Any]):
        dlg = EditActivityDialog(order_data, self.partner_name, parent=self)
        if dlg.exec_() == dlg.Accepted:
            self.reload()

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
