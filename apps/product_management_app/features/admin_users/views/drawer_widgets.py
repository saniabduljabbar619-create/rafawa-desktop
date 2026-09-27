# apps/product_management_app/features/admin_users/views/drawer_widgets.py
# -*- coding: utf-8 -*-
"""
Painted pieces for the drawer screen.

Built for an operator who may not read fluently: big numbers, one colour per
direction (green = money came IN, red = money went OUT), physical verbs only.
Never the words "owe" / "owed" - the engine already hands us the sentence.
"""
from __future__ import annotations

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QFrame, QLabel, QHBoxLayout, QVBoxLayout, QPushButton
)

NAIRA = "\u20a6"

GREEN = "#1a7f37"
RED = "#c02b2b"
GREY = "#6b7280"
INK = "#111827"
LINE = "#e5e7eb"


def money(v) -> str:
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0
    return f"{NAIRA}{v:,.0f}"


class MoneyCard(QFrame):
    """One big number + plain-language caption. The headline zone.

    Clickable: emits `clicked` so the screen can show the full list of
    transactions behind this figure - the whole point of a reconciliation
    screen is being able to drill from a number down to what actually
    produced it, not just trust the total."""

    clicked = Signal()

    def __init__(self, caption: str = "", amount: float = 0.0,
                 color: str = INK, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid " + LINE +
            "; border-radius: 10px; }"
            "QFrame:hover { border: 1px solid " + INK + "; background: #fafafa; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)
        self.cap = QLabel(caption)
        self.cap.setStyleSheet(f"color: {GREY}; font-size: 12px; border: none;")
        self.val = QLabel(money(amount))
        self.val.setStyleSheet(
            f"color: {color}; font-size: 26px; font-weight: 900; border: none;")
        lay.addWidget(self.cap)
        lay.addWidget(self.val)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_values(self, caption: str, amount: float, color: str = None):
        self.cap.setText(caption)
        self.val.setText(money(amount))
        if color:
            self.val.setStyleSheet(
                f"color: {color}; font-size: 26px; font-weight: 900; border: none;")


class StatCard(QFrame):
    """Compact card for the 10 informational figures. Clickable, same as
    MoneyCard - drills down to the transactions behind this specific
    figure."""

    clicked = Signal()

    def __init__(self, caption: str = "", amount: float = 0.0, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame { background: #f9fafb; border: 1px solid " + LINE +
            "; border-radius: 8px; }"
            "QFrame:hover { border: 1px solid " + INK + "; background: #f3f4f6; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(1)
        self.cap = QLabel(caption)
        self.cap.setWordWrap(True)
        self.cap.setStyleSheet(f"color: {GREY}; font-size: 10px; border: none;")
        self.val = QLabel(money(amount))
        self.val.setStyleSheet(
            f"color: {INK}; font-size: 15px; font-weight: 800; border: none;")
        lay.addWidget(self.cap)
        lay.addWidget(self.val)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def set_amount(self, amount: float):
        self.val.setText(money(amount))


class VerdictRow(QFrame):
    """
    One story-sentence + a Settle button.

    The sentence is NOT built here - DrawerService already returns it in both
    languages (verdict["en"] / verdict["ha"]). This widget only paints.
    """
    settle_clicked = Signal(int)   # other_user_id

    def __init__(self, verdict: dict, lang: str = "en",
                 can_settle: bool = True, parent=None):
        super().__init__(parent)
        self.v = dict(verdict or {})
        collect = (self.v.get("action") == "collect")
        color = GREEN if collect else RED
        tint = "#f0fdf4" if collect else "#fef2f2"

        self.setStyleSheet(
            "QFrame { background: " + tint + "; border: 1px solid " + LINE +
            "; border-radius: 10px; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        arrow = QLabel("\u2190" if collect else "\u2192")
        arrow.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: 900; border: none;")
        lay.addWidget(arrow)

        self.text = QLabel(self.v.get(lang) or self.v.get("en") or "")
        self.text.setStyleSheet(
            f"color: {color}; font-size: 17px; font-weight: 800; border: none;")
        lay.addWidget(self.text, 1)

        self.btn = QPushButton("Settle")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(
            "QPushButton { background: " + color + "; color: #ffffff;"
            " border: none; border-radius: 8px; padding: 8px 18px;"
            " font-weight: 800; }"
            "QPushButton:disabled { background: " + GREY + "; }"
        )
        self.btn.setEnabled(bool(can_settle))
        if not can_settle:
            self.btn.setToolTip("Only an admin can confirm a settlement.")
        self.btn.clicked.connect(self._emit)
        lay.addWidget(self.btn)

    def set_lang(self, lang: str):
        self.text.setText(self.v.get(lang) or self.v.get("en") or "")
        self.btn.setText("Settle" if lang == "en" else "Daidaita")

    def _emit(self):
        self.settle_clicked.emit(int(self.v.get("other_user_id") or 0))


class Banner(QLabel):
    """The one-line verdict on the whole drawer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)

    def paint(self, text: str, balanced: bool):
        bg = "#ecfdf5" if balanced else "#fff7ed"
        fg = GREEN if balanced else "#9a3412"
        self.setText(text)
        self.setStyleSheet(
            "QLabel { background: " + bg + "; color: " + fg +
            "; border-radius: 10px; padding: 14px 16px;"
            " font-size: 18px; font-weight: 900; }"
        )