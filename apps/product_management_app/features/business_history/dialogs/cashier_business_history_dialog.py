# apps/product_management_app/features/business_history/dialogs/cashier_business_history_dialog.py
# -*- coding: utf-8 -*-
"""
One cashier's individual purchases, sales, or returns - opened by
double-clicking a row in CashierHistoryListView. A dropdown switches
between "Total Purchases", "Total Sales", and "Total Returns"; each
shows every transaction of that kind this cashier created, one row per
transaction, with the party involved, what was transacted, when, and
who created it.
"""
from __future__ import annotations

from datetime import date as _date

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QCheckBox, QDateEdit,
)
from PySide2.QtCore import Qt, QDate
from PySide2.QtGui import QColor, QFont

from database.services.drawer_service import DrawerService


def _money(v) -> str:
    try:
        return f"₦{float(v or 0):,.2f}"
    except Exception:
        return "₦0.00"


_STATUS_LABELS = {"paid": "Paid", "partial": "Partial", "unpaid": "Unpaid"}
_STATUS_COLORS = {"paid": "#16a34a", "partial": "#d97706", "unpaid": "#dc2626"}
# Same pastel language as the admin dashboard's cards (green/amber/red),
# applied to the whole row here so an unpaid or partial transaction is
# visible at a glance, not just from the Status column's text.
_STATUS_ROW_BG = {"paid": "#E9F8EE", "partial": "#FFF6E5", "unpaid": "#FDEAEA"}
# Reference-column accent distinguishing a purchase row from a sale row -
# mostly latent for those two (the dropdown already shows one kind at a
# time), but earns its keep for "Total Returns", which is exactly the
# "Both" case this was built for: it mixes sale_return and purchase_return
# rows in one table, so the accent is how a row identifies itself.
_KIND_COLORS = {
    "purchase": "#2563eb", "sale": "#7c3aed",
    "sale_return": "#0d9488", "purchase_return": "#c026d3",
}


class _SortableItem(QTableWidgetItem):
    """Sorts by a stored numeric/date key (Qt.UserRole) rather than the
    displayed text - so Amount and When sort correctly (numerically,
    chronologically), not lexicographically."""
    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if a is not None and b is not None:
            try:
                return a < b
            except Exception:
                pass
        return super().__lt__(other)


class CashierBusinessHistoryDialog(QDialog):
    def __init__(self, user_id: int, username: str, parent=None):
        super().__init__(parent)
        self.user_id = int(user_id)
        self.username = username

        self.setWindowTitle(f"Business History - {username}")
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        )
        self.resize(980, 580)

        layout = QVBoxLayout(self)

        title = QLabel(username)
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        layout.addWidget(title)

        hint = QLabel("Double-click a row to open its full detail. Click a column header to sort.")
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self.legend = QLabel()
        self.legend.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.legend)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Total Purchases", "purchase")
        self.kind_combo.addItem("Total Sales", "sale")
        self.kind_combo.addItem("Total Returns", "return")
        self.kind_combo.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.kind_combo)
        top.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #666;")
        top.addWidget(self.count_label)
        layout.addLayout(top)

        # Defaults to TODAY only - a fresh morning starts with an empty
        # table unless there's new activity, same convention as the "Fully
        # Paid" cards on the admin dashboard. Checking the box switches to
        # an explicit, admin-picked range for browsing history.
        date_row = QHBoxLayout()
        self.date_chk = QCheckBox("Filter by date range")
        self.date_chk.stateChanged.connect(self._on_dates_changed)
        date_row.addWidget(self.date_chk)

        self.d_from = QDateEdit()
        self.d_from.setCalendarPopup(True)
        self.d_from.setDisplayFormat("yyyy-MM-dd")
        self.d_from.setDate(QDate.currentDate())
        self.d_from.setEnabled(False)
        self.d_from.dateChanged.connect(self._on_dates_changed)
        date_row.addWidget(self.d_from)

        date_row.addWidget(QLabel("to"))

        self.d_to = QDateEdit()
        self.d_to.setCalendarPopup(True)
        self.d_to.setDisplayFormat("yyyy-MM-dd")
        self.d_to.setDate(QDate.currentDate())
        self.d_to.setEnabled(False)
        self.d_to.dateChanged.connect(self._on_dates_changed)
        date_row.addWidget(self.d_to)

        date_row.addStretch(1)
        layout.addLayout(date_row)

        self.start_date = _date.today()
        self.end_date = _date.today()

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["When", "Party", "Reference", "Product", "Amount", "Status", "Created By"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(self._open_detail)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.refresh()

    def _on_dates_changed(self):
        on = self.date_chk.isChecked()
        self.d_from.setEnabled(on)
        self.d_to.setEnabled(on)
        if on:
            self.start_date = self.d_from.date().toPython()
            self.end_date = self.d_to.date().toPython()
        else:
            self.start_date = _date.today()
            self.end_date = _date.today()
        self.refresh()

    def refresh(self):
        kind = self.kind_combo.currentData()
        try:
            rows = DrawerService.cashier_transactions(
                user_id=self.user_id, kind=kind,
                start_date=self.start_date, end_date=self.end_date,
            )
        except Exception as e:
            QMessageBox.critical(self, "Load Failed", str(e))
            rows = []
        self._fill_table(rows)
        self._update_legend(kind)
        if kind == "purchase":
            label = "purchase(s)"
        elif kind == "sale":
            label = "sale(s)"
        else:
            label = "return(s)"
        if self.start_date == self.end_date:
            range_txt = self.start_date.strftime("%Y-%m-%d")
        else:
            range_txt = f"{self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}"
        self.count_label.setText(f"{len(rows)} {label} ({range_txt})")

    def _update_legend(self, kind: str):
        # "Total Returns" mixes two kinds (sale_return/purchase_return) in
        # one table, same as a future "Both" for purchase/sale would - so
        # its legend swaps to the pair that's actually on screen, rather
        # than showing all four kinds all the time.
        if kind == "return":
            ref_bit = (
                f"<span style='color:{_KIND_COLORS['sale_return']};'>sale return</span> / "
                f"<span style='color:{_KIND_COLORS['purchase_return']};'>purchase return</span>"
            )
            status_bit = "Status column shows the stated reason, if any"
        else:
            ref_bit = (
                f"<span style='color:{_KIND_COLORS['purchase']};'>purchase</span> / "
                f"<span style='color:{_KIND_COLORS['sale']};'>sale</span>"
            )
            status_bit = "Row color = payment status (green/amber/red = paid/partial/unpaid)"
        self.legend.setText(
            f"{status_bit} "
            "&nbsp;•&nbsp; <b>Bold date</b> = today "
            "&nbsp;•&nbsp; Reference color = " + ref_bit
        )

    def _fill_table(self, rows):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        today = _date.today()
        for r, t in enumerate(rows):
            when = t.get("when")
            when_s = when.strftime("%Y-%m-%d %H:%M") if when else ""
            status = t.get("payment_status", "")
            status_label = _STATUS_LABELS.get(status, status)
            is_today = bool(when and when.date() == today)

            when_item = _SortableItem(when_s)
            when_item.setData(Qt.UserRole, when.timestamp() if when else 0)
            # (kind, id) travels WITH this item through any sort Qt performs.
            when_item.setData(Qt.UserRole + 1, (t["kind"], t["id"]))
            if is_today:
                bold = QFont()
                bold.setBold(True)
                when_item.setFont(bold)
            self.table.setItem(r, 0, when_item)

            self.table.setItem(r, 1, QTableWidgetItem(t.get("party_name", "")))

            ref_item = QTableWidgetItem(t.get("doc_no", ""))
            kind_color = _KIND_COLORS.get(t.get("kind"))
            if kind_color:
                ref_item.setForeground(QColor(kind_color))
            self.table.setItem(r, 2, ref_item)

            self.table.setItem(r, 3, QTableWidgetItem(t.get("product_summary", "")))

            amount_item = _SortableItem(_money(t.get("amount")))
            amount_item.setData(Qt.UserRole, float(t.get("amount") or 0))
            amount_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 4, amount_item)

            status_item = QTableWidgetItem(status_label)
            color = _STATUS_COLORS.get(status)
            if color:
                status_item.setForeground(QColor(color))
            self.table.setItem(r, 5, status_item)

            self.table.setItem(r, 6, QTableWidgetItem(t.get("created_by", "")))

            # Whole-row payment-status tint, applied last so it lands under
            # every cell in this row regardless of which order they were set.
            row_bg = _STATUS_ROW_BG.get(status)
            if row_bg:
                bg_color = QColor(row_bg)
                for c in range(self.table.columnCount()):
                    cell = self.table.item(r, c)
                    if cell is not None:
                        cell.setBackground(bg_color)
        self.table.setSortingEnabled(True)

    def _open_detail(self, row, _col):
        item = self.table.item(row, 0)
        if item is None:
            return
        kind_id = item.data(Qt.UserRole + 1)
        if not kind_id:
            return
        kind, doc_id = kind_id

        try:
            if kind in ("sale", "sale_return"):
                if kind == "sale_return":
                    # No standalone return-detail dialog exists - doc_id is
                    # the RETURN's parent sale id (see DrawerService.
                    # _cashier_returns), so this opens the full sale it came
                    # from, same target AdminReturnsView uses for previews.
                    from apps.product_management_app.features.profiling.views.sale_history_dialog import (
                        SaleHistoryDialog,
                    )
                    dlg = SaleHistoryDialog(sale_id=doc_id, parent=self)
                else:
                    from apps.product_management_app.features.suppliers.views.supplier_activity_dialogs import (
                        SupplierSaleDetailDialog,
                    )
                    dlg = SupplierSaleDetailDialog(doc_id, parent=self)
            else:
                if kind == "purchase_return":
                    from apps.product_management_app.features.profiling.views.purchase_history_dialog import (
                        PurchaseHistoryDialog,
                    )
                    dlg = PurchaseHistoryDialog(purchase_id=doc_id, parent=self)
                else:
                    from apps.product_management_app.features.suppliers.views.supplier_activity_dialogs import (
                        SupplierPurchaseDetailDialog,
                    )
                    dlg = SupplierPurchaseDetailDialog(doc_id, parent=self)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Detail Failed", str(e))