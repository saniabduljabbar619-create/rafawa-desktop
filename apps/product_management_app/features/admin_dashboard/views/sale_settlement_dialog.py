# apps\product_management_app\features\admin_dashboard\views\sale_settlement_dialog.py

from __future__ import annotations

from functools import partial
from typing import List, Dict, Callable, Optional

# ✅ Added QDate to imports
from PySide2.QtCore import Qt, QDate
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QHBoxLayout,
    QDateEdit, QMessageBox # ✅ Added QDateEdit, QMessageBox
)

from database.services.dashboard_service import (
    get_sale_payment_snapshot,
    delete_sale,
    DeletionNotAllowed,
)


def _fmt_money(x: float) -> str:
    return f"₦{float(x or 0):,.2f}"


class SaleSettlementDialog(QDialog):

    def __init__(
        self,
        status: str,
        role: str,
        user_id: int | None,
        current_user_id: Optional[int] = None,
        on_change: Optional[Callable[[], None]] = None,
        parent=None,
    ):
        super().__init__(parent)

        self.status = (status or "").upper()
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

        self.setWindowTitle(f"{self.status.title()} Sales")
        self.setModal(True)
        self.resize(900, 550)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # Header Layout
        header_row = QHBoxLayout()
        root.addLayout(header_row)

        self.lbl_header = QLabel(f"{self.status.title()} Sales Settlements")
        self.lbl_header.setStyleSheet("font-size: 16px; font-weight: 800;")
        header_row.addWidget(self.lbl_header)

        header_row.addStretch(1)

        # ✅ ADDED: Date Filters
        header_row.addWidget(QLabel("From:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setDate(QDate.currentDate().addDays(-30)) 
        header_row.addWidget(self.start_date_edit)

        header_row.addWidget(QLabel("To:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDate(QDate.currentDate()) 
        header_row.addWidget(self.end_date_edit)

        header_row.addSpacing(10)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._load_data)
        header_row.addWidget(self.btn_refresh)

        # Table (7th column only rendered with buttons for admins, but the
        # header stays consistent either way)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Sale ID", "Customer", "Total", "Paid", "Balance", "Status", ""]
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        # Footer totals
        self.lbl_footer = QLabel("")
        self.lbl_footer.setStyleSheet("font-weight: 700;")
        root.addWidget(self.lbl_footer)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self._load_data()

    # =====================================
    def _load_data(self):

        # ✅ Extract dates from UI
        start_date = self.start_date_edit.date().toPython()
        end_date = self.end_date_edit.date().toPython()

        # ✅ Pass dates to service
        snap = get_sale_payment_snapshot(
            role=self.role,
            user_id=self.user_id,
            start_date=start_date,
            end_date=end_date
        )

        rows: List[Dict] = snap.get("rows") or []
        filtered = [r for r in rows if r.get("status") == self.status]

        self.table.setRowCount(0)

        total_value = 0.0
        total_paid = 0.0
        total_balance = 0.0

        for r in filtered:
            row = self.table.rowCount()
            self.table.insertRow(row)

            total_value += float(r.get("total") or 0)
            total_paid += float(r.get("paid") or 0)
            total_balance += float(r.get("balance") or 0)

            self.table.setItem(row, 0, QTableWidgetItem(str(r.get("sale_id"))))
            self.table.setItem(row, 1, QTableWidgetItem(str(r.get("customer") or "")))

            total_item = QTableWidgetItem(_fmt_money(r.get("total")))
            total_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 2, total_item)

            paid_item = QTableWidgetItem(_fmt_money(r.get("paid")))
            paid_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, paid_item)

            bal_item = QTableWidgetItem(_fmt_money(r.get("balance")))
            bal_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 4, bal_item)

            self.table.setItem(row, 5, QTableWidgetItem(r.get("status")))

            if self.can_delete:
                sale_id = r.get("sale_id")
                btn_delete = QPushButton("Delete")
                btn_delete.setStyleSheet(
                    "QPushButton { color: #B00020; font-weight: 700; } "
                    "QPushButton:hover { color: #7A0016; }"
                )
                btn_delete.clicked.connect(partial(self._confirm_delete, sale_id, r))
                self.table.setCellWidget(row, 6, btn_delete)

        self.lbl_footer.setText(
            f"Total Value: {_fmt_money(total_value)}   |   "
            f"Total Paid: {_fmt_money(total_paid)}   |   "
            f"Total Balance: {_fmt_money(total_balance)}"
        )

    # =====================================
    def _confirm_delete(self, sale_id, row_data: Dict):
        customer = row_data.get("customer") or "Unknown customer"
        total = _fmt_money(row_data.get("total"))

        answer = QMessageBox.warning(
            self,
            "Delete Sale",
            f"Permanently delete Sale #{sale_id} ({customer}, {total})?\n\n"
            "This also removes its payments, advance allocations, returns, and "
            "discounts. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            delete_sale(sale_id=sale_id, deleted_by_user_id=self.current_user_id)
        except DeletionNotAllowed as e:
            QMessageBox.critical(self, "Cannot Delete", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", f"Could not delete sale:\n{e}")
            return

        self._load_data()
        if self.on_change:
            self.on_change()