# apps\product_management_app\features\admin_dashboard\views\advance_settlement_dialog.py

from __future__ import annotations

from functools import partial
from typing import Callable, Optional

# ✅ Add QDate to imports
from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QDateEdit,  # ✅ Import QDateEdit
    QMessageBox,
)

from database.services.dashboard_service import (
    get_advance_settlement_snapshot,
    delete_advance,
    DeletionNotAllowed,
)

def _fmt_money(x: float) -> str:
    return f"₦{float(x or 0):,.2f}"

class AdvanceSettlementDialog(QDialog):

    def __init__(
        self,
        direction: str,
        role: str,
        user_id,
        current_user_id: Optional[int] = None,
        on_change: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)

        self.direction = direction.upper()
        self.role = role
        self.user_id = user_id

        # The logged-in admin actually performing actions in this dialog —
        # distinct from `user_id` above, which is only used for cashier
        # row-scoping and is commonly None for admins. Required for delete.
        self.current_user_id = current_user_id

        # Optional callback so the parent dashboard can refresh its cards
        # after a row here is deleted.
        self.on_change = on_change

        self.can_delete = (self.role == "admin")

        self.setWindowTitle(f"Advance Settlement — {self.direction}")
        self.resize(1000, 600)

        self._build_ui()
        self.refresh()

    # ==============================
    # UI Construction
    # ==============================
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ---- Header Block ----
        header_row = QHBoxLayout()
        root.addLayout(header_row)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 900; color: #0B0B0B;")
        header_row.addWidget(self.title_label)

        header_row.addStretch(1)

        # ---- Date Filters ----
        # NOTE: this filters advances by *creation* date, not by whether
        # they're still outstanding. An advance from 90 days ago that is
        # still unpaid is still real, live debt - it must not disappear
        # from this dialog just because it's old. So the default range
        # here is effectively "since the beginning" (EARLIEST_DATE..today)
        # rather than a rolling 30-day window, so the dialog opens showing
        # every outstanding advance by default, matching the card totals
        # on the main dashboard. The date pickers are still there for
        # anyone who genuinely wants to narrow to advances *created* in a
        # specific window.
        header_row.addWidget(QLabel("From:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(self.EARLIEST_DATE)
        header_row.addWidget(self.start_date_edit)

        header_row.addWidget(QLabel("To:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        # Default to today
        self.end_date_edit.setDate(QDate.currentDate())
        header_row.addWidget(self.end_date_edit)

        # Spacer before refresh button
        header_row.addSpacing(10)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        header_row.addWidget(self.btn_refresh)

        # ---- Summary Block ----
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #444;")
        root.addWidget(self.summary_label)

        # ---- Table ----
        # 9th column only rendered with buttons for admins, but the header
        # stays consistent either way.
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Date", "Party", "Reference", "Amount", "Bags", "KG", "Created By", "Note", ""]
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        # ---- Footer Summary ----
        self.footer_label = QLabel()
        self.footer_label.setStyleSheet("font-size: 13px; font-weight: 800; color: #111;")
        root.addWidget(self.footer_label)

    # A stand-in for "no lower bound" that QDateEdit can actually display.
    # Anything genuinely older than this in the data would be a data-entry
    # error, not a real advance.
    EARLIEST_DATE = QDate(2000, 1, 1)

    # ==============================
    # Data Loading
    # ==============================
    def refresh(self):
        self.title_label.setText(f"{self.direction} Advances")

        # ✅ Extract Python date objects from the UI
        start_date = self.start_date_edit.date().toPython()
        end_date = self.end_date_edit.date().toPython()

        # ✅ Pass dates to the service
        snapshot = get_advance_settlement_snapshot(
            direction=self.direction,
            role=self.role,
            user_id=self.user_id,
            start_date=start_date, 
            end_date=end_date      
        ) or {}

        summary = snapshot.get("summary") or {}
        rows = snapshot.get("rows") or []

        count = summary.get("count", 0)
        total_amount = summary.get("total_amount", 0)

        self.summary_label.setText(
            f"Total Records: {count}    |    Total Amount: {_fmt_money(total_amount)}"
        )

        self._populate_table(rows)

        # Footer update
        # Use the SAME date range, role/user scoping (via user_id being
        # irrelevant here - grand summary is always the true global
        # position), advance_type, and status filters as the table above
        # and as the dashboard cards, so the three numbers on screen never
        # disagree. Previously this always asked for scope="today"
        # regardless of what the user picked above, and summed the raw
        # advance amount (not the remaining balance) across every advance
        # type and status - it could never actually match the table it
        # sits under.
        from database.services.dashboard_service import get_advance_grand_summary
        grand = get_advance_grand_summary(start_date=start_date, end_date=end_date)

        received = grand.get("received", 0)
        given = grand.get("given", 0)
        net = grand.get("net", 0)

        self.footer_label.setText(
            f"Grand Received: {_fmt_money(received)}    |    "
            f"Grand Given: {_fmt_money(given)}    |    "
            f"Net Position: {_fmt_money(net)}"
        )

    # ==============================
    # Table Rendering
    # ==============================
    def _populate_table(self, rows: list):
        self.table.setRowCount(0)

        for r in rows:
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)

            self.table.setItem(row_index, 0, QTableWidgetItem(str(r.get("date") or "")))
            self.table.setItem(row_index, 1, QTableWidgetItem(str(r.get("party_name") or "")))
            self.table.setItem(row_index, 2, QTableWidgetItem(str(r.get("reference") or "")))

            amount_item = QTableWidgetItem(_fmt_money(r.get("amount")))
            amount_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            if self.direction == "IN":
                amount_item.setForeground(Qt.darkGreen)
            else:
                amount_item.setForeground(Qt.red)

            self.table.setItem(row_index, 3, amount_item)

            bags_item = QTableWidgetItem(f"{float(r.get('qty_bags') or 0):,.2f}")
            bags_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_index, 4, bags_item)

            kg_item = QTableWidgetItem(f"{float(r.get('total_weight_kg') or 0):,.2f}")
            kg_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_index, 5, kg_item)

            self.table.setItem(row_index, 6, QTableWidgetItem(str(r.get("created_by") or "")))
            self.table.setItem(row_index, 7, QTableWidgetItem(str(r.get("note") or "")))

            if self.can_delete:
                advance_id = r.get("advance_id")
                btn_delete = QPushButton("Delete")
                btn_delete.setStyleSheet(
                    "QPushButton { color: #B00020; font-weight: 700; } "
                    "QPushButton:hover { color: #7A0016; }"
                )
                btn_delete.clicked.connect(partial(self._confirm_delete, advance_id, r))
                self.table.setCellWidget(row_index, 8, btn_delete)

    # ==============================
    # Delete
    # ==============================
    def _confirm_delete(self, advance_id, row_data: dict):
        party = row_data.get("party_name") or "Unknown party"
        amount = _fmt_money(row_data.get("amount"))
        reference = row_data.get("reference") or f"ADV-{advance_id}"

        answer = QMessageBox.warning(
            self,
            "Delete Advance",
            f"Permanently delete {reference} ({party}, {amount})?\n\n"
            "This also removes its linked payments and allocations. "
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            delete_advance(advance_id=advance_id, deleted_by_user_id=self.current_user_id)
        except DeletionNotAllowed as e:
            QMessageBox.critical(self, "Cannot Delete", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", f"Could not delete advance:\n{e}")
            return

        self.refresh()
        if self.on_change:
            self.on_change()