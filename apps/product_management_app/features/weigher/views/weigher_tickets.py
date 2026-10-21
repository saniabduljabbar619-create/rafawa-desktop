# apps/product_management_app/features/weigher/views/weigher_tickets.py
"""The weigher's Finalize List: every weighing they did, where each stands - edit, remove or reprint it."""
from __future__ import annotations

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from database.services.weigh_service import WeighService, day_start
from shared.uix.layout.scrollable import make_scrollable_page
from shared.uix.widgets.cards import KpiCard, Segmented
from apps.product_management_app.features.weigher.views.ticket_table import (
    fill_ticket_table, make_ticket_table, selected_ticket_id,
)

FILTERS = [("all", "All"), ("open", "Weighing"), ("submitted", "Awaiting cashier"), ("finalized", "Finalized"),
           ("cancelled", "Removed")]
EDITABLE = ("open", "submitted")           # until the cashier finalizes it


class WeigherTicketsView(QWidget):
    def __init__(self):
        super().__init__()
        root = make_scrollable_page(self, margins=(16, 14, 16, 14))
        root.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Finalize List")
        title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color:#6b7280; font-size:12px;")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        root.addLayout(titles)

        kp = QHBoxLayout()
        kp.setSpacing(12)
        self.kpi_open = KpiCard("Still weighing", "0", "unfinished tickets", "amber")
        self.kpi_wait = KpiCard("Awaiting cashier", "0", "submitted, can still be edited", "blue")
        self.kpi_done = KpiCard("Finalized", "0", "paid into the books", "green")
        for k in (self.kpi_open, self.kpi_wait, self.kpi_done):
            kp.addWidget(k, 1)
        root.addLayout(kp)

        bar = QHBoxLayout()
        self.filter_seg = Segmented(FILTERS)
        bar.addWidget(self.filter_seg)
        self.chk_earlier = QCheckBox("Include earlier days")
        self.chk_earlier.setToolTip("The list starts from zero each day. Tick this to see weighings from earlier days too.")
        bar.addWidget(self.chk_earlier)
        bar.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_print = QPushButton("Print Weighing Receipt")
        self.btn_print_value = QPushButton("Print Value Receipt")
        self.btn_print_pay = QPushButton("Print Payment Receipt")
        self.btn_edit = QPushButton("Edit")
        self.btn_edit.setObjectName("PrimaryButton")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setProperty("role", "danger")
        self.btn_move = QPushButton("Change customer")
        self.btn_move.setToolTip("Weighed for the wrong person? Move the whole weighing to the right one")
        for b in (self.btn_edit, self.btn_move, self.btn_remove, self.btn_print, self.btn_print_value, self.btn_print_pay, self.btn_refresh):
            bar.addWidget(b)
        root.addLayout(bar)

        self.table = make_ticket_table(show_amount=True, show_weigher=False)
        root.addWidget(self.table, 1)

        self.filter_seg.changed.connect(lambda _k: self.refresh())
        self.chk_earlier.toggled.connect(lambda _c: self.refresh())
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_print.clicked.connect(lambda: self.print_selected("weighing"))
        self.btn_print_value.clicked.connect(lambda: self.print_selected("value"))
        self.btn_print_pay.clicked.connect(lambda: self.print_selected("payment"))
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_move.clicked.connect(self.move_selected)
        self.table.cellDoubleClicked.connect(lambda *_a: self.edit_selected())
        self.table.itemSelectionChanged.connect(self._buttons)
        self.refresh()

        # at midnight the list starts again from zero (nothing is deleted - admin / cashier still see everything)
        self._day = day_start().date()
        self._midnight = QTimer(self)
        self._midnight.timeout.connect(self._check_new_day)
        self._midnight.start(30000)

    def _check_new_day(self):
        today = day_start().date()
        if today != self._day:
            self._day = today
            self.refresh()

    def refresh(self):
        try:
            allrows = WeighService.list_tickets(since=None if self.chk_earlier.isChecked() else day_start())
        except Exception as e:
            QMessageBox.warning(self, "Finalize List", str(e))
            return
        count = lambda s: sum(1 for t in allrows if t["status"] == s)       # noqa: E731
        self.kpi_open.set(str(count("open")))
        self.kpi_wait.set(str(count("submitted")))
        self.kpi_done.set(str(count("finalized")))
        key = self.filter_seg.key()
        rows = allrows if key == "all" else [t for t in allrows if t["status"] == key]
        self.subtitle.setText(f"{len(rows)} of {len(allrows)} tickets"
                              + ("" if self.chk_earlier.isChecked() else " today"))
        self._rows = {int(t["id"]): t for t in rows}
        fill_ticket_table(self.table, rows)
        self._buttons()

    def _selected(self):
        tid = selected_ticket_id(self.table)
        return (tid, self._rows.get(tid)) if tid else (None, None)

    def _buttons(self):
        _tid, t = self._selected()
        ok = bool(t and t["status"] in EDITABLE)
        self.btn_edit.setEnabled(ok)
        self.btn_remove.setEnabled(ok)
        self.btn_move.setEnabled(ok)
        printable = bool(t and t["status"] != "open")
        self.btn_print.setEnabled(printable)
        self.btn_print_value.setEnabled(printable)
        self.btn_print_pay.setEnabled(printable)

    def edit_selected(self):
        """Correct a weighing: its rounds and prices (a finalized one opens read-only)."""
        tid, t = self._selected()
        if not tid:
            QMessageBox.information(self, "Edit", "Select a weighing first.")
            return
        from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import (
            WeighTicketReviewDialog)
        WeighTicketReviewDialog(tid, parent=self, mode="weigher").exec_()
        self.refresh()

    def move_selected(self):
        """Weighed for the wrong person: move the whole weighing to someone else."""
        tid, t = self._selected()
        if not tid:
            QMessageBox.information(self, "Change customer", "Select a weighing first.")
            return
        from apps.product_management_app.features.weigher.dialogs.reassign_dialog import move_weighing
        if move_weighing(self, tid):
            self.refresh()

    def remove_selected(self):
        tid, t = self._selected()
        if not tid:
            QMessageBox.information(self, "Remove", "Select a weighing first.")
            return
        if t["status"] not in EDITABLE:
            QMessageBox.information(self, "Remove", f"This ticket is {t['status']} and can no longer be removed.")
            return
        ok = QMessageBox.question(
            self, "Remove Weighing",
            f"Remove {t['ticket_no']} ({t['party_name']}, {t['total_kg']:,.2f} kg)?\n\n"
            "It is cancelled and kept in the history (filter: Removed); nothing is booked.",
            QMessageBox.Yes | QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        try:
            WeighService.cancel_ticket(tid, "Removed by the weigher")
        except Exception as e:
            QMessageBox.warning(self, "Remove", str(e))
        self.refresh()

    def print_selected(self, option: str = "weighing"):
        tid = selected_ticket_id(self.table)
        if not tid:
            QMessageBox.information(self, "Print Receipt", "Select a ticket first.")
            return
        try:
            t = WeighService.get_ticket(tid)
            if t["status"] == "open":
                QMessageBox.information(self, "Print Receipt", "Finish the weighing first (Weighing Station → Resume).")
                return
            from apps.product_management_app.features.receipts_v2 import get_shop, receipt_print, render_weigh_slip
            receipt_print.print_text(self, render_weigh_slip(t, get_shop(), option),
                                     window_title=f"Weighing Receipt - {t['ticket_no']}")
        except Exception as e:
            QMessageBox.warning(self, "Print Receipt", str(e))
