#apps/product_management_app/features/cashier/dialogs/activity_history_dialog.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any
import re

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDateTimeEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox
)
from PySide2.QtCore import Qt, QDateTime

from database.services.payment_service import PaymentService
from apps.product_management_app.app_state import state

# ✅ Single-file receipt engine
from apps.product_management_app.features.receipts.activity_receipt_printer import ActivityReceipt, ShopMeta


def _parse_dt(dt_str: str) -> Optional[datetime]:
    """
    Accepts ISO strings like: 2026-01-20T12:34:56
    Returns None if parsing fails.
    """
    s = (dt_str or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _get(a: Any, key: str, default=None):
    """Supports dict rows + object rows."""
    if isinstance(a, dict):
        return a.get(key, default)
    return getattr(a, key, default)


def _extract_id_from_ref(ref: str) -> int:
    """
    Robustly extract an integer id from Ref column.

    Supports common formats:
      - "sale:12"
      - "purchase:00012"
      - "SALE-00012"
      - "payment#45"
      - "advance 9"
      - "... 123"
    """
    s = (ref or "").strip()
    if not s:
        return 0

    # Prefer last integer group
    m = re.search(r"(\d+)\s*$", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0

    # Fallback: any integer in string (take last)
    nums = re.findall(r"\d+", s)
    if nums:
        try:
            return int(nums[-1])
        except Exception:
            return 0

    return 0


class ActivityHistoryDialog(QDialog):
    """
    Professional history viewer for a single customer.

    DB-backed via PaymentService.list_customer_activities_db()

    Filters:
    - Date/time range (From -> To)
    - Ledger: All / Crops / Fertilizers
    - Type: All / payment / advance / sale / intake

    Table:
    - DT | Ref | Ledger | Type | Amount | Summary | Status
    """

    def __init__(self, parent=None, customer_id: int = 0, customer_label: str = ""):
        super().__init__(parent)
        self.setModal(True)
        self.resize(900, 520)
        self.setWindowTitle("Customer Activities")

        self.customer_id = int(customer_id)
        self.customer_label = customer_label

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Title
        title_row = QHBoxLayout()
        root.addLayout(title_row)

        lbl = QLabel("Activities")
        lbl.setObjectName("Title")
        title_row.addWidget(lbl)

        title_row.addStretch(1)

        if customer_label:
            c = QLabel(customer_label)
            c.setObjectName("Muted")
            title_row.addWidget(c)

        # Filters row
        filters = QHBoxLayout()
        root.addLayout(filters)

        filters.addWidget(QLabel("From:"))
        self.dt_from = QDateTimeEdit()
        self.dt_from.setCalendarPopup(True)
        self.dt_from.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_from.setDateTime(QDateTime.currentDateTime().addDays(-30))
        filters.addWidget(self.dt_from)

        filters.addWidget(QLabel("To:"))
        self.dt_to = QDateTimeEdit()
        self.dt_to.setCalendarPopup(True)
        self.dt_to.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_to.setDateTime(QDateTime.currentDateTime())
        filters.addWidget(self.dt_to)

        filters.addSpacing(8)

        filters.addWidget(QLabel("Ledger:"))
        self.ledger = QComboBox()
        self.ledger.addItems(["All", "Crops", "Fertilizers"])
        filters.addWidget(self.ledger)

        filters.addWidget(QLabel("Type:"))
        self.typ = QComboBox()
        self.typ.addItems([
            "All",
            "payment",
            "advance",
            "sale",
            "intake",
        ])
        filters.addWidget(self.typ)

        filters.addStretch(1)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("PrimaryButton")
        filters.addWidget(self.btn_apply)

        self.btn_reset = QPushButton("Reset")
        filters.addWidget(self.btn_reset)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["DT", "Ref", "Ledger", "Type", "Amount", "Summary", "Status"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        # Footer buttons
        footer = QHBoxLayout()
        root.addLayout(footer)

        # ✅ Print button
        self.btn_print = QPushButton("Print")
        self.btn_print.setObjectName("PrimaryButton")
        footer.addWidget(self.btn_print)

        footer.addStretch(1)

        self.btn_close = QPushButton("Close")
        footer.addWidget(self.btn_close)

        # Wiring
        self.btn_close.clicked.connect(self.accept)
        self.btn_apply.clicked.connect(self.refresh)
        self.btn_reset.clicked.connect(self._reset)

        # ✅ Print wiring
        self.btn_print.clicked.connect(self._print_selected)

        self.refresh()

    def _reset(self):
        self.dt_from.setDateTime(QDateTime.currentDateTime().addDays(-30))
        self.dt_to.setDateTime(QDateTime.currentDateTime())
        self.ledger.setCurrentText("All")
        self.typ.setCurrentText("All")
        self.refresh()

    def _filters(self):
        f = self.dt_from.dateTime().toPython()
        t = self.dt_to.dateTime().toPython()

        ledger = self.ledger.currentText().strip().lower()
        if ledger == "all":
            ledger = ""

        typ = self.typ.currentText().strip().lower()
        if typ == "all":
            typ = ""

        return f, t, ledger, typ

    def _matches(self, a) -> bool:
        f, t, ledger, typ = self._filters()

        a_ledger = str(_get(a, "ledger", "") or "").strip().lower()
        a_typ = str(_get(a, "type", "") or "").strip().lower()

        if ledger and a_ledger != ledger:
            return False

        if typ and a_typ != typ:
            return False

        a_dt = _parse_dt(str(_get(a, "dt", "") or ""))
        if a_dt is None:
            return True

        return (f <= a_dt <= t)

    def _selected_activity_key(self) -> Optional[tuple]:
        """
        Returns (activity_type, activity_id) from the selected row.
        Columns:
          0 DT | 1 Ref | 2 Ledger | 3 Type | 4 Amount | 5 Summary | 6 Status
        """
        r = self.table.currentRow()
        if r < 0:
            return None

        ref = ""
        typ = ""
        try:
            ref = (self.table.item(r, 1).text() or "").strip()
            typ = (self.table.item(r, 3).text() or "").strip().lower()
        except Exception:
            return None

        rid = _extract_id_from_ref(ref)
        if rid <= 0:
            return None

        # Map dialog types to receipt engine types
        # - intake is stored as Purchase-based intake -> engine accepts "intake" (or "purchase")
        # - payment/advance/sale are direct
        if typ not in ("payment", "advance", "sale", "intake"):
            # If typ is unexpected, try to infer from ref
            low = ref.lower()
            if "sale" in low:
                typ = "sale"
            elif "purchase" in low or "intake" in low:
                typ = "intake"
            elif "advance" in low:
                typ = "advance"
            elif "payment" in low:
                typ = "payment"
            else:
                return None

        return (typ, rid)

    def _print_selected(self):
        key = self._selected_activity_key()
        if not key:
            QMessageBox.information(self, "Print", "Select an activity row first.")
            return

        typ, rid = key

        try:
            shop = ShopMeta(
                shop_name="Alh Zakari Rafawa Enterprises",
                address="No 18 Kwanar Tsau, Gwarzo Kano State",
                phone="07046329444",
                paper_width=48,  # ✅ 80mm look like your sample
            )
            ActivityReceipt(shop).print_from_activity(
                self,
                activity_type=str(typ),
                activity_id=int(rid),
                cashier_name_override="",   # optional
                printer_name=None,
            )
        except Exception as e:
            QMessageBox.critical(self, "Print Failed", f"{e}")

    def refresh(self):
        try:
            acts = PaymentService.list_customer_activities_db(
                int(self.customer_id),
                session_user_id=state.require_user_id(),
                role=state.session_user.get("role", ""),
                limit=5000,
            )
        except Exception as e:
            QMessageBox.warning(self, "Activities", f"Failed to load activities:\n{e}")
            acts = []

        acts = [a for a in acts if self._matches(a)]

        # newest first
        acts.sort(key=lambda x: _parse_dt(str(_get(x, "dt", "") or "")) or datetime.min, reverse=True)

        self.table.setRowCount(0)
        for a in acts:
            row = self.table.rowCount()
            self.table.insertRow(row)

            dt = str(_get(a, "dt", "") or "")
            ref = str(_get(a, "reference", "") or "")
            ledger = str(_get(a, "ledger", "") or "")
            typ = str(_get(a, "type", "") or "")
            amount = float(_get(a, "amount", 0.0) or 0.0)
            summary = str(_get(a, "summary", "") or "")
            status = str(_get(a, "status", "") or "")

            vals = [dt, ref, ledger, typ, f"{amount:,.2f}", summary, status]

            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, c, it)
