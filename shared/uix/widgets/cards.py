# shared/uix/widgets/cards.py
"""Small reusable building blocks for the redesigned screens: KPI cards, status chips,
initials avatars and a segmented selector. Styling is applied per widget (inline QSS)
so nothing here leaks onto the rest of the app."""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

# tone -> (background, text, border)
TONES = {
    "green": ("#ecfdf5", "#047857", "#a7f3d0"),
    "red": ("#fef2f2", "#b91c1c", "#fecaca"),
    "amber": ("#fffbeb", "#b45309", "#fde68a"),
    "blue": ("#eff6ff", "#1d4ed8", "#bfdbfe"),
    "grey": ("#f3f4f6", "#4b5563", "#e5e7eb"),
}

_AVATAR_COLORS = ["#0f766e", "#1d4ed8", "#7c3aed", "#b45309", "#be123c", "#0369a1", "#4d7c0f"]


def money(v) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def initials(name: str) -> str:
    parts = [p for p in (name or "").replace("-", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def chip(text: str, tone: str = "grey", parent: Optional[QWidget] = None) -> QLabel:
    bg, fg, border = TONES.get(tone, TONES["grey"])
    lab = QLabel(text, parent)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet(
        f"background:{bg}; color:{fg}; border:1px solid {border}; border-radius:10px;"
        "padding:2px 10px; font-size:11px; font-weight:600;"
    )
    return lab


def avatar(name: str, size: int = 36, parent: Optional[QWidget] = None, on_dark: bool = False) -> QLabel:
    lab = QLabel(initials(name), parent)
    lab.setFixedSize(size, size)
    lab.setAlignment(Qt.AlignCenter)
    if on_dark:
        bg, fg = "rgba(255,255,255,0.16)", "#ffffff"
    else:
        bg, fg = _AVATAR_COLORS[sum(map(ord, name or "?")) % len(_AVATAR_COLORS)], "#ffffff"
    lab.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:{size // 2}px; "
        f"font-size:{max(size // 3, 10)}px; font-weight:700;"
    )
    return lab


class KpiCard(QFrame):
    """A white card: small caption, big value, optional hint line, coloured accent edge."""

    def __init__(self, caption: str, value: str = "-", hint: str = "", tone: str = "green",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        _bg, fg, _b = TONES.get(tone, TONES["green"])
        self.setObjectName("KpiCard")
        self.setStyleSheet(
            "QFrame#KpiCard { background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; "
            f"border-left:4px solid {fg}; }}"
            "QFrame#KpiCard QLabel { background:transparent; border:none; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        self.caption = QLabel(caption.upper())
        self.caption.setStyleSheet("color:#6b7280; font-size:10px; font-weight:700; letter-spacing:1px;")
        self.value = QLabel(value)
        self.value.setStyleSheet(f"color:{fg}; font-size:22px; font-weight:700;")
        self.hint = QLabel(hint)
        self.hint.setStyleSheet("color:#9ca3af; font-size:11px;")
        lay.addWidget(self.caption)
        lay.addWidget(self.value)
        lay.addWidget(self.hint)

    def set(self, value: str, hint: Optional[str] = None) -> None:
        self.value.setText(value)
        if hint is not None:
            self.hint.setText(hint)


class Segmented(QWidget):
    """Pill-shaped single-choice selector. Emits the selected key."""
    changed = Signal(str)

    def __init__(self, options: Iterable[Tuple[str, str]], parent: Optional[QWidget] = None,
                 default_first: bool = True):
        super().__init__(parent)
        self.setStyleSheet(
            "QPushButton { background:#ffffff; color:#374151; border:1px solid #d1d5db; padding:6px 16px; "
            "font-weight:600; border-radius:0; }"
            "QPushButton:hover { background:#f3f4f6; }"
            "QPushButton:checked { background:#0f766e; color:#ffffff; border-color:#0f766e; }"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[QPushButton] = []
        self._keys: List[str] = []
        for key, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("role", "plain")        # opt out of the auto button polisher
            self._group.addButton(b)
            lay.addWidget(b)
            self._buttons.append(b)
            self._keys.append(key)
            b.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))
        if self._buttons and default_first:
            self._buttons[0].setChecked(True)

    def clear(self) -> None:
        """Show no selection (an exclusive group needs the flag lifted to uncheck)."""
        self._group.setExclusive(False)
        for b in self._buttons:
            b.setChecked(False)
        self._group.setExclusive(True)

    def set_key(self, key: str) -> None:
        if key in self._keys:
            self._buttons[self._keys.index(key)].setChecked(True)

    def key(self) -> str:
        for k, b in zip(self._keys, self._buttons):
            if b.isChecked():
                return k
        return self._keys[0] if self._keys else ""


_TABLE_QSS = """
QTableWidget { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px; gridline-color:transparent;
    alternate-background-color:#ffffff; outline:0; }
QTableWidget::item { padding:4px 8px; border-bottom:1px solid #f1f5f9; }
QTableWidget::item:selected { background:#ecfdf5; color:#065f46; }
QTableWidget::item:hover { background:#f8fafc; }
QHeaderView::section { background:#f8fafc; color:#6b7280; border:none; border-bottom:1px solid #e5e7eb;
    padding:8px; font-size:11px; font-weight:700; }
"""


def style_table(table, row_height: int = 40) -> None:
    """Clean card-like look for a QTableWidget (no grid, light header, soft selection)."""
    table.setStyleSheet(_TABLE_QSS)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setFocusPolicy(Qt.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
