# apps/product_management_app/features/profiling/dialogs/advance_apply_dialog.py
# -*- coding: utf-8 -*-
"""
Choose where an advance goes.

An advance is never swept over everything a party owes. This dialog lists what is still owed, shows how
much advance there is, and lets the user type how much of it goes to each purchase (or sale). It starts
with a suggestion - oldest first, no more than the advance just recorded - that the user can change.
Whatever is not given to a document simply stays as advance.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from database.services.payment_center import PaymentCenter

COLS = ["Document", "Date", "Total", "Still owed", "Use from advance"]


def _num(raw) -> float:
    raw = str(raw or "").replace(",", "").strip()
    return float(raw) if raw else 0.0


class AdvanceApplyDialog(QDialog):
    def __init__(self, parent, *, party_name: str, kind: str, docs: List[dict], available: float,
                 suggest: Optional[float] = None):
        super().__init__(parent)
        self.kind, self.docs, self.available = kind, docs, float(available)
        self.allocations: List[Tuple[int, float]] = []
        word = "purchases" if kind == "purchase" else "sales"
        self.setWindowTitle("Use the advance on…")
        self.setMinimumWidth(640)
        root = QVBoxLayout(self)
        head = QLabel(f"<b>{party_name or 'This party'}</b> has <b>₦{self.available:,.2f}</b> of advance not yet used "
                      f"and owes on {len(docs)} {word}.<br>Type how much of the advance goes to each one. "
                      "Anything you leave out stays as advance.")
        head.setWordWrap(True)
        root.addWidget(head)

        self.table = QTableWidget(len(docs), len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.edits: List[QLineEdit] = []
        left = float(suggest if suggest is not None else self.available)
        left = min(left, self.available)
        for i, d in enumerate(docs):
            give = max(min(left, d["balance"]), 0.0)
            left -= give
            vals = [d["ref"], d["date"].strftime("%d %b %Y") if d["date"] else "-",
                    f"{d['total']:,.2f}", f"{d['balance']:,.2f}"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
            ed = QLineEdit(f"{give:.2f}" if give > 0.004 else "")
            ed.setAlignment(Qt.AlignRight)
            ed.setPlaceholderText("0.00")
            ed.textChanged.connect(self._recount)
            self.table.setCellWidget(i, 4, ed)
            self.edits.append(ed)
        root.addWidget(self.table, 1)

        self.lbl_total = QLabel("")
        self.lbl_total.setStyleSheet("font-weight:600;")
        root.addWidget(self.lbl_total)

        row = QHBoxLayout()
        self.btn_keep = QPushButton("Keep it all as advance")
        self.btn_apply = QPushButton("Use the advance as typed")
        self.btn_apply.setDefault(True)
        row.addStretch(1)
        row.addWidget(self.btn_keep)
        row.addWidget(self.btn_apply)
        root.addLayout(row)
        self.btn_keep.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self._accept)
        self._recount()

    def _typed(self) -> List[Tuple[int, float]]:
        return [(d["id"], _num(e.text())) for d, e in zip(self.docs, self.edits)]

    def _problem(self) -> str:
        total = 0.0
        for d, e in zip(self.docs, self.edits):
            try:
                a = _num(e.text())
            except ValueError:
                return f"{d['ref']}: type a number."
            if a < 0:
                return f"{d['ref']}: cannot be negative."
            if a > d["balance"] + 0.005:
                return f"{d['ref']}: only ₦{d['balance']:,.2f} is owed on it."
            total += a
        if total > self.available + 0.005:
            return f"That is more than the advance (₦{self.available:,.2f})."
        return ""

    def _recount(self, *_a) -> None:
        bad = self._problem()
        try:
            total = sum(a for _d, a in self._typed())
        except ValueError:
            total = 0.0
        if bad:
            self.lbl_total.setText(bad)
            self.lbl_total.setStyleSheet("font-weight:600; color:#a23b34;")
        else:
            self.lbl_total.setText(f"Using ₦{total:,.2f}  -  ₦{self.available - total:,.2f} stays as advance")
            self.lbl_total.setStyleSheet("font-weight:600;")
        self.btn_apply.setEnabled(not bad and total > 0.004)

    def _accept(self) -> None:
        bad = self._problem()
        if bad:
            QMessageBox.warning(self, "Advance", bad)
            return
        self.allocations = [(i, a) for i, a in self._typed() if a > 0.004]
        self.accept()


def choose_and_apply_advance(parent, *, party_id: int, kind: str, user_id: int, party_name: str = "",
                             suggest: Optional[float] = None) -> float:
    """Show the chooser when the party has both unused advance and something owing; apply what the user picks.

    Returns the amount applied (0 if nothing to choose from, or the user kept it all as advance).
    """
    docs = PaymentCenter.open_documents(party_id, kind)
    available = PaymentCenter.available_advance(party_id, kind)
    if not docs or available <= 0.004:
        return 0.0
    dlg = AdvanceApplyDialog(parent, party_name=party_name, kind=kind, docs=docs, available=available,
                             suggest=suggest)
    if dlg.exec_() != QDialog.Accepted or not dlg.allocations:
        return 0.0
    return PaymentCenter.apply_advance(party_id, kind, dlg.allocations, user_id)
