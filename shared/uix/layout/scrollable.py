# shared/uix/layout/scrollable.py
# -*- coding: utf-8 -*-
"""
One shared way to make a screen's content scrollable, instead of every
screen hand-rolling its own QScrollArea wiring. Proven first on the Cash
Drawer screen (pinned header + scrollable body); this factors that exact
pattern out so every other screen in the app can reuse it verbatim rather
than risk a slightly-different, unverified attempt per file.
"""
from __future__ import annotations

from PySide2.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame


def make_scrollable_page(widget: QWidget, margins=(16, 16, 16, 16),
                         spacing: int = 12) -> QVBoxLayout:
    """
    Call this INSTEAD of `QVBoxLayout(widget)` at the top of a screen's
    __init__. Returns the inner layout to build the page's content onto -
    used exactly like a normal top-level QVBoxLayout, just scrollable:

        from shared.uix.layout.scrollable import make_scrollable_page
        root = make_scrollable_page(self)
        root.addWidget(QLabel("Title"))
        ...

    Do not ALSO call QVBoxLayout(widget) afterward - this already claims
    that spot. The widget's real top-level layout becomes a single
    QScrollArea; everything you add via the returned layout lives inside
    the scrollable content widget.
    """
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    # Transparent so whatever background the screen sets on itself (e.g.
    # self.setStyleSheet("background-color: #f8f9fa;")) shows through the
    # scroll viewport naturally, instead of the viewport's own default
    # background masking it - a real visual-mismatch risk this avoids.
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    content = QWidget()
    # NOTE: do NOT call content.setStyleSheet(...) here. A plain QWidget
    # with no local style sheet and no WA_StyledBackground already paints
    # no background of its own (the parent's background shows through),
    # which is exactly the "transparent" effect we want. Calling
    # setStyleSheet() on this widget - even with an innocuous
    # "background: transparent;" - pulls its entire subtree into a
    # separate local style-sheet scope, and Qt's cascade then silently
    # drops the app-wide QPushButton#PrimaryButton background (while
    # still applying its color: #ffffff), making every primary button
    # placed inside any scrollable page render as invisible white text
    # on no fill. Confirmed via isolated repro - see chat.
    inner = QVBoxLayout(content)
    inner.setContentsMargins(*margins)
    inner.setSpacing(spacing)

    scroll.setWidget(content)
    outer.addWidget(scroll)

    return inner


def make_scrollable_dialog(widget: QWidget, margins=(12, 12, 12, 12),
                           spacing: int = 10):
    """
    For dialogs whose content can genuinely grow without bound (e.g. a sale
    checkout grid that gets one row per product added) while the action
    buttons (Confirm/Cancel) must ALWAYS stay reachable, no matter how tall
    the content gets. Returns (content_layout, footer_layout):
    content_layout is the scrollable region - put everything except the
    final button row here; footer_layout sits OUTSIDE the scroll area,
    pinned at the bottom, and never scrolls away.

        from shared.uix.layout.scrollable import make_scrollable_dialog
        root, footer = make_scrollable_dialog(self)
        root.addWidget(self.grid)          # can grow, scrolls if needed
        footer.addWidget(self.btn_cancel)  # always visible
        footer.addWidget(self.btn_ok)      # always visible, never hidden
                                            # off the bottom of the screen
    """
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(*margins)
    outer.setSpacing(spacing)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    content = QWidget()
    # NOTE: do NOT call content.setStyleSheet(...) here. A plain QWidget
    # with no local style sheet and no WA_StyledBackground already paints
    # no background of its own (the parent's background shows through),
    # which is exactly the "transparent" effect we want. Calling
    # setStyleSheet() on this widget - even with an innocuous
    # "background: transparent;" - pulls its entire subtree into a
    # separate local style-sheet scope, and Qt's cascade then silently
    # drops the app-wide QPushButton#PrimaryButton background (while
    # still applying its color: #ffffff), making every primary button
    # placed inside any scrollable page render as invisible white text
    # on no fill. Confirmed via isolated repro - see chat.
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(spacing)

    scroll.setWidget(content)
    outer.addWidget(scroll, 1)

    footer_layout = QHBoxLayout()
    outer.addLayout(footer_layout)

    return content_layout, footer_layout


def make_scrollable_page_with_header(widget: QWidget, margins=(16, 16, 16, 16),
                                     spacing: int = 12):
    """
    Same as make_scrollable_page, but for screens that need something
    pinned at the top (a toolbar, a date filter, an action-button row) that
    should stay visible while the rest of the page scrolls - matching the
    Cash Drawer screen's own header. Returns (header_layout, content_layout):
    header_layout sits OUTSIDE the scroll area and never scrolls;
    content_layout is the scrollable region, same as make_scrollable_page.

        from shared.uix.layout.scrollable import make_scrollable_page_with_header
        header, root = make_scrollable_page_with_header(self)
        header.addWidget(self.btn_refresh)   # stays pinned
        root.addWidget(self.big_table)        # scrolls
    """
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(*margins)
    outer.setSpacing(spacing)

    header_layout = QVBoxLayout()
    outer.addLayout(header_layout)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    content = QWidget()
    # NOTE: do NOT call content.setStyleSheet(...) here. A plain QWidget
    # with no local style sheet and no WA_StyledBackground already paints
    # no background of its own (the parent's background shows through),
    # which is exactly the "transparent" effect we want. Calling
    # setStyleSheet() on this widget - even with an innocuous
    # "background: transparent;" - pulls its entire subtree into a
    # separate local style-sheet scope, and Qt's cascade then silently
    # drops the app-wide QPushButton#PrimaryButton background (while
    # still applying its color: #ffffff), making every primary button
    # placed inside any scrollable page render as invisible white text
    # on no fill. Confirmed via isolated repro - see chat.
    inner = QVBoxLayout(content)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(spacing)

    scroll.setWidget(content)
    outer.addWidget(scroll, 1)

    return header_layout, inner