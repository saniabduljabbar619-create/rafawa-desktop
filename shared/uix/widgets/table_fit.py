# shared/uix/widgets/table_fit.py
# -*- coding: utf-8 -*-
"""
Make a table's columns fit what is in them AND fit the window - so a balance or a name is never cut off in the
middle of a digit, and a table never needs a sideways scrollbar.

Every column takes the width of its widest text. If everything fits, spare room goes to the widest-text column.
If it does not fit, the TEXT columns (names, notes, products) are narrowed first - their text is shortened with "..."
and the full text stays in the tooltip - while the NUMBER columns (amounts, weights, dates) keep their full width, so
no digit is ever cut. Only when even that is not enough are all columns narrowed together. The fit is redone whenever
the table is resized. Applied app-wide when a table is first shown (see main._FitTables); a table can opt out with
`table.setProperty("autofit", False)`.
"""
from __future__ import annotations

import re

from PySide2.QtCore import QEvent, QObject, Qt
from PySide2.QtWidgets import QAbstractItemView, QHeaderView, QTableView

_MAX_STRETCH_PRECISION = 200
_MIN_TEXT_COL = 90          # a text column is narrowed to this before any number column is touched
_MIN_ANY_COL = 56           # the last resort floor
_NUMBERISH = re.compile(r"^[\s₦N$+\-(]*[\d][\d,]*(\.\d+)?[\s)%]*(kg|bags?|bg)?\s*$|^\d{1,4}[-/ .]\w{1,9}[-/ .]\d{2,4}.*$",
                        re.IGNORECASE)


def _is_numeric_column(table: QTableView, col: int, sample: int = 30) -> bool:
    model = table.model()
    seen = hits = 0
    for r in range(min(model.rowCount(), sample)):
        v = model.index(r, col).data(Qt.DisplayRole)
        text = ("" if v is None else str(v)).strip()
        if not text:
            continue
        seen += 1
        hits += 1 if _NUMBERISH.match(text) else 0
    return seen > 0 and hits / seen >= 0.7


def _fit(table: QTableView) -> None:
    hh = table.horizontalHeader()
    model = table.model()
    if hh is None or model is None or model.columnCount() == 0:
        return
    cols = [c for c in range(model.columnCount()) if not table.isColumnHidden(c)]
    if not cols:
        return
    hh.setResizeContentsPrecision(_MAX_STRETCH_PRECISION)
    hh.setStretchLastSection(False)
    fixed = [c for c in cols if hh.sectionResizeMode(c) == QHeaderView.Fixed]   # a deliberately fixed column stays
    flex = [c for c in cols if c not in fixed]
    # natural widths: what each column needs for its widest text (measured once, with the content-sized mode)
    for c in flex:
        if hh.sectionResizeMode(c) != QHeaderView.ResizeToContents:
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    hints = {c: max(table.sizeHintForColumn(c), hh.sectionSizeHint(c)) for c in flex}
    avail = table.viewport().width() - sum(hh.sectionSize(c) for c in fixed) - 6
    total = sum(hints.values())
    hh.setMinimumSectionSize(_MIN_ANY_COL)

    if total <= avail:
        # everything fits: content-sized columns, the widest one takes the spare room
        if flex and avail - total > 0:
            widest = max(flex, key=lambda c: hints[c])
            hh.setSectionResizeMode(widest, QHeaderView.Stretch)
        return

    # too wide for the window: narrow the text columns first, numbers last
    excess = total - avail
    numeric = {c for c in flex if _is_numeric_column(table, c)}
    widths = dict(hints)
    for group, floor in (([c for c in flex if c not in numeric], _MIN_TEXT_COL), (flex, _MIN_ANY_COL)):
        room = {c: widths[c] - floor for c in group if widths[c] > floor}
        capacity = sum(room.values())
        if capacity <= 0:
            continue
        cut = min(excess, capacity)
        for c, r in room.items():
            widths[c] -= cut * r / capacity
        excess -= cut
        if excess <= 0.5:
            break
    for c in flex:
        hh.setSectionResizeMode(c, QHeaderView.Interactive)
        hh.resizeSection(c, max(int(widths[c]), _MIN_ANY_COL))


class _Refit(QObject):
    """Re-fit a table when its window is resized (the fit depends on the width it has)."""

    def __init__(self, table: QTableView):
        super().__init__(table)
        self.table = table
        self.last = table.viewport().width()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self.table.viewport():
            w = self.table.viewport().width()
            if abs(w - self.last) > 2 and self.table.property("autofit") is not False:
                self.last = w
                try:
                    _fit(self.table)
                except Exception:
                    pass
        return False


def autofit(table: QTableView) -> None:
    if not isinstance(table, QTableView) or table.property("autofit") is False:
        return
    try:
        _fit(table)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setTextElideMode(Qt.ElideRight)
        if not table.property("autofit_refit"):
            table.viewport().installEventFilter(_Refit(table))
            table.setProperty("autofit_refit", True)
        table.setProperty("autofit_done", True)
    except Exception:
        pass
