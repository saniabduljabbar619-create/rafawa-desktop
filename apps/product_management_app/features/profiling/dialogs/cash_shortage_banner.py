# apps/product_management_app/features/profiling/dialogs/cash_shortage_banner.py
# -*- coding: utf-8 -*-
"""
Embedded in both sale and purchase payment dialogs - shows every open
cash-handling shortage/overpayment for this party, so it's impossible to
miss during a payment without being confused for the party's normal
balance (it never touches that balance at all).
"""
from __future__ import annotations

from PySide2.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide2.QtCore import Qt


def _money(v) -> str:
    try:
        return f"\u20a6{float(v or 0):,.2f}"
    except Exception:
        return "\u20a60.00"


class CashShortageBanner(QFrame):
    def __init__(self, party_id: int, party_name: str, created_by_user_id: int,
                reference_type: str = None, parent=None):
        super().__init__(parent)
        self.party_id = int(party_id)
        self.party_name = party_name
        self.created_by_user_id = int(created_by_user_id)
        # "sale" or "purchase" - keeps a sale-originated shortage from
        # ever showing in the purchase payment dialog for the same
        # party, and vice versa. Left as None only for a future
        # all-shortages admin view, if one's ever needed.
        self.reference_type = reference_type

        self.setStyleSheet(
            "QFrame { background: #FFF6E5; border: 1px solid #FFD79A; "
            "border-radius: 8px; }"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 10, 8)
        self._layout.setSpacing(6)

        self.setVisible(False)  # hidden entirely when there's nothing open
        self.refresh()

    def refresh(self):
        # Clear existing rows
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        from database.services.cash_shortage_service import CashShortageService
        open_shortages = CashShortageService.list_open_shortages(
            self.party_id, reference_type=self.reference_type
        )

        if not open_shortages:
            self.setVisible(False)
            return

        self.setVisible(True)

        header = QLabel(
            f"\u26A0 {len(open_shortages)} open cash correction"
            f"{'s' if len(open_shortages) != 1 else ''} for {self.party_name} "
            f"- not part of the balance below, settle separately:"
        )
        header.setWordWrap(True)
        header.setStyleSheet("color:#92400e; font-weight:700;")
        self._layout.addWidget(header)

        for row in open_shortages:
            line = QHBoxLayout()
            direction_label = (
                f"Company must pay {self.party_name}" if row["direction"] == "owed_to_party"
                else f"{self.party_name} must pay company"
            )
            when = row["created_at"].strftime("%Y-%m-%d") if row["created_at"] else ""
            text = QLabel(f"{direction_label}: {_money(row['amount'])} (from {row['reference']}, {when})")
            text.setStyleSheet("color:#92400e;")
            text.setWordWrap(True)
            line.addWidget(text, 1)

            btn_settle = QPushButton("Settle")
            btn_settle.setMaximumWidth(80)
            btn_settle.clicked.connect(lambda _checked=False, sid=row["id"]: self._settle(sid))
            line.addWidget(btn_settle)

            self._layout.addLayout(line)

    def _settle(self, shortage_id: int):
        from PySide2.QtWidgets import QMessageBox, QInputDialog
        confirm = QMessageBox.question(
            self, "Settle Shortage",
            "Mark this as settled? Only do this once the real cash has "
            "actually changed hands to close it out.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        notes, ok = QInputDialog.getText(self, "Notes (optional)", "How was this settled?")
        if not ok:
            return

        from database.services.cash_shortage_service import CashShortageService
        try:
            CashShortageService.settle_shortage(
                shortage_id, settled_by_user_id=self.created_by_user_id,
                settlement_notes=notes or "",
            )
        except Exception as e:
            QMessageBox.critical(self, "Settle Failed", str(e))
            return

        self.refresh()