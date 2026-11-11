# apps/product_management_app/features/closings/closing_detail_dialog.py
# -*- coding: utf-8 -*-
"""
Professional detailed breakdown dialog for a Customer Closing snapshot.
Presents Summary cards, Purchases tab, Sales tab, Advances tab, and Payments tab,
plus 80mm thermal slip printing.
"""
from __future__ import annotations

from typing import Any, Dict
from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
)

from apps.product_management_app.features.receipts_v2.closing_slip_dialog import open_closing_slip


def _money(val: float) -> str:
    return f"\u20a6{float(val or 0):,.2f}"


class ClosingDetailDialog(QDialog):
    def __init__(self, closing_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.data = closing_data
        self.setWindowTitle(f"Customer Closing - {closing_data.get('closing_no', 'CLS')}")
        self.setMinimumWidth(850)
        self.setMinimumHeight(650)
        self.resize(900, 700)

        main_lay = QVBoxLayout(self)
        main_lay.setSpacing(12)

        # Header info
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(
            "QFrame { background-color: #f1f5f9; border-radius: 8px; } "
            "QLabel { border: none; background: transparent; padding: 0px; }"
        )
        hdr_lay = QVBoxLayout(hdr_frame)
        hdr_lay.setContentsMargins(14, 10, 14, 10)

        top_row = QHBoxLayout()
        name_lbl = QLabel(f"<span style='font-size: 18px; font-weight: bold; color: #0f172a;'>"
                          f"{closing_data.get('party_name', 'Customer')}</span>")
        closing_no_lbl = QLabel(f"<span style='font-size: 16px; font-weight: bold; color: #1e3a8a;'>"
                                f"{closing_data.get('closing_no', '')}</span>")
        top_row.addWidget(name_lbl)
        top_row.addStretch()
        top_row.addWidget(closing_no_lbl)
        hdr_lay.addLayout(top_row)

        meta_row = QHBoxLayout()
        phone = closing_data.get("party_phone") or "No phone"
        period = f"{closing_data.get('period_start', '')} &rarr; {closing_data.get('period_end', '')}"
        closed_by = closing_data.get("created_by") or "System"
        meta_lbl = QLabel(
            f"<span style='color: #475569;'>Phone: <b>{phone}</b> &nbsp;|&nbsp; "
            f"Period: <b>{period}</b> &nbsp;|&nbsp; "
            f"Closed by: <b>{closed_by}</b></span>"
        )
        meta_row.addWidget(meta_lbl)
        hdr_lay.addLayout(meta_row)
        main_lay.addWidget(hdr_frame)

        # Metrics cards bar
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(8)

        open_bal = float(closing_data.get("opening_balance") or 0)
        purch = float(closing_data.get("total_purchases") or 0)
        sales = float(closing_data.get("total_sales") or 0)
        adv_out = float(closing_data.get("total_advance_out") or 0)
        adv_in = float(closing_data.get("total_advance_in") or 0)
        pay_in = float(closing_data.get("total_payments_in") or 0)
        pay_out = float(closing_data.get("total_payments_out") or 0)
        net_chg = float(closing_data.get("net_period_change") or 0)
        close_bal = float(closing_data.get("closing_balance") or 0)

        cards_lay.addWidget(self._make_card("Opening Balance", _money(open_bal), "#334155"))
        cards_lay.addWidget(self._make_card("Purchases (In)", _money(purch), "#059669"))
        cards_lay.addWidget(self._make_card("Sales (Out)", _money(sales), "#d97706"))
        cards_lay.addWidget(self._make_card("Advances (Out/In)", f"{_money(adv_out)} / {_money(adv_in)}", "#7c3aed"))
        cards_lay.addWidget(self._make_card("Payments (In/Out)", f"{_money(pay_in)} / {_money(pay_out)}", "#0284c7"))

        bal_color = "#dc2626" if close_bal > 0.005 else ("#059669" if close_bal < -0.005 else "#475569")
        bal_tag = " (Owes us)" if close_bal > 0.005 else (" (We owe)" if close_bal < -0.005 else " (Settled)")
        cards_lay.addWidget(self._make_card("Closing Balance", _money(close_bal) + bal_tag, bal_color, highlight=True))
        main_lay.addLayout(cards_lay)

        # Tabs for itemized lists
        tabs = QTabWidget()
        details = closing_data.get("details") or {}

        tabs.addTab(self._build_purchases_tab(details.get("purchases") or []), "Purchases (Sayayya)")
        tabs.addTab(self._build_sales_tab(details.get("sales") or []), "Sales (Sayarwa)")
        tabs.addTab(
            self._build_advances_tab((details.get("advance_out") or []) + (details.get("advance_in") or [])),
            "Advances (Kudin Gaba)"
        )
        tabs.addTab(
            self._build_payments_tab((details.get("payments_in") or []) + (details.get("payments_out") or [])),
            "Payments (Biyan Kudi)"
        )
        main_lay.addWidget(tabs, 1)

        # Notes if present
        notes = closing_data.get("notes")
        if notes:
            notes_lbl = QLabel(f"<b>Notes:</b> {notes}")
            notes_lbl.setStyleSheet("color: #475569; font-style: italic; padding: 4px;")
            main_lay.addWidget(notes_lbl)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_print = QPushButton("Print Thermal Slip (80mm)")
        btn_print.setStyleSheet(
            "background-color: #1e3a8a; color: white; font-weight: bold; padding: 8px 18px; border-radius: 4px;"
        )
        btn_print.clicked.connect(self._print_slip)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(btn_print)
        main_lay.addLayout(btn_row)

    def _make_card(self, title: str, value: str, color: str, highlight: bool = False) -> QFrame:
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
        v_lbl = QLabel(f"<span style='font-size: 13px; font-weight: bold; color: {color};'>{value}</span>")
        t_lbl.setAlignment(Qt.AlignCenter)
        v_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(t_lbl)
        lay.addWidget(v_lbl)
        return card

    def _build_purchases_tab(self, items: list) -> QTableWidget:
        table = QTableWidget(len(items), 6)
        table.setHorizontalHeaderLabels(["Reference", "Date", "Summary", "Total Amount", "Paid", "Balance"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for row, it in enumerate(items):
            table.setItem(row, 0, QTableWidgetItem(str(it.get("ref", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(it.get("date", ""))))
            table.setItem(row, 2, QTableWidgetItem(str(it.get("summary", ""))))
            table.setItem(row, 3, QTableWidgetItem(_money(it.get("total_amount", 0))))
            table.setItem(row, 4, QTableWidgetItem(_money(it.get("settled", 0))))
            table.setItem(row, 5, QTableWidgetItem(_money(it.get("balance", 0))))
        return table

    def _build_sales_tab(self, items: list) -> QTableWidget:
        table = QTableWidget(len(items), 6)
        table.setHorizontalHeaderLabels(["Reference", "Date", "Summary", "Total Amount", "Paid", "Balance"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for row, it in enumerate(items):
            table.setItem(row, 0, QTableWidgetItem(str(it.get("ref", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(it.get("date", ""))))
            table.setItem(row, 2, QTableWidgetItem(str(it.get("summary", ""))))
            table.setItem(row, 3, QTableWidgetItem(_money(it.get("total_amount", 0))))
            table.setItem(row, 4, QTableWidgetItem(_money(it.get("settled", 0))))
            table.setItem(row, 5, QTableWidgetItem(_money(it.get("balance", 0))))
        return table

    def _build_advances_tab(self, items: list) -> QTableWidget:
        table = QTableWidget(len(items), 5)
        table.setHorizontalHeaderLabels(["Reference", "Direction", "Date", "Amount", "Notes"])
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for row, it in enumerate(items):
            table.setItem(row, 0, QTableWidgetItem(str(it.get("ref", ""))))
            dir_str = "OUT (Given)" if it.get("direction") == "given" else "IN (Received)"
            table.setItem(row, 1, QTableWidgetItem(dir_str))
            table.setItem(row, 2, QTableWidgetItem(str(it.get("date", ""))))
            table.setItem(row, 3, QTableWidgetItem(_money(it.get("amount", 0))))
            table.setItem(row, 4, QTableWidgetItem(str(it.get("notes", ""))))
        return table

    def _build_payments_tab(self, items: list) -> QTableWidget:
        table = QTableWidget(len(items), 6)
        table.setHorizontalHeaderLabels(["Reference", "Direction", "Date", "Method", "Amount", "Notes"])
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        for row, it in enumerate(items):
            table.setItem(row, 0, QTableWidgetItem(str(it.get("ref", ""))))
            dir_str = "IN (Received)" if it.get("direction") == "in" else "OUT (Paid)"
            table.setItem(row, 1, QTableWidgetItem(dir_str))
            table.setItem(row, 2, QTableWidgetItem(str(it.get("date", ""))))
            table.setItem(row, 3, QTableWidgetItem(str(it.get("method", ""))))
            table.setItem(row, 4, QTableWidgetItem(_money(it.get("amount", 0))))
            table.setItem(row, 5, QTableWidgetItem(str(it.get("notes", ""))))
        return table

    def _print_slip(self):
        open_closing_slip(self, self.data)
