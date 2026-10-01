"""
App-wide table defaults, applied the first time a table is shown.

Many screens create plain QTableWidgets and never configure them, which looks
inconsistent (one fat column, others cramped, clipped headers behind a
horizontal scrollbar, row numbers nobody asked for). This gives every
UNCONFIGURED table the same professional behaviour:

  * columns share the width instead of overflowing sideways
  * row numbers hidden, zebra rows on, whole-row selection, read-only
  * non-wrapping text with "..." instead of a broken layout

A table that a screen already set up on purpose (custom column widths or
resize modes) is left exactly as the screen made it.
"""
from __future__ import annotations

from PySide2.QtCore import QEvent, QObject, Qt
from PySide2.QtWidgets import QAbstractItemView, QHeaderView, QTableView

_FLAG = "_rafawa_table_polished"


def _is_untouched(table: QTableView) -> bool:
    header = table.horizontalHeader()
    if header.count() == 0:
        return False
    # setStretchLastSection(True) is the common "I did nothing else" idiom (it makes
    # one fat last column), so it does not count as deliberate configuration.
    default = header.defaultSectionSize()
    for i in range(header.count()):
        if header.sectionResizeMode(i) != QHeaderView.Interactive:
            return False
        if header.sectionSize(i) != default:
            return False
    return True


def polish_table(table: QTableView) -> bool:
    """Apply the defaults if the table is unconfigured. Returns True if changed."""
    if table.property(_FLAG):
        return False
    table.setProperty(_FLAG, True)
    if not _is_untouched(table):
        return False
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideRight)
    if table.selectionBehavior() == QAbstractItemView.SelectItems:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
    return True


class TablePolisher(QObject):
    """Install once: ``app.installEventFilter(TablePolisher(app))``."""

    def eventFilter(self, obj, event):          # noqa: N802 (Qt naming)
        if event.type() == QEvent.Show and isinstance(obj, QTableView):
            polish_table(obj)
        return False
