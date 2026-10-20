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


def test_autofit_never_squeezes_a_column_below_its_text(qapp):
    from shared.uix.widgets.table_fit import autofit
    t, texts = _table(qapp, 300)                                             # far too narrow: scrolls, never clips
    autofit(t)
    qapp.processEvents()
    fm = QFontMetrics(t.font())
    for c in range(3):
        assert t.horizontalHeader().sectionResizeMode(c) == QHeaderView.ResizeToContents
        assert t.horizontalHeader().sectionSize(c) >= max(fm.horizontalAdvance(texts[0][c]),
                                                          fm.horizontalAdvance(texts[1][c]))


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
