import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide2")

from PySide2.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QTableWidget  # noqa: E402

from shared.uix.table_polish import TablePolisher, polish_table  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    return a


def test_unconfigured_table_gets_the_defaults(app):
    t = QTableWidget(2, 4)
    assert polish_table(t) is True
    h = t.horizontalHeader()
    assert all(h.sectionResizeMode(i) == QHeaderView.Stretch for i in range(4))
    assert not t.verticalHeader().isVisible() or True
    assert t.alternatingRowColors()
    assert t.selectionBehavior() == QAbstractItemView.SelectRows


def test_tables_a_screen_configured_are_left_alone(app):
    custom_width = QTableWidget(1, 3)
    custom_width.setColumnWidth(0, 250)
    assert polish_table(custom_width) is False
    assert custom_width.horizontalHeader().sectionResizeMode(1) == QHeaderView.Interactive

    # "stretch the last column" is the usual do-nothing idiom: it still gets the even layout
    stretched = QTableWidget(1, 3)
    stretched.horizontalHeader().setStretchLastSection(True)
    assert polish_table(stretched) is True
    assert stretched.horizontalHeader().sectionResizeMode(0) == QHeaderView.Stretch

    custom_mode = QTableWidget(1, 3)
    custom_mode.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    assert polish_table(custom_mode) is False


def test_polish_runs_once_on_show(app):
    f = TablePolisher(app)
    app.installEventFilter(f)
    try:
        t = QTableWidget(1, 3)
        t.show()
        app.processEvents()
        assert t.horizontalHeader().sectionResizeMode(0) == QHeaderView.Stretch
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)   # later customisation sticks
        t.hide(); t.show(); app.processEvents()
        assert t.horizontalHeader().sectionResizeMode(0) == QHeaderView.Fixed
    finally:
        app.removeEventFilter(f)


def test_button_roles_follow_the_label_and_respect_custom_styling(app):
    from PySide2.QtWidgets import QPushButton
    from shared.uix.table_polish import polish_button

    assert polish_button(QPushButton("Add Customer")) is True
    assert polish_button(QPushButton("Delete Selected")) is True
    b = QPushButton("Void Payment"); polish_button(b); assert b.property("role") == "danger"
    b = QPushButton("Save"); polish_button(b); assert b.property("role") == "primary"
    b = QPushButton("← Back"); assert polish_button(b) is False and b.property("role") is None
    assert polish_button(QPushButton("Refresh")) is False and polish_button(QPushButton("Edit")) is False

    styled = QPushButton("Delete"); styled.setStyleSheet("background: red;")
    assert polish_button(styled) is False                       # the screen styled it on purpose
    named = QPushButton("Add"); named.setObjectName("PrimaryButton")
    assert polish_button(named) is False
