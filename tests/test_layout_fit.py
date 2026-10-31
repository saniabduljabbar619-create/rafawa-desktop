"""Windows never outgrow the screen, screens scroll instead; table columns never clip a number."""
from PySide2.QtGui import QFontMetrics
from PySide2.QtWidgets import QHeaderView, QScrollArea, QTableWidget, QTableWidgetItem, QWidget


def _table(qapp, width):
    t = QTableWidget(2, 3)
    t.setHorizontalHeaderLabels(["Name", "Balance", "Notes"])
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)          # the old behaviour: equal columns
    t.resize(width, 200)
    texts = [["A very long customer name here", "1,234,567,890.00", "x"], ["Short", "9.00", "y" * 40]]
    for r, row in enumerate(texts):
        for c, v in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(v))
    t.show()
    qapp.processEvents()
    return t, texts


def test_autofit_narrows_text_columns_first_and_never_cuts_a_number(qapp):
    from shared.uix.widgets.table_fit import autofit
    t, texts = _table(qapp, 520)                                             # too narrow for everything
    autofit(t)
    qapp.processEvents()
    hh = t.horizontalHeader()
    fm = QFontMetrics(t.font())
    assert hh.sectionSize(1) >= fm.horizontalAdvance(texts[0][1])           # the balance keeps its full width
    assert sum(hh.sectionSize(c) for c in range(3)) <= t.viewport().width() + 2     # and the table fits: no sideways scroll
    assert not t.horizontalScrollBar().isVisible() or t.horizontalScrollBar().maximum() == 0


def test_a_table_refits_when_the_window_changes(qapp):
    from shared.uix.widgets.table_fit import autofit
    t, texts = _table(qapp, 520)
    autofit(t)
    narrow = t.horizontalHeader().sectionSize(0)
    t.resize(1600, 200)
    qapp.processEvents()
    qapp.processEvents()
    hh = t.horizontalHeader()
    assert sum(hh.sectionSize(c) for c in range(3)) >= t.viewport().width() - 2     # grew back to use the room
    assert max(hh.sectionSize(c) for c in range(3)) > narrow


def test_autofit_numbers_are_recognised_but_names_and_notes_are_not(qapp):
    from shared.uix.widgets.table_fit import _is_numeric_column
    t, _ = _table(qapp, 800)
    assert [_is_numeric_column(t, c) for c in range(3)] == [False, True, False]


def test_autofit_gives_spare_room_to_the_widest_column(qapp):
    from shared.uix.widgets.table_fit import autofit
    t, texts = _table(qapp, 1400)
    autofit(t)
    qapp.processEvents()
    hh = t.horizontalHeader()
    assert [hh.sectionResizeMode(c) for c in range(3)].count(QHeaderView.Stretch) == 1
    assert sum(hh.sectionSize(c) for c in range(3)) >= t.viewport().width() - 2     # no dead space on the right


def test_autofit_can_be_switched_off_for_a_table(qapp):
    from shared.uix.widgets.table_fit import autofit
    t = QTableWidget(1, 2)
    t.setProperty("autofit", False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    autofit(t)
    assert t.horizontalHeader().sectionResizeMode(0) == QHeaderView.Stretch


def test_a_screen_taller_than_the_window_scrolls_instead_of_stretching_the_window(qapp):
    from apps.product_management_app.main import _scrolled
    big = QWidget()
    big.setMinimumSize(3000, 2500)
    host = _scrolled(big)
    assert isinstance(host, QScrollArea) and host.widget() is big
    assert host.minimumSizeHint().width() < 1000 and host.minimumSizeHint().height() < 1000
