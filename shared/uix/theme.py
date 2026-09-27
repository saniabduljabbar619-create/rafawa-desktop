# shared/uix/theme.py
# -*- coding: utf-8 -*-

APP_STYLESHEET = """
/* Global */
QMainWindow { background: #f6f7fb; }
QWidget { font-size: 12px; color: #111827; font-family: Segoe UI, Arial; }

/* TopBar */
QWidget#TopBar {
    background: #ffffff;
    border-bottom: 1px solid #e5e7eb;
}
QWidget#TopBar QLabel {
    font-size: 14px;
    font-weight: 700;
}
QWidget#TopBar QPushButton {
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    padding: 5px 10px;
    border-radius: 8px;
}
QWidget#TopBar QPushButton:hover { background: #e5e7eb; }

/* Sidebar */
QWidget#Sidebar {
    background: #0b2a24;
}

QLabel#SidebarHeader {
    color: #ffffff;
    font-size: 13px;
    font-weight: 800;
    padding: 6px 2px;
}

QWidget#Sidebar QPushButton {
    background: transparent;
    color: #ffffff;
    border: none;
    padding: 10px 12px;
    border-radius: 10px;
    text-align: left;
    font-weight: 700;
    font-size: 12px;
}

QWidget#Sidebar QPushButton:hover {
    background: rgba(255, 255, 255, 0.22);
    border-left: 4px solid rgba(255, 255, 255, 0.55);
    padding-left: 8px;
}

QWidget#Sidebar QPushButton:pressed {
    background: rgba(255, 255, 255, 0.30);
}

QWidget#Sidebar QPushButton:checked {
    background: rgba(255, 255, 255, 0.16);
    border-left: 4px solid #34d399;
    padding-left: 8px;
}

/* Hovering the ALREADY-current page: stay on the green "you are here"
   marker (don't let the plain white hover border override it), just
   brighten the background a bit so hover still visibly registers. */
QWidget#Sidebar QPushButton:checked:hover {
    background: rgba(255, 255, 255, 0.24);
    border-left: 4px solid #34d399;
    padding-left: 8px;
}

/* Screen titles (e.g. "Sales Console") - a clear anchor, without towering
   over the rest of the screen. */
QLabel#Title {
    font-size: 16px;
    font-weight: 800;
    color: #0b2a24;
}

/* Card - a light panel used to visually group related fields, ERP-style.
   Give any QFrame this objectName to wrap a logical section (Customer,
   Product, etc.) in a bordered panel instead of loose floating labels. */
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
}

/* Small section headers INSIDE a card (e.g. "Customer", "Product") -
   muted, bold, slightly spaced - a common ERP convention for labeling
   a group of fields without it competing visually with the screen title. */
QLabel#SectionLabel {
    font-size: 11px;
    font-weight: 800;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding-top: 3px;
}

/* Primary buttons (inside screens) - only when explicitly marked */
QPushButton#PrimaryButton {
    background: #1f8f6b;
    color: #ffffff;
    border: none;
    padding: 7px 14px;
    border-radius: 10px;
    min-height: 30px;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover { background: #177a5b; }
QPushButton#PrimaryButton:pressed { background: #136a50; }
QPushButton#PrimaryButton:disabled { background: #9ca3af; color: #ffffff; }

/* Neutral default buttons (prevents ugly OS look). Sized to fit comfortably
   on every screen, not just the ones this was originally tuned for. */
QPushButton {
    background: #f3f4f6;
    color: #111827;
    border: 1px solid #e5e7eb;
    padding: 5px 11px;
    border-radius: 8px;
    min-height: 28px;
    font-size: 12px;
}
QPushButton:hover { background: #e5e7eb; }
QPushButton:pressed { background: #d1d5db; }
QPushButton:disabled { background: #f3f4f6; color: #9ca3af; border: 1px solid #eef0f2; }

/* Inputs - clearer than the original (which had zero sizing at all for
   QDateEdit/QSpinBox/QDoubleSpinBox), but scaled back from the first pass -
   this now applies to every screen in the app, not just the one it was
   built for. */
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #d1d5db;
    padding: 5px 8px;
    border-radius: 8px;
    min-height: 28px;
    font-size: 12px;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #1f8f6b;
}
QLineEdit:disabled, QComboBox:disabled {
    background: #f3f4f6;
    color: #9ca3af;
}

/* Combo dropdown POPUP - this is the one that actually mattered: it had
   ZERO styling anywhere before, falling back to a tiny native list. Still
   fixed here, just not oversized - meaningfully bigger than the OS default,
   without taking over the screen. */
QComboBox::drop-down {
    width: 24px;
    border: none;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d1d5db;
    outline: none;
    font-size: 12px;
    selection-background-color: #dcfce7;
    selection-color: #065f46;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 5px 8px;
}

/* Scrollbars - previously undefined anywhere, meaning every scroll area
   in this app (every screen wrapped in make_scrollable_page/dialog) had
   no reliable, visible scrollbar at all - just Fusion's own thin
   default, easy to miss entirely. Explicit here so it's unmistakable. */
QScrollBar:vertical {
    background: #f3f4f6;
    width: 14px;
    margin: 0px;
    border-radius: 7px;
}
QScrollBar::handle:vertical {
    background: #9ca3af;
    min-height: 24px;
    border-radius: 7px;
}
QScrollBar::handle:vertical:hover {
    background: #6b7280;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    border: none;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    background: #f3f4f6;
    height: 14px;
    margin: 0px;
    border-radius: 7px;
}
QScrollBar::handle:horizontal {
    background: #9ca3af;
    min-width: 24px;
    border-radius: 7px;
}
QScrollBar::handle:horizontal:hover {
    background: #6b7280;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    border: none;
    background: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* Tables */
QTableWidget {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    gridline-color: #eef2f7;
    selection-background-color: #dcfce7;
    selection-color: #065f46;
    font-size: 12px;
}
QTableWidget::item {
    padding: 4px;
}
QHeaderView::section {
    background: #f3f4f6;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    padding: 7px;
    font-weight: 700;
    font-size: 12px;
}

QTableWidget::item:selected {
    background: #dcfce7;
    color: #065f46;
}

/* Empty/unstyled panes and the table's top-left corner box - these paint
   with QPalette::Window/Base directly and can ignore setPalette() in some
   Qt/PySide2 builds, so pin them explicitly here too. */
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #e5e7eb;
}
QTableCornerButton::section {
    background: #f3f4f6;
    border: none;
}
QTableWidget {
    background-color: #ffffff;
}
QAbstractScrollArea {
    background: #ffffff;
}

QTabBar::tab {
    background: #f3f4f6;
    color: #111827;
    border: 1px solid #e5e7eb;
    border-bottom: none;
    padding: 6px 14px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0b2a24;
    font-weight: 700;
    border-bottom: 1px solid #ffffff; /* blends into the pane below */
}
QTabBar::tab:hover:!selected {
    background: #e5e7eb;
}


QPushButton {
    outline: none;
}
QPushButton:focus {
    outline: none;
}
QPushButton#PrimaryButton:focus,
QPushButton#PrimaryButton:default {
    background: #1f8f6b;
    color: #ffffff;
    border: none;
}

"""