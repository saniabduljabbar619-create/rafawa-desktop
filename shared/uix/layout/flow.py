# shared/uix/layout/flow.py
# -*- coding: utf-8 -*-
"""
FlowLayout - a row of widgets that wraps onto the next line when the window is too narrow, instead of forcing the
whole page to scroll sideways. Use it for toolbars (a filter, then a handful of buttons): on a wide screen it looks
exactly like a QHBoxLayout, on a small one the buttons simply drop to a second line.
"""
from __future__ import annotations

from PySide2.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide2.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 8, v_spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._h, self._v = h_spacing, v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # ---- QLayout API
    def addItem(self, item) -> None:
        self._items.append(item)

    def addWidget(self, widget, *_args) -> None:           # accepts (and ignores) a stretch / alignment like the box layouts
        super().addWidget(widget)

    def addStretch(self, *_args) -> None:                  # a flow row has no stretch; kept so box-layout code ports as is
        pass

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """As wide as the widest single item (everything else can wrap), so a flow row never forces a page wider."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ---- the wrapping itself
    def _layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            w = min(hint.width(), max(area.width(), 1))
            if x + w > area.right() + 1 and line_h > 0:           # no room: next line
                x = area.x()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, hint.height())))
            x += w + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ResponsiveRow(QLayout):
    """
    A row of cards that share the width equally on a wide window and drop into fewer columns (more lines) on a narrow
    one - instead of demanding the sum of all their minimum widths. Each item needs at least `min_item_width`
    (default: the widest item's own minimum), so the number of columns is however many of those fit.
    """

    def __init__(self, parent=None, spacing: int = 10, min_item_width: int = 0):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self._min_w = int(min_item_width)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def addWidget(self, widget, *_args) -> None:
        super().addWidget(widget)

    def setSpacing(self, spacing: int) -> None:
        self._gap = int(spacing)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Horizontal

    def hasHeightForWidth(self) -> bool:
        return True

    def _shown(self):
        return [i for i in self._items if not (i.widget() is not None and i.widget().isHidden())]

    def _item_min(self) -> int:
        return max([self._min_w] + [i.minimumSize().width() for i in self._shown()]) if self._shown() else 0

    def _plan(self, width: int):
        items = self._shown()
        if not items:
            return 1, max(width, 1), 0
        cols = max(1, min(len(items), (width + self._gap) // (self._item_min() + self._gap)))
        cell = max((width - (cols - 1) * self._gap) // cols, 1)
        return cols, cell, len(items)

    def heightForWidth(self, width: int) -> int:
        m = self.contentsMargins()
        cols, cell, n = self._plan(width - m.left() - m.right())
        if not n:
            return m.top() + m.bottom()
        items = self._shown()
        rows = [items[i:i + cols] for i in range(0, n, cols)]
        h = sum(max(it.sizeHint().height() for it in row) for row in rows) + (len(rows) - 1) * self._gap
        return h + m.top() + m.bottom()

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        cols, cell, n = self._plan(area.width())
        items = self._shown()
        y = area.y()
        for start in range(0, n, cols):
            row = items[start:start + cols]
            h = max(it.sizeHint().height() for it in row)
            for k, it in enumerate(row):
                it.setGeometry(QRect(area.x() + k * (cell + self._gap), y, cell, h))
            y += h + self._gap

    def sizeHint(self) -> QSize:
        items = self._shown()
        w = sum(i.sizeHint().width() for i in items) + max(len(items) - 1, 0) * self._gap
        h = max([i.sizeHint().height() for i in items] or [0])
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        m = self.contentsMargins()
        h = max([i.minimumSize().height() for i in self._shown()] or [0])
        return QSize(self._item_min() + m.left() + m.right(), h + m.top() + m.bottom())
