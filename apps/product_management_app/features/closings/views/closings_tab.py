# apps/product_management_app/features/closings/views/closings_tab.py
# -*- coding: utf-8 -*-
"""
Customer Profile Tab: live unclosed period calculation, record closing button,
and historical closings audit table with details & print slip actions.
"""
from __future__ import annotations

from typing import Optional
from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QMessageBox,
)

from database.services.closing_service import ClosingService
from apps.product_management_app.features.closings.closing_detail_dialog import ClosingDetailDialog
from apps.product_management_app.features.closings.new_closing_dialog import NewClosingDialog
from apps.product_management_app.features.receipts_v2.closing_slip_dialog import open_closing_slip


def _money(val: float) -> str:
    return f"\u20a6{float(val or 0):,.2f}"


class ClosingsTab(QWidget):
    def __init__(self, party_id: int, session_user_id: int, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.session_user_id = int(session_user_id)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(12, 12, 12, 12)
        main_lay.setSpacing(12)

        # -------------------------------------------------------------
        # 1. Top Section: Live Unclosed Period Preview
        # -------------------------------------------------------------
        preview_box = QFrame()
        preview_box.setStyleSheet(
            "QFrame { background-color: #f8fafc; border: 1.5px solid #cbd5e1; border-radius: 8px; } "
            "QLabel { border: none; background: transparent; padding: 0px; }"
        )
        p_lay = QVBoxLayout(preview_box)
        p_lay.setContentsMargins(12, 10, 12, 10)

        title_row = QHBoxLayout()
        self.lbl_period_info = QLabel("<b>Current Period Activity (Unclosed)</b>")
        self.lbl_period_info.setStyleSheet("font-size: 14px; color: #0f172a;")

        self.btn_close_now = QPushButton("Record Closing Now (Rufe Lissafin Lokaci)")
        self.btn_close_now.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_close_now.clicked.connect(self._on_record_closing)

        title_row.addWidget(self.lbl_period_info)
        title_row.addStretch()
        title_row.addWidget(self.btn_close_now)
        p_lay.addLayout(title_row)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)

        self.card_open = self._make_card("Opening Balance", "\u20a60.00", "#475569")
        self.card_purch = self._make_card("Purchases (In)", "\u20a60.00", "#059669")
        self.card_sales = self._make_card("Sales (Out)", "\u20a60.00", "#d97706")
        self.card_adv = self._make_card("Advances (Out/In)", "\u20a60 / \u20a60", "#7c3aed")
        self.card_pay = self._make_card("Payments (In/Out)", "\u20a60 / \u20a60", "#0284c7")
        self.card_close = self._make_card("Projected Closing", "\u20a60.00", "#dc2626", highlight=True)

        cards_row.addWidget(self.card_open)
        cards_row.addWidget(self.card_purch)
        cards_row.addWidget(self.card_sales)
        cards_row.addWidget(self.card_adv)
        cards_row.addWidget(self.card_pay)
        cards_row.addWidget(self.card_close)
        p_lay.addLayout(cards_row)

        main_lay.addWidget(preview_box)

        # -------------------------------------------------------------
        # 2. Bottom Section: Historical Closings Table
        # -------------------------------------------------------------
        hist_title = QLabel("<b>Closings History / Tarihin Rufewar Lissafi</b>")
        hist_title.setStyleSheet("font-size: 13px; color: #334155; margin-top: 4px;")
        main_lay.addWidget(hist_title)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Closing #", "Closed Date", "Period", "Purchases", "Sales",
            "Advances (Out/In)", "Payments (In/Out)", "Closing Balance", "Actions"
        ])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { background-color: white; border: 1px solid #e2e8f0; }")
        main_lay.addWidget(self.table, 1)

        self.refresh_data()

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
        v_lbl = QLabel(f"<span style='font-size: 12px; font-weight: bold; color: {color};'>{val}</span>")
        t_lbl.setAlignment(Qt.AlignCenter)
        v_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(t_lbl)
        lay.addWidget(v_lbl)
        card.t_lbl = t_lbl
        card.v_lbl = v_lbl
        return card

    def _update_card(self, card: QFrame, val: str, color: Optional[str] = None):
        if color:
            card.v_lbl.setText(f"<span style='font-size: 12px; font-weight: bold; color: {color};'>{val}</span>")
        else:
            card.v_lbl.setText(f"<span style='font-size: 12px; font-weight: bold;'>{val}</span>")

    def refresh_data(self):
        # 1. Update live preview
        try:
            live = ClosingService.compute_closing(self.party_id)
            p_start = live.get("period_start")
            p_str = p_start.strftime("%Y-%m-%d %H:%M") if hasattr(p_start, "strftime") else str(p_start)[:16]
            self.lbl_period_info.setText(
                f"<b>Current Period (Unclosed)</b> &nbsp;|&nbsp; "
                f"<span style='font-size: 12px; color: #475569;'>Period since: {p_str} &rarr; Now "
                f"({live.get('activity_count', 0)} activities)</span>"
            )

            self._update_card(self.card_open, _money(live.get("opening_balance", 0)))
            self._update_card(self.card_purch, _money(live.get("total_purchases", 0)))
            self._update_card(self.card_sales, _money(live.get("total_sales", 0)))
            adv_str = f"{_money(live.get('total_advance_out', 0))} / {_money(live.get('total_advance_in', 0))}"
            self._update_card(self.card_adv, adv_str)
            pay_str = f"{_money(live.get('total_payments_in', 0))} / {_money(live.get('total_payments_out', 0))}"
            self._update_card(self.card_pay, pay_str)

            close_bal = live.get("closing_balance", 0)
            bal_color = "#dc2626" if close_bal > 0.005 else ("#059669" if close_bal < -0.005 else "#475569")
            tag = " (Owes)" if close_bal > 0.005 else (" (We owe)" if close_bal < -0.005 else " (0)")
            self._update_card(self.card_close, _money(close_bal) + tag, color=bal_color)
        except Exception:
            pass

        # 2. Update historical table
        try:
            closings = ClosingService.list_closings(party_id=self.party_id, limit=200)
            self.table.setRowCount(len(closings))
            for row, c in enumerate(closings):
                cid = c.get("id")
                cno = c.get("closing_no", "")
                self.table.setItem(row, 0, QTableWidgetItem(cno))
                self.table.setItem(row, 1, QTableWidgetItem(c.get("created_at", "")))
                p_range = f"{c.get('period_start', '')[:10]} &rarr; {c.get('period_end', '')[:10]}"
                self.table.setItem(row, 2, QTableWidgetItem(p_range))
                self.table.setItem(row, 3, QTableWidgetItem(_money(c.get("total_purchases", 0))))
                self.table.setItem(row, 4, QTableWidgetItem(_money(c.get("total_sales", 0))))
                adv_txt = f"{_money(c.get('total_advance_out', 0))} / {_money(c.get('total_advance_in', 0))}"
                self.table.setItem(row, 5, QTableWidgetItem(adv_txt))
                pay_txt = f"{_money(c.get('total_payments_in', 0))} / {_money(c.get('total_payments_out', 0))}"
                self.table.setItem(row, 6, QTableWidgetItem(pay_txt))

                c_bal = float(c.get("closing_balance") or 0)
                bal_it = QTableWidgetItem(_money(c_bal))
                if c_bal > 0.005:
                    bal_it.setForeground(Qt.red)
                elif c_bal < -0.005:
                    bal_it.setForeground(Qt.darkGreen)
                self.table.setItem(row, 7, bal_it)

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
                self.table.setCellWidget(row, 8, act_widget)
        except Exception:
            pass

    def _on_record_closing(self):
        dlg = NewClosingDialog(self.party_id, self.session_user_id, parent=self)
        if dlg.exec_() == NewClosingDialog.Accepted:
            self.refresh_data()

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
