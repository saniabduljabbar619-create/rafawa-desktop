# apps/product_management_app/features/weigher/views/ticket_table.py
"""Shared look for the weigh-ticket lists (weigher history and the cashier/admin queue)."""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QWidget,
)

from shared.uix.widgets.cards import chip, style_table

STATUS_TONE = {"open": "amber", "submitted": "blue", "finalized": "green", "cancelled": "grey"}
STATUS_TEXT = {"open": "Weighing", "submitted": "Awaiting cashier", "finalized": "Finalized", "cancelled": "Cancelled"}


def fmt_bags(v) -> str:
    v = float(v or 0)
    return f"{int(v)}" if v == int(v) else f"{v:g}"


def make_ticket_table(show_amount: bool, show_weigher: bool) -> QTableWidget:
    cols = ["Ticket", "Date", "Party", "Products", "Bags", "Weight (kg)"]
    if show_weigher:
        cols.append("Weigher")
    if show_amount:
        cols.append("Amount")
    cols.append("Status")
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    style_table(t, 48)
    t.horizontalHeader().setSectionResizeMode(len(cols) - 1, QHeaderView.Fixed)
    t.setColumnWidth(len(cols) - 1, 170)                  # room for the status chip
    t.setProperty("show_amount", show_amount)
    t.setProperty("show_weigher", show_weigher)
    return t


def fill_ticket_table(table: QTableWidget, rows) -> None:
    show_amount = bool(table.property("show_amount"))
    show_weigher = bool(table.property("show_weigher"))
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
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setData(Qt.UserRole, int(t["id"]))
            if c in (4, 5) or (show_amount and c == len(vals) - 1):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(r, c, it)
        # status chip in the last column
        table.setItem(r, len(vals), QTableWidgetItem(""))
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.addWidget(chip(STATUS_TEXT.get(t["status"], t["status"]), STATUS_TONE.get(t["status"], "grey")),
                      0, Qt.AlignVCenter)
        lay.addStretch(1)
        cell.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        table.setCellWidget(r, len(vals), cell)
        table.item(r, len(vals)).setData(Qt.UserRole, int(t["id"]))
    table.setUpdatesEnabled(True)


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
