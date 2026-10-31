# apps/product_management_app/features/weigher/views/ticket_table.py
"""Shared look for the weigh-ticket lists (weigher history and the cashier/admin queue)."""
from __future__ import annotations

from PySide2.QtCore import QDate, QDateTime, Qt, QTime, QTimer, Signal
from PySide2.QtWidgets import (
    QAbstractItemView, QCheckBox, QDateTimeEdit, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QWidget,
)

from shared.uix.widgets.cards import chip, style_table

STATUS_TONE = {"open": "amber", "submitted": "blue", "finalized": "green", "cancelled": "grey"}
STATUS_TEXT = {"open": "Weighing", "submitted": "Awaiting cashier", "finalized": "Finalized", "cancelled": "Cancelled"}


def fmt_bags(v) -> str:
    v = float(v or 0)
    return f"{int(v)}" if v == int(v) else f"{v:g}"


def make_ticket_table(show_amount: bool, show_weigher: bool, checkable: bool = False) -> QTableWidget:
    """checkable: a tick-box column first (the walk-in list: tick the weighings to pay together)."""
    cols = ["Ticket", "Date", "Party", "Products", "Bags", "Weight (kg)"]
    if show_weigher:
        cols.append("Weigher")
    if show_amount:
        cols.append("Amount")
    cols.append("Status")
    if checkable:
        cols.insert(0, "")
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    style_table(t, 48)
    t.horizontalHeader().setSectionResizeMode(len(cols) - 1, QHeaderView.Fixed)
    t.setColumnWidth(len(cols) - 1, 170)                  # room for the status chip
    if checkable:
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        t.setColumnWidth(0, 44)
    t.setProperty("show_amount", show_amount)
    t.setProperty("show_weigher", show_weigher)
    t.setProperty("checkable", checkable)
    return t


def fill_ticket_table(table: QTableWidget, rows) -> None:
    show_amount = bool(table.property("show_amount"))
    show_weigher = bool(table.property("show_weigher"))
    off = 1 if table.property("checkable") else 0        # columns taken by the tick box
    table.setUpdatesEnabled(False)                       # one repaint at the end, not one per cell
    table.clearContents()
    table.setRowCount(len(rows))
    for r, t in enumerate(rows):
        when = t.get("submitted_at") or t.get("created_at")
        vals = [t["ticket_no"], when.strftime("%Y-%m-%d %H:%M") if when else "",
                f"{t['party_name']}{' [' + t['party_code'] + ']' if t.get('party_code') else ''}  ({t['party_role']})", t.get("products", ""),
                fmt_bags(t["total_bags"]), f"{t['total_kg']:,.2f}"]
        if show_weigher:
            vals.append(t.get("weigher_name", ""))
        if show_amount:
            amt = t.get("total_amount")
            vals.append(f"{amt:,.2f}" if amt else "-")
        if off:
            box = QTableWidgetItem()
            if t["status"] == "submitted":
                box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                box.setCheckState(Qt.Unchecked)
            else:
                box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)      # only awaiting-intake ones can be paid
            box.setData(Qt.UserRole, int(t["id"]))
            table.setItem(r, 0, box)
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setData(Qt.UserRole, int(t["id"]))
            if c in (4, 5) or (show_amount and c == len(vals) - 1):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(r, c + off, it)
        # status chip in the last column
        table.setItem(r, len(vals) + off, QTableWidgetItem(""))
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.addWidget(chip(STATUS_TEXT.get(t["status"], t["status"]), STATUS_TONE.get(t["status"], "grey")),
                      0, Qt.AlignVCenter)
        lay.addStretch(1)
        cell.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        table.setCellWidget(r, len(vals) + off, cell)
        table.item(r, len(vals) + off).setData(Qt.UserRole, int(t["id"]))
    table.setUpdatesEnabled(True)


class TicketFilterBar(QWidget):
    """Search and a date / time range for the weighing lists. `changed` fires when either is edited (typing waits a
    moment); values() gives {search, date_from, date_to} ready for WeighService.list_tickets."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search ticket no, name, phone, walk-in ID or product")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(32)
        lay.addWidget(self.search, 1)
        self.chk_range = QCheckBox("Date and time")
        self.chk_range.setToolTip("Only weighings between these two moments")
        lay.addWidget(self.chk_range)
        today = QDate.currentDate()
        self.d_from, self.d_to = QDateTimeEdit(), QDateTimeEdit()
        for w, t in ((self.d_from, QTime(0, 0)), (self.d_to, QTime(23, 59))):
            w.setDisplayFormat("dd MMM yyyy  HH:mm")
            w.setCalendarPopup(True)
            w.setDateTime(QDateTime(today, t))
            w.setEnabled(False)
        lay.addWidget(QLabel("from"))
        lay.addWidget(self.d_from)
        lay.addWidget(QLabel("to"))
        lay.addWidget(self.d_to)
        self.btn_today = QPushButton("Today")
        self.btn_today.setToolTip("Set the range to today, midnight to midnight")
        lay.addWidget(self.btn_today)

        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.setInterval(300)
        self._wait.timeout.connect(self.changed.emit)
        self.search.textChanged.connect(lambda *_: self._wait.start())
        self.chk_range.toggled.connect(self._range_toggled)
        self.d_from.dateTimeChanged.connect(lambda *_: self.chk_range.isChecked() and self.changed.emit())
        self.d_to.dateTimeChanged.connect(lambda *_: self.chk_range.isChecked() and self.changed.emit())
        self.btn_today.clicked.connect(self._today)

    def _range_toggled(self, on: bool) -> None:
        self.d_from.setEnabled(on)
        self.d_to.setEnabled(on)
        self.changed.emit()

    def _today(self) -> None:
        today = QDate.currentDate()
        self.d_from.blockSignals(True)
        self.d_from.setDateTime(QDateTime(today, QTime(0, 0)))
        self.d_from.blockSignals(False)
        self.d_to.setDateTime(QDateTime(today, QTime(23, 59)))      # fires once, with both ends set
        if not self.chk_range.isChecked():
            self.chk_range.setChecked(True)

    def has_range(self) -> bool:
        return self.chk_range.isChecked()

    def values(self) -> dict:
        on = self.chk_range.isChecked()
        return {"search": self.search.text().strip(),
                "date_from": self.d_from.dateTime().toPython().replace(second=0, microsecond=0) if on else None,
                "date_to": self.d_to.dateTime().toPython().replace(second=59, microsecond=999999) if on else None}


def edit_finalized_prices(parent, ticket: dict) -> bool:
    """Right-click > Edit prices on a finalized weighing: change the price of each product on its purchase.
    Returns True if the new prices were saved."""
    from PySide2.QtWidgets import QMessageBox
    from shared.auth.session import get_session as get_login_session
    from apps.product_management_app.features.profiling.dialogs.purchase_price_edit_dialog import (
        PurchasePriceEditDialog,
    )
    pid = ticket.get("purchase_id")
    if not pid:
        QMessageBox.information(parent, "Edit prices", "This weighing has no purchase yet.")
        return False
    uid = int(getattr(get_login_session(), "user_id", 0) or 0)
    dlg = PurchasePriceEditDialog(parent, purchase_id=int(pid), ref=ticket.get("ticket_no") or f"PUR-{pid}", user_id=uid)
    return dlg.exec_() == dlg.Accepted and dlg.saved


def selected_ticket_id(table: QTableWidget):
    row = table.currentRow()
    it = table.item(row, 0) if row >= 0 else None
    return int(it.data(Qt.UserRole)) if it else None
