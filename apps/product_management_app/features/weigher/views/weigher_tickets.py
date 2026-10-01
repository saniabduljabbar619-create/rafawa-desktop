# apps/product_management_app/features/weigher/views/weigher_tickets.py
"""The weigher's own tickets: what they weighed, where each one stands, reprint a slip. No prices."""
from __future__ import annotations

from PySide2.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from database.services.weigh_service import WeighService
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.weigher.views.ticket_table import (
    fill_ticket_table, make_ticket_table, selected_ticket_id,
)

FILTERS = [("all", "All"), ("open", "Weighing"), ("submitted", "Awaiting cashier"), ("finalized", "Finalized")]


class WeigherTicketsView(QWidget):
    def __init__(self):
        super().__init__()
        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("My Tickets")
        title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        root.addLayout(titles)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_open = KpiCard("Still weighing", "0", "unfinished tickets", "amber")
        self.kpi_wait = KpiCard("Awaiting cashier", "0", "submitted, not yet priced", "blue")
        self.kpi_done = KpiCard("Finalized", "0", "paid into the books", "green")
        for k in (self.kpi_open, self.kpi_wait, self.kpi_done):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.filter_seg = Segmented(FILTERS)
        bar.addWidget(self.filter_seg)
        bar.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_print = QPushButton("Print Slip")
        bar.addWidget(self.btn_print)
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.table = make_ticket_table(show_amount=False, show_weigher=False)
        root.addWidget(self.table, 1)

        self.filter_seg.changed.connect(lambda _k: self.refresh())
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_print.clicked.connect(self.print_selected)
        self.refresh()

    def refresh(self):
        try:
            allrows = WeighService.list_tickets()
        except Exception as e:
            QMessageBox.warning(self, "My Tickets", str(e))
            return
        count = lambda s: sum(1 for t in allrows if t["status"] == s)       # noqa: E731
        self.kpi_open.set(str(count("open")))
        self.kpi_wait.set(str(count("submitted")))
        self.kpi_done.set(str(count("finalized")))
        key = self.filter_seg.key()
        rows = allrows if key == "all" else [t for t in allrows if t["status"] == key]
        self.subtitle.setText(f"{len(rows)} of {len(allrows)} tickets")
        fill_ticket_table(self.table, rows)

    def print_selected(self):
        tid = selected_ticket_id(self.table)
        if not tid:
            QMessageBox.information(self, "Print Slip", "Select a ticket first.")
            return
        try:
            t = WeighService.get_ticket(tid)
            if t["status"] == "open":
                QMessageBox.information(self, "Print Slip", "Finish the weighing first (Weighing Station → Resume).")
                return
            from apps.product_management_app.features.receipts_v2 import get_shop, receipt_print, render_weigh_slip
            receipt_print.print_text(self, render_weigh_slip(t, get_shop(), t["receipt_type"]),
                                     window_title=f"Weighing Slip - {t['ticket_no']}")
        except Exception as e:
            QMessageBox.warning(self, "Print Slip", str(e))
