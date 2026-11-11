# apps/product_management_app/features/closings/views/customer_closings_view.py
# -*- coding: utf-8 -*-
"""
Admin Workspace View: Customer Closings.
Displays metrics, batch evening auto-closing round-up, search and filter,
and complete itemized audit history for all customer closing periods.
"""
from __future__ import annotations

from typing import Optional
from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QMessageBox,
)

from database.services.closing_service import ClosingService
from apps.product_management_app.features.closings.closing_detail_dialog import ClosingDetailDialog
from apps.product_management_app.features.receipts_v2.closing_slip_dialog import open_closing_slip


def _money(val: float) -> str:
    return f"\u20a6{float(val or 0):,.2f}"


class CustomerClosingsView(QWidget):
    def __init__(self, session_user_id: int, parent=None):
        super().__init__(parent)
        self.session_user_id = int(session_user_id)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(16, 16, 16, 16)
        main_lay.setSpacing(14)

        # Header
        top_bar = QHBoxLayout()
        header_text = QVBoxLayout()
        title = QLabel("<span style='font-size: 20px; font-weight: bold; color: #0f172a;'>"
                       "Customer Closings &bull; Rufe Lissafin Abokan Ciniki</span>")
        subtitle = QLabel("<span style='color: #64748b; font-size: 13px;'>"
                          "Audit periodic balances, net calculations (Purchases : Sales : Advances : Payments), "
                          "and official closing statements.</span>")
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        top_bar.addLayout(header_text)
        top_bar.addStretch()

        self.btn_auto_close = QPushButton("Batch Close Active for Today (Round-up)")
        self.btn_auto_close.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 8px 16px; border-radius: 4px;"
        )
        self.btn_auto_close.clicked.connect(self._on_batch_close)
        top_bar.addWidget(self.btn_auto_close)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setStyleSheet("padding: 8px 14px; border-radius: 4px;")
        btn_refresh.clicked.connect(self.load_data)
        top_bar.addWidget(btn_refresh)

        main_lay.addLayout(top_bar)

        # Metrics cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self.card_total_count = self._make_card("Total Closings", "0", "#0f172a")
        self.card_purch_sum = self._make_card("Total Purchases Settled", "\u20a60.00", "#059669")
        self.card_sales_sum = self._make_card("Total Sales Settled", "\u20a60.00", "#d97706")
        self.card_net_sum = self._make_card("Net Total Closing Balance", "\u20a60.00", "#1e3a8a", highlight=True)

        cards_row.addWidget(self.card_total_count)
        cards_row.addWidget(self.card_purch_sum)
        cards_row.addWidget(self.card_sales_sum)
        cards_row.addWidget(self.card_net_sum)
        main_lay.addLayout(cards_row)

        # Search bar
        search_box = QHBoxLayout()
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search by customer name, phone number, or closing number (e.g. CLS-00001)...")
        self.txt_search.setStyleSheet("padding: 7px 10px; border: 1px solid #cbd5e1; border-radius: 4px;")
        self.txt_search.textChanged.connect(self.load_data)
        search_box.addWidget(self.txt_search)
        main_lay.addLayout(search_box)

        # Table
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Closing #", "Customer Name", "Phone", "Closed Date", "Period",
            "Purchases", "Sales", "Advances (Out/In)", "Payments (In/Out)", "Closing Balance", "Actions"
        ])
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "Closing #", "Customer Name", "Phone", "Closed Date", "Period",
            "Purchases", "Sales", "Advances (Out/In)", "Payments (In/Out)", "Closing Balance", "Actions"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { background-color: white; border: 1px solid #e2e8f0; }")
        self.table.cellDoubleClicked.connect(self._on_table_double_click)
        main_lay.addWidget(self.table, 1)

        self.load_data()

    def _make_card(self, title: str, val: str, color: str, highlight: bool = False) -> QFrame:
        card = QFrame()
        bg = "#eff6ff" if highlight else "#ffffff"
        bd = color if highlight else "#cbd5e1"
        card.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1.5px solid {bd}; border-radius: 6px; }} "
            f"QLabel {{ border: none; background: transparent; padding: 0px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 6)
        t_lbl = QLabel(f"<span style='font-size: 11px; color: #64748b;'>{title}</span>")
        v_lbl = QLabel(f"<span style='font-size: 15px; font-weight: bold; color: {color};'>{val}</span>")
        t_lbl.setAlignment(Qt.AlignCenter)
        v_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(t_lbl)
        lay.addWidget(v_lbl)
        card.v_lbl = v_lbl
        return card

    def load_data(self):
        search = self.txt_search.text().strip()
        rows = ClosingService.list_closings(search=search, limit=300)

        # Update metrics
        tot_count = len(rows)
        sum_purch = sum(r.get("total_purchases", 0) for r in rows)
        sum_sales = sum(r.get("total_sales", 0) for r in rows)
        sum_bal = sum(r.get("closing_balance", 0) for r in rows)

        self.card_total_count.v_lbl.setText(f"<span style='font-size: 15px; font-weight: bold;'>{tot_count}</span>")
        self.card_purch_sum.v_lbl.setText(f"<span style='font-size: 15px; font-weight: bold; color: #059669;'>{_money(sum_purch)}</span>")
        self.card_sales_sum.v_lbl.setText(f"<span style='font-size: 15px; font-weight: bold; color: #d97706;'>{_money(sum_sales)}</span>")
        bal_color = "#dc2626" if sum_bal > 0.005 else ("#059669" if sum_bal < -0.005 else "#1e3a8a")
        self.card_net_sum.v_lbl.setText(f"<span style='font-size: 15px; font-weight: bold; color: {bal_color};'>{_money(sum_bal)}</span>")

        # Update table
        self.table.setRowCount(len(rows))
        for row, c in enumerate(rows):
            cid = c.get("id")
            cno = c.get("closing_no", "")
            self.table.setItem(row, 0, QTableWidgetItem(cno))
            self.table.setItem(row, 1, QTableWidgetItem(c.get("party_name", "")))
            self.table.setItem(row, 2, QTableWidgetItem(c.get("party_phone", "")))
            self.table.setItem(row, 3, QTableWidgetItem(c.get("created_at", "")))
            p_range = f"{c.get('period_start', '')[:10]} &rarr; {c.get('period_end', '')[:10]}"
            self.table.setItem(row, 4, QTableWidgetItem(p_range))
            self.table.setItem(row, 5, QTableWidgetItem(_money(c.get("total_purchases", 0))))
            self.table.setItem(row, 6, QTableWidgetItem(_money(c.get("total_sales", 0))))
            adv_txt = f"{_money(c.get('total_advance_out', 0))} / {_money(c.get('total_advance_in', 0))}"
            self.table.setItem(row, 7, QTableWidgetItem(adv_txt))
            pay_txt = f"{_money(c.get('total_payments_in', 0))} / {_money(c.get('total_payments_out', 0))}"
            self.table.setItem(row, 8, QTableWidgetItem(pay_txt))

            c_bal = float(c.get("closing_balance") or 0)
            bal_it = QTableWidgetItem(_money(c_bal))
            if c_bal > 0.005:
                bal_it.setForeground(Qt.red)
            elif c_bal < -0.005:
                bal_it.setForeground(Qt.darkGreen)
            self.table.setItem(row, 9, bal_it)

            # Action buttons
            act_widget = QWidget()
            act_lay = QHBoxLayout(act_widget)
            act_lay.setContentsMargins(2, 2, 2, 2)
            act_lay.setSpacing(4)

            btn_view = QPushButton("Details")
            btn_view.setStyleSheet("font-size: 11px; padding: 2px 6px;")
            btn_view.clicked.connect(lambda checked=False, x=cid: self._on_view_details(x))

            btn_print = QPushButton("Slip")
            btn_print.setStyleSheet("font-size: 11px; padding: 2px 6px; background-color: #1e3a8a; color: white;")
            btn_print.clicked.connect(lambda checked=False, x=cid: self._on_print_slip(x))

            act_lay.addWidget(btn_view)
            act_lay.addWidget(btn_print)
            self.table.setCellWidget(row, 10, act_widget)

    def _on_table_double_click(self, row: int, col: int):
        cno_item = self.table.item(row, 0)
        if not cno_item:
            return
        cno = cno_item.text()
        rows = ClosingService.list_closings(search=cno, limit=1)
        if rows:
            self._on_view_details(rows[0]["id"])

    def _on_view_details(self, closing_id: int):
        data = ClosingService.get_closing_by_id(closing_id)
        if not data:
            QMessageBox.warning(self, "Not Found", "Could not load closing details.")
            return
        dlg = ClosingDetailDialog(data, parent=self)
        dlg.exec_()

    def _on_print_slip(self, closing_id: int):
        data = ClosingService.get_closing_by_id(closing_id)
        if not data:
            QMessageBox.warning(self, "Not Found", "Could not load closing data for printing.")
            return
        open_closing_slip(self, data)

    def _on_batch_close(self):
        box = QMessageBox(self)
        box.setWindowTitle("Batch Closing Round-up")
        box.setIcon(QMessageBox.Question)
        box.setText(
            "<b>Batch Close Active Customer Accounts for Today?</b><br><br>"
            "This will calculate and record an official closing period for every customer "
            "who has had transactions (purchases, sales, advances, payments) since their last closing.<br><br>"
            "Proceed with batch round-up?"
        )
        b_ok = box.addButton("Proceed & Close", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec_()

        if box.clickedButton() is b_ok:
            closed = ClosingService.auto_close_active_customers_for_today(self.session_user_id)
            QMessageBox.information(
                self,
                "Batch Closing Complete",
                f"Successfully closed {len(closed)} active customer account(s) for today."
            )
            self.load_data()
