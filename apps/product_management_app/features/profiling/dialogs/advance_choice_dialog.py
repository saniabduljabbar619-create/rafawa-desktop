# apps/product_management_app/features/profiling/dialogs/advance_choice_dialog.py
# -*- coding: utf-8 -*-
"""
Advance-choice dialog + helper.

Advances are NEVER applied automatically. Whenever a payment is about to be
taken and the party has a usable advance, the caller invokes
`prompt_and_apply_advance(...)`. If (and only if) an advance exists, this shows
a dialog with two choices:

    1. Use the advance first, then pay the remaining balance.
    2. Skip the advance and pay the full amount.
"""

from __future__ import annotations

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QPushButton, QFrame
)
from PySide2.QtCore import Qt

from database.services.advance_service import AdvanceService
from database.services.payment_center import PaymentCenter


class AdvanceChoiceDialog(QDialog):
    def __init__(self, parent, *, party_name: str, entity_type: str,
                 available: float, balance: float):
        super().__init__(parent)
        self.setWindowTitle("Use Advance?")
        self.setMinimumWidth(380)

        applied = min(available, balance)
        remaining_if_used = max(balance - applied, 0.0)
        noun = "customer" if entity_type == "sale" else "supplier"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("This party has an advance on record")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        root.addWidget(title)

        def _line(label, value):
            row = QHBoxLayout()
            l = QLabel(label)
            v = QLabel(value)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.setStyleSheet("font-weight: bold;")
            row.addWidget(l)
            row.addStretch(1)
            row.addWidget(v)
            root.addLayout(row)

        _line(f"{noun.capitalize()}:", party_name or "-")
        _line("Advance available:", f"{available:,.2f}")
        _line("Transaction balance:", f"{balance:,.2f}")

        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        root.addWidget(rule)

        self.group = QButtonGroup(self)

        self.rb_use = QRadioButton(
            f"Use advance first ({applied:,.2f}), then pay {remaining_if_used:,.2f}"
        )
        self.rb_skip = QRadioButton(
            f"Skip advance, pay full {balance:,.2f}"
        )
        self.rb_use.setChecked(True)

        self.group.addButton(self.rb_use, 1)
        self.group.addButton(self.rb_skip, 2)
        root.addWidget(self.rb_use)
        root.addWidget(self.rb_skip)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_ok = QPushButton("Continue")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        btns.addWidget(self.btn_ok)
        root.addLayout(btns)

    def use_advance(self) -> bool:
        return self.rb_use.isChecked()


def prompt_and_apply_advance(parent, *, party_id: int, entity_type: str,
                             entity_id: int, balance: float,
                             user_id: int, party_name: str = "") -> float:
    """
    If the party has a usable advance, ask whether to apply it. Applies it only
    on explicit choice. Returns the balance still to pay afterwards.

    entity_type: "sale" (uses received advances) or "purchase" (uses given).
    """
    if party_id <= 0 or balance <= 0:
        return balance

    direction = "received" if entity_type == "sale" else "given"
    try:
        fifo = AdvanceService.get_fifo_advances(party_id, direction)
        available = sum(float(a.get("remaining") or 0) for a in fifo)
    except Exception:
        available = 0.0

    if available <= 0:
        return balance  # no advance -> no prompt, proceed to normal payment

    dlg = AdvanceChoiceDialog(
        parent,
        party_name=party_name,
        entity_type=entity_type,
        available=available,
        balance=balance,
    )
    if dlg.exec_() != QDialog.Accepted:
        return balance  # cancelled == skip

    if not dlg.use_advance():
        return balance  # user chose to keep the advance

    # Apply the advance via the existing allocation + settlement logic.
    if entity_type == "sale":
        res = PaymentCenter.handle_sale_creation(
            party_id=party_id, sale_id=entity_id,
            sale_total=balance, created_by_user_id=user_id
        )
    else:
        res = PaymentCenter.handle_purchase_creation(
            party_id=party_id, purchase_id=entity_id,
            purchase_total=balance, created_by_user_id=user_id
        )
    return float(res.get("remaining", balance))