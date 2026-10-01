# apps/product_management_app/features/weigher/views/weigh_tickets_admin.py
"""Weigh Tickets - the queue admin/cashier work from: Open Intake → verify → price → finalize → pay."""
from __future__ import annotations

from PySide2.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from database.services.weigh_service import WeighService
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
from apps.product_management_app.features.weigher.views.ticket_table import (
    fill_ticket_table, make_ticket_table, selected_ticket_id,
)

FILTERS = [("submitted", "Awaiting intake"), ("finalized", "Finalized"), ("open", "Being weighed"),
           ("cancelled", "Cancelled"), ("all", "All")]


class WeighTicketsAdminView(QWidget):
    def __init__(self, mode: str = "admin"):
        super().__init__()
        self.mode = mode
        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Weigh Tickets")
        title.setObjectName("Title")
        self.subtitle = QLabel("Weighed deliveries waiting for you to verify, price and pay")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        self.btn_open = QPushButton("Open Intake")
        self.btn_open.setObjectName("PrimaryButton")
        self.btn_open.setMinimumHeight(36)
        head.addWidget(self.btn_open)
        root.addLayout(head)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_wait = KpiCard("Awaiting intake", "0", "submitted by weighers", "blue")
        self.kpi_open = KpiCard("Being weighed", "0", "not yet submitted", "amber")
        self.kpi_done = KpiCard("Finalized", "0", "turned into purchases", "green")
        for k in (self.kpi_wait, self.kpi_open, self.kpi_done):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.filter_seg = Segmented(FILTERS)
        bar.addWidget(self.filter_seg)
        bar.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.table = make_ticket_table(show_amount=True, show_weigher=True)
        root.addWidget(self.table, 1)
        hint = QLabel("Double-click a ticket to open it  -  the weigher records bags and weights; "
                      "price, payment and finalizing are done here.")
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        root.addWidget(hint)

        self.filter_seg.changed.connect(lambda _k: self.refresh())
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_open.clicked.connect(self.open_selected)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.open_selected())
        self.refresh()

    def refresh(self):
        try:
            allrows = WeighService.list_tickets()
        except Exception as e:
            QMessageBox.warning(self, "Weigh Tickets", str(e))
            return
        count = lambda s: sum(1 for t in allrows if t["status"] == s)       # noqa: E731
        self.kpi_wait.set(str(count("submitted")))
        self.kpi_open.set(str(count("open")))
        self.kpi_done.set(str(count("finalized")))
        key = self.filter_seg.key()
        rows = allrows if key == "all" else [t for t in allrows if t["status"] == key]
        fill_ticket_table(self.table, rows)
        if self.table.rowCount():
            self.table.selectRow(0)

    def open_selected(self):
        tid = selected_ticket_id(self.table)
        if not tid:
            QMessageBox.information(self, "Open Intake", "Select a ticket first.")
            return
        dlg = WeighTicketReviewDialog(tid, parent=self)
        dlg.exec_()
        self.refresh()
