"""A toolbar that wraps instead of widening the page."""
from PySide2.QtWidgets import QPushButton, QVBoxLayout, QWidget

from shared.uix.layout.flow import FlowLayout


def _bar(n=8, text="A fairly long button"):
    w = QWidget()
    lay = FlowLayout(w)
    for i in range(n):
        lay.addWidget(QPushButton(f"{text} {i}"))
    return w, lay


def test_wide_window_keeps_one_line_and_narrow_window_wraps(qapp):
    w, _lay = _bar()
    w.resize(2000, 300)
    w.show()
    qapp.processEvents()
    ys = {b.y() for b in w.findChildren(QPushButton)}
    assert len(ys) == 1
    w.resize(500, 300)
    qapp.processEvents()
    ys = {b.y() for b in w.findChildren(QPushButton)}
    assert len(ys) > 1
    assert all(b.x() + b.width() <= 500 for b in w.findChildren(QPushButton))


def test_a_flow_row_never_demands_the_full_row_width(qapp):
    w, lay = _bar()
    assert lay.minimumSize().width() < 250                     # one button, not the sum of eight
    assert w.minimumSizeHint().width() < 250


def test_height_grows_when_it_wraps(qapp):
    w, lay = _bar()
    assert lay.heightForWidth(300) > lay.heightForWidth(3000)


def test_inside_a_page_layout_the_page_height_follows(qapp):
    page = QWidget()
    outer = QVBoxLayout(page)
    bar, _ = _bar()
    outer.addWidget(bar)
    page.resize(400, 100)
    page.show()
    qapp.processEvents()
    assert bar.height() > 40                                   # several lines, all visible
    assert all(b.isVisible() for b in bar.findChildren(QPushButton))


def test_hidden_widgets_take_no_room(qapp):
    w, lay = _bar(3)
    w.findChildren(QPushButton)[1].hide()
    w.resize(2000, 100)
    w.show()
    qapp.processEvents()
    visible = [b for b in w.findChildren(QPushButton) if b.isVisible()]
    assert len(visible) == 2 and visible[1].x() == visible[0].x() + visible[0].width() + 8


def test_responsive_row_shares_width_wide_and_wraps_narrow(qapp):
    from PySide2.QtWidgets import QLabel
    from shared.uix.layout.flow import ResponsiveRow
    w = QWidget()
    lay = ResponsiveRow(w, spacing=10)
    for i in range(4):
        lab = QLabel("A card with a fairly long caption")
        lab.setMinimumWidth(240)
        lay.addWidget(lab)
    assert w.minimumSizeHint().width() < 300                      # one card, not four
    w.resize(1100, 200)
    w.show()
    qapp.processEvents()
    labs = w.findChildren(QLabel)
    assert len({l.y() for l in labs}) == 1 and abs(labs[0].width() - labs[1].width()) <= 1     # one line, equal cards
    assert labs[-1].x() + labs[-1].width() <= 1100
    w.resize(560, 300)
    qapp.processEvents()
    assert len({l.y() for l in labs}) == 2                        # two columns, two lines
    assert all(l.x() + l.width() <= 560 for l in labs)
