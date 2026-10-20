# shared/uix/widgets/table_fit.py
# -*- coding: utf-8 -*-
"""
Make a table's columns fit what is in them, so a balance or a name is never cut off in the middle of a digit.

Columns used to be either "stretch" (all equal, so a 12-digit amount was clipped in a narrow one) or a fixed
default width. Now every column takes the width of its widest text, and any spare room goes to the widest-text column. If the window is genuinely narrower than the table, the table scrolls sideways instead of
hiding digits. Applied app-wide when a table is first shown (see main._FitTables); a table can opt out with
`table.setProperty("autofit", False)`.
"""
from __future__ import annotations

from PySide2.QtWidgets import QAbstractItemView, QHeaderView, QTableView

_MAX_STRETCH_PRECISION = 200


def autofit(table: QTableView) -> None:
    if not isinstance(table, QTableView) or table.property("autofit") is False:
        return
    hh = table.horizontalHeader()
    model = table.model()
    if hh is None or model is None or model.columnCount() == 0:
        return
    cols = [c for c in range(model.columnCount()) if not table.isColumnHidden(c)]
    if not cols:
        return
    try:
        hh.setResizeContentsPrecision(_MAX_STRETCH_PRECISION)
        hh.setStretchLastSection(False)
        hints = {c: max(table.sizeHintForColumn(c), hh.sectionSizeHint(c)) for c in cols}
        for c in cols:
            if hh.sectionResizeMode(c) != QHeaderView.Fixed:          # a deliberately fixed column (e.g. ID) stays
                hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        # spare room goes to the widest column, but only when everything fits - otherwise the table scrolls
        # sideways rather than squeezing any column (a squeezed column is what cut digits off)
        free = table.viewport().width() - sum(hints.values()) - 8
        widest = max(cols, key=lambda c: hints[c])
        if free > 0 and hh.sectionResizeMode(widest) != QHeaderView.Fixed:
            hh.setSectionResizeMode(widest, QHeaderView.Stretch)
        hh.setMinimumSectionSize(max(hh.minimumSectionSize(), 60))
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setProperty("autofit_done", True)
    except Exception:
        pass
