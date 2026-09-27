# apps/product_management_app/features/admin_users/views/user_profile_dialog.py
from __future__ import annotations
from datetime import date, timedelta
from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDateEdit, QCheckBox, QPushButton
)
from database.services.user_admin_service import UserAdminService
from database.services.user_activity_service import UserActivityService
from apps.product_management_app.features.admin_users.views.user_activity_details_dialog import (
    UserActivityDetailsDialog
)
from PySide2.QtGui import QFont  


class UserProfileDialog(QDialog):
    def __init__(self, user_id: int, parent=None):
        super().__init__(parent)
        self.user_id = int(user_id or 0)
        
        # BOUNDED RANGE INITIALIZATION: Default scope is Today Only
        self.current_start_date = date.today()
        self.current_end_date = date.today()

        self.setWindowTitle("User Profile Statement")
        self.setModal(True)
        self.resize(1150, 750)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 1. Header & User Info
        uid, username, role, active = UserAdminService.get_user_header(self.user_id)
        self.role = (role or "").strip().lower()

        header = QHBoxLayout()
        title = QLabel(f"{username}  •  {role}")
        title.setStyleSheet("font-size: 16px; font-weight: 900;")
        
        # DATE RANGE FILTERS
        filter_box = QHBoxLayout()
        filter_box.setSpacing(6)
        
        self.date_filter_chk = QCheckBox("Filter by Date Range")
        self.date_filter_chk.setChecked(False)
        self.date_filter_chk.stateChanged.connect(self._on_date_filter_toggled)
        filter_box.addWidget(self.date_filter_chk)
        
        filter_box.addWidget(QLabel("From:"))
        self.start_date_picker = QDateEdit()
        self.start_date_picker.setCalendarPopup(True)
        self.start_date_picker.setDisplayFormat("yyyy-MM-dd")
        self.start_date_picker.setDate(QDate.currentDate().addDays(-30))
        self.start_date_picker.dateChanged.connect(self._on_date_bounds_changed)
        self.start_date_picker.setEnabled(False)
        filter_box.addWidget(self.start_date_picker)
        
        filter_box.addWidget(QLabel("To:"))
        self.end_date_picker = QDateEdit()
        self.end_date_picker.setCalendarPopup(True)
        self.end_date_picker.setDisplayFormat("yyyy-MM-dd")
        self.end_date_picker.setDate(QDate.currentDate())
        self.end_date_picker.dateChanged.connect(self._on_date_bounds_changed)
        self.end_date_picker.setEnabled(False)
        filter_box.addWidget(self.end_date_picker)
        
        badge = QLabel("ACTIVE" if active else "DISABLED")
        badge.setStyleSheet(
            "padding: 4px 10px; border-radius: 10px; "
            f"background: {'#e9f7ef' if active else '#fdecea'}; "
            f"color: {'#1e7e34' if active else '#a61b1b'}; "
            "font-weight: 800;"
        )
        
        header.addWidget(title)
        header.addStretch(1)
        header.addLayout(filter_box)
        header.addSpacing(20)
        header.addWidget(badge)
        root.addLayout(header)

        # 2. Summary Tiles Row
        self.tiles_layout = QHBoxLayout()
        self.tiles_layout.setSpacing(10)
        root.addLayout(self.tiles_layout)

        # 3. Tabs Setup
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tab_purchases = self._tab_table(["ID", "Purchase No", "Status", "Total", "Completed", "Created"])
        self.tab_sales = self._tab_table(["ID", "Sale No", "Status", "Total", "Completed", "Created"])
        self.tab_payments = self._tab_table(["ID", "Type", "Entity", "Dir", "Amount", "Method", "Created", "Notes"])
        self.tab_sale_returns = self._tab_table(["ID", "Sale ID", "Amount", "Reason", "Created"])
        self.tab_purchase_returns = self._tab_table(["ID", "Purchase ID", "Amount", "Reason", "Created"])

        self.tabs.addTab(self.tab_purchases["widget"], "Purchases")
        self.tabs.addTab(self.tab_sales["widget"], "Sales")
        self.tabs.addTab(self.tab_payments["widget"], "Payments")
        self.tabs.addTab(self.tab_sale_returns["widget"], "Sale Returns")
        self.tabs.addTab(self.tab_purchase_returns["widget"], "Purchase Returns")

        # Advances Tab View Container
        self.tab_advances = QWidget()
        adv_layout = QVBoxLayout(self.tab_advances)
        self.card_adv_in = self._tile("Advance IN", "", 0)
        self.card_adv_out = self._tile("Advance OUT", "", 0)
        cards_row = QHBoxLayout()
        cards_row.addWidget(self.card_adv_in)
        cards_row.addWidget(self.card_adv_out)
        adv_layout.addLayout(cards_row)

        self.advance_table = QTableWidget(0, 6)
        self.advance_table.setHorizontalHeaderLabels(["ID", "Direction", "Amount", "Method", "Notes", "Created"])
        self.advance_table.verticalHeader().setVisible(False)
        self.advance_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        adv_layout.addWidget(self.advance_table, 1)
        self.tabs.addTab(self.tab_advances, "Advances")

        # 🚀 4. INJECTED NEW TAB: ADVANCE SETTLEMENT PANEL
        self.tab_settlements = QWidget()
        set_layout = QVBoxLayout(self.tab_settlements)
        
        info_banner = QLabel("📜 Active ledger logs containing an initial Advance Allocation that have an outstanding balance due:")
        info_banner.setStyleSheet("color: #495057; font-weight: bold; padding: 4px;")
        set_layout.addWidget(info_banner)

        self.settlement_table = QTableWidget(0, 7)
        self.settlement_table.setHorizontalHeaderLabels([
            "Reference ID", "Document Type", "Invoice No", "Net Grand Total", "Advance Factored", "Remaining Balance Due", "Action Panel"
        ])
        self.settlement_table.verticalHeader().setVisible(False)
        self.settlement_table.setAlternatingRowColors(True)
        self.settlement_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.settlement_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        set_layout.addWidget(self.settlement_table, 1)
        self.tabs.addTab(self.tab_settlements, "Advance Settlement")

        # Connect double-click triggers
        self.tab_purchases["table"].cellDoubleClicked.connect(self._open_details_from_table)
        self.tab_sales["table"].cellDoubleClicked.connect(self._open_details_from_table)
        self.tab_payments["table"].cellDoubleClicked.connect(self._open_details_from_table)
        self.tab_sale_returns["table"].cellDoubleClicked.connect(self._open_details_from_table)
        self.tab_purchase_returns["table"].cellDoubleClicked.connect(self._open_details_from_table)
        self.advance_table.cellDoubleClicked.connect(self._open_details_from_table)

        # Core execution
        self._refresh_ui()

    def _on_date_filter_toggled(self, state):
        is_active = (state == Qt.Checked)
        self.start_date_picker.setEnabled(is_active)
        self.end_date_picker.setEnabled(is_active)
        self._recompute_date_bounds()

    def _on_date_bounds_changed(self):
        if self.date_filter_chk.isChecked():
            self._recompute_date_bounds()

    def _recompute_date_bounds(self):
        if not self.date_filter_chk.isChecked():
            self.current_start_date = date.today()
            self.current_end_date = date.today()
        else:
            q_start = self.start_date_picker.date()
            q_end = self.end_date_picker.date()
            self.current_start_date = date(q_start.year(), q_start.month(), q_start.day())
            self.current_end_date = date(q_end.year(), q_end.month(), q_end.day())
            if self.current_start_date > self.current_end_date:
                return
        self._refresh_ui()

    def _refresh_ui(self):
        summary = UserActivityService.get_summary(self.user_id, start_date=self.current_start_date, end_date=self.current_end_date)
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            if item.widget(): 
                item.widget().deleteLater()

        self.tiles_layout.addWidget(self._tile("Purchases (Net)", summary["purchases"]["count"], summary["purchases"]["net"]))
        self.tiles_layout.addWidget(self._tile("Sales (Net)", summary["sales"]["count"], summary["sales"]["net"]))
        
        self.top_tile_adv_in = self._tile("Advance IN", "", 0.00)
        self.top_tile_adv_out = self._tile("Advance OUT", "", 0.00)
        self.tiles_layout.addWidget(self.top_tile_adv_in)
        self.tiles_layout.addWidget(self.top_tile_adv_out)

        self._load_tables()

    def _load_tables(self):
        start_dt = self.current_start_date
        end_dt = self.current_end_date

        # 1. Populate Purchases
        purchases = UserActivityService.list_purchases(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        t_purch = self.tab_purchases["table"]
        t_purch.setRowCount(len(purchases))
        for r, p in enumerate(purchases):
            t_purch.setItem(r, 0, QTableWidgetItem(str(p["id"])))
            t_purch.setItem(r, 1, QTableWidgetItem(str(p["purchase_no"])))
            t_purch.setItem(r, 2, QTableWidgetItem(str(p["status"])))
            t_purch.setItem(r, 3, QTableWidgetItem(f"{float(p['total']):,.2f}"))
            t_purch.setItem(r, 4, QTableWidgetItem(str(p["completed_at"])))
            t_purch.setItem(r, 5, QTableWidgetItem(str(p["created_at"])))
            self._set_row_meta(t_purch, r, {"kind": "purchase", "id": p["id"]})

        # 2. Populate Sales
        sales = UserActivityService.list_sales(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        t_sales = self.tab_sales["table"]
        t_sales.setRowCount(len(sales))
        for r, s_row in enumerate(sales):
            t_sales.setItem(r, 0, QTableWidgetItem(str(s_row["id"])))
            t_sales.setItem(r, 1, QTableWidgetItem(str(s_row["sale_no"])))
            t_sales.setItem(r, 2, QTableWidgetItem(str(s_row["status"])))
            t_sales.setItem(r, 3, QTableWidgetItem(f"{float(s_row['total']):,.2f}"))
            t_sales.setItem(r, 4, QTableWidgetItem(str(s_row["completed_at"])))
            t_sales.setItem(r, 5, QTableWidgetItem(str(s_row["created_at"])))
            self._set_row_meta(t_sales, r, {"kind": "sale", "id": s_row["id"]})

        # 3. Populate Payments
        payments = UserActivityService.list_payments(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        t_pmts = self.tab_payments["table"]
        t_pmts.setRowCount(len(payments))
        for r, p in enumerate(payments):
            t_pmts.setItem(r, 0, QTableWidgetItem(str(p["id"])))
            t_pmts.setItem(r, 1, QTableWidgetItem(str(p["entity_type"])))
            t_pmts.setItem(r, 2, QTableWidgetItem(str(p["entity_id"])))
            t_pmts.setItem(r, 3, QTableWidgetItem(str(p["direction"])))
            t_pmts.setItem(r, 4, QTableWidgetItem(f"{float(p['amount']):,.2f}"))
            t_pmts.setItem(r, 5, QTableWidgetItem(str(p["method"])))
            t_pmts.setItem(r, 6, QTableWidgetItem(str(p["created_at"])))
            t_pmts.setItem(r, 7, QTableWidgetItem(str(p["notes"])))
            self._set_row_meta(t_pmts, r, {"kind": "payment", "id": p["id"]})

        # 4. Populate Sale Returns
        s_rets = UserActivityService.list_sale_returns(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        t_s_rets = self.tab_sale_returns["table"]
        t_s_rets.setRowCount(len(s_rets))
        for r, sr in enumerate(s_rets):
            t_s_rets.setItem(r, 0, QTableWidgetItem(str(sr["id"])))
            t_s_rets.setItem(r, 1, QTableWidgetItem(str(sr["sale_id"])))
            t_s_rets.setItem(r, 2, QTableWidgetItem(f"{float(sr['amount']):,.2f}"))
            t_s_rets.setItem(r, 3, QTableWidgetItem(str(sr["reason"])))
            t_s_rets.setItem(r, 4, QTableWidgetItem(str(sr["created_at"])))
            self._set_row_meta(t_s_rets, r, {"kind": "sale_return", "id": sr["id"]})

        # 5. Populate Purchase Returns
        p_rets = UserActivityService.list_purchase_returns(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        t_p_rets = self.tab_purchase_returns["table"]
        t_p_rets.setRowCount(len(p_rets))
        for r, pr in enumerate(p_rets):
            t_p_rets.setItem(r, 0, QTableWidgetItem(str(pr["id"])))
            t_p_rets.setItem(r, 1, QTableWidgetItem(str(pr["purchase_id"])))
            t_p_rets.setItem(r, 2, QTableWidgetItem(f"{float(pr['amount']):,.2f}"))
            t_p_rets.setItem(r, 3, QTableWidgetItem(str(pr["reason"])))
            t_p_rets.setItem(r, 4, QTableWidgetItem(str(pr["created_at"])))
            self._set_row_meta(t_p_rets, r, {"kind": "purchase_return", "id": pr["id"]})

        # 6. Populate Advances
        advs = UserActivityService.list_advances(self.user_id, 400, start_date=start_dt, end_date=end_dt)
        self.advance_table.setRowCount(len(advs))
        for r, a in enumerate(advs):
            amt = float(a["amount"] or 0)
            self.advance_table.setItem(r, 0, QTableWidgetItem(str(a["id"])))
            self.advance_table.setItem(r, 1, QTableWidgetItem(a["direction"]))
            self.advance_table.setItem(r, 2, QTableWidgetItem(f"{amt:.2f}"))
            self.advance_table.setItem(r, 3, QTableWidgetItem(a["method"]))
            self.advance_table.setItem(r, 4, QTableWidgetItem(a["notes"]))
            self.advance_table.setItem(r, 5, QTableWidgetItem(a["created_at"]))
            self._set_row_meta(self.advance_table, r, {"kind": "advance", "id": a["id"]})

        # Cards show NET advance exposure (settlements netted against advances),
        # not the gross sum of history rows - so a settled advance shows 0.
        adv_totals = UserActivityService.get_summary(
            self.user_id, start_date=start_dt, end_date=end_dt
        ).get("payments", {})
        tin = float(adv_totals.get("advance_in", 0.0))
        tout = float(adv_totals.get("advance_out", 0.0))

        self.card_adv_in.layout().itemAt(1).widget().setText(f"{tin:,.2f}")
        self.card_adv_out.layout().itemAt(1).widget().setText(f"{tout:,.2f}")
        self.top_tile_adv_in.layout().itemAt(1).widget().setText(f"{tin:,.2f}")
        self.top_tile_adv_out.layout().itemAt(1).widget().setText(f"{tout:,.2f}")

        # 🚀 7. DYNAMIC POPULATION: ADVANCE SETTLEMENT TRACK GRID
        settlement_items = UserActivityService.list_pending_advance_settlements(self.user_id, start_date=start_dt, end_date=end_dt)
        self.settlement_table.setRowCount(len(settlement_items))
        
        for r, item in enumerate(settlement_items):
            self.settlement_table.setItem(r, 0, QTableWidgetItem(str(item["id"])))
            self.settlement_table.setItem(r, 1, QTableWidgetItem(str(item["type"]).upper()))
            self.settlement_table.setItem(r, 2, QTableWidgetItem(str(item["doc_no"])))
            self.settlement_table.setItem(r, 3, QTableWidgetItem(f"₦{item['net_total']:,.2f}"))
            self.settlement_table.setItem(r, 4, QTableWidgetItem(f"₦{item['advance_used']:,.2f}"))
            self.settlement_table.setItem(r, 5, QTableWidgetItem(f"₦{item['balance_due']:,.2f}"))
            
            # Action Button configuration injected into row column index 6
            action_btn = QPushButton("Clear Balance")
            action_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
            action_btn.setCursor(Qt.PointingHandCursor)
            action_btn.setStyleSheet("""
                QPushButton {
                    background-color: #107c41; color: white; border: none; border-radius: 4px; padding: 4px;
                }
                QPushButton:hover { background-color: #0b5931; }
            """)
            
            # Attach operational metadata parameters to the button row object instance
            action_btn.setProperty("doc_type", item["type"])
            action_btn.setProperty("doc_id", item["id"])
            action_btn.setProperty("balance_due", item["balance_due"])
            action_btn.clicked.connect(self._on_settle_action_clicked)
            
            self.settlement_table.setCellWidget(r, 6, action_btn)

    def _on_settle_action_clicked(self):
        """🚀 Interactive action trigger to conclude and wipe pending ledger items"""
        btn = self.sender()
        doc_type = btn.property("doc_type")
        doc_id = btn.property("doc_id")
        balance_due = btn.property("balance_due")

        confirm = QMessageBox.question(
            self, "Confirm Settle", 
            f"Are you sure you want to log a cash payment of ₦{balance_due:,.2f} to complete this {doc_type} balance statement?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            # Commit the financial payment clearance transaction to the DB
            UserActivityService.clear_pending_advance_balance(
                doc_type=doc_type,
                doc_id=doc_id,
                amount=balance_due,
                operator_id=self.user_id
            )
            QMessageBox.information(self, "Success", "Transaction settled completely. Record removed from active tracking lines.")
            self._refresh_ui()  # Instantly reloads view to clear item from list view panel
        except Exception as e:
            QMessageBox.critical(self, "Settlement Error", str(e))

    def _tile(self, title: str, count, amount) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 10, 12, 10)
        t = QLabel(str(title))
        t.setStyleSheet("color: #666; font-weight: 700;")
        v = QLabel(f"{amount:,.2f}" if isinstance(amount, (int, float)) else str(amount))
        v.setStyleSheet("font-size: 18px; font-weight: 900;")
        c = QLabel(f"{count} records" if count != "" and count is not None else "")
        c.setStyleSheet("color: #666;")
        lay.addWidget(t); lay.addWidget(v); lay.addWidget(c)
        return w

    def _tab_table(self, headers):
        widget = QWidget(); lay = QVBoxLayout(widget); lay.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(table.NoEditTriggers)
        table.setSelectionBehavior(table.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(table, 1)
        return {"widget": widget, "table": table}

    def _set_row_meta(self, table: QTableWidget, row: int, meta: dict) -> None:
        it = table.item(row, 0)
        if not it:
            it = QTableWidgetItem(""); table.setItem(row, 0, it)
        it.setData(Qt.UserRole, meta)

    def _open_details_from_table(self, row: int, col: int) -> None:
        table = self.sender()
        it = table.item(row, 0)
        if not it:
            return
            
        meta = it.data(Qt.UserRole) or {}
        kind = str(meta.get("kind") or "").strip().lower()
        entity_id = int(meta.get("id") or 0)

        if not kind or entity_id <= 0:
            return

        dlg = UserActivityDetailsDialog(
            parent=self, 
            actor_user_id=self.user_id, 
            actor_role=self.role, 
            kind=kind, 
            entity_id=entity_id
        )
        dlg.exec_()