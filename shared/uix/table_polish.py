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
from PySide2.QtWidgets import QAbstractItemView, QHeaderView, QPushButton, QTableView

_FLAG = "_rafawa_table_polished"

# Button wording -> visual role. Matched on the first word of the label, so
# "Add Customer", "Save", "Delete Selected", "Void Payment" all read correctly.
_PRIMARY_WORDS = {"add", "save", "create", "new", "finalize", "confirm", "record", "submit", "apply"}
_DANGER_WORDS = {"delete", "remove", "void", "revert", "clear"}


def polish_button(btn: QPushButton) -> bool:
    """Give an unstyled button a primary/danger look from its label. Returns True if changed."""
    if btn.property("role") is not None or btn.objectName() or btn.styleSheet():
        return False                     # the screen styled it on purpose
    words = btn.text().replace("&", "").replace("\u2190", "").strip().lower().split()
    if not words:
        return False
    role = "danger" if words[0] in _DANGER_WORDS else "primary" if words[0] in _PRIMARY_WORDS else None
    if role is None:
        return False
    btn.setProperty("role", role)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    btn.updateGeometry()                 # padding/borders changed -> re-measure in its layout
    return True


def _is_untouched(table: QTableView) -> bool:
    header = table.horizontalHeader()
    if header.count() == 0:
        return False
    # setStretchLastSection(True) is the common "I did nothing else" idiom (it makes
    # one fat last column), so it does not count as deliberate configuration.
    # Nobody sized columns individually when every section (bar the stretched last
    # one) has the same width - whether that is Qt's default or a uniform width
    # it settled on from the header text.
    last = header.count() - 1
    widths = set()
    for i in range(header.count()):
        if header.sectionResizeMode(i) != QHeaderView.Interactive:
            return False
        if i != last or not header.stretchLastSection():
            widths.add(header.sectionSize(i))
    return len(widths) <= 1


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
        if event.type() == QEvent.Show:
            if isinstance(obj, QTableView):
                polish_table(obj)
            elif isinstance(obj, QPushButton):
                polish_button(obj)
        return False
