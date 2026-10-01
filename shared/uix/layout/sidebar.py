# shared/uix/layout/sidebar.py
from dataclasses import dataclass, field
from typing import List, Optional

from PySide2.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QScrollArea, QFrame, QButtonGroup
from PySide2.QtCore import Signal, Qt


@dataclass(frozen=True)
class SidebarItem:
    label: str
    route: Optional[str] = None
    # A group header (e.g. "Profiles") has children and no route of its
    # own - clicking it only ever toggles expansion, it never navigates.
    # A leaf item (e.g. "Customers") has a route and no children.
    children: Optional[List["SidebarItem"]] = field(default=None)


class Sidebar(QWidget):
    route_selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(240)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        brand = QLabel("RAFAWA")
        brand.setObjectName("SidebarBrand")
        outer.addWidget(brand)

        self._header = QLabel("Product Management")
        self._header.setStyleSheet("font-size: 14px; font-weight: 700; padding: 12px 12px 4px 12px;")
        self._header.setObjectName("SidebarHeader")
        outer.addWidget(self._header)

        # The button list previously had NO scroll mechanism at all - with
        # this many menu items now (12+), a smaller or non-maximized window
        # left the bottom items genuinely unreachable, not just hard to
        # see, since there was nothing to scroll in the first place.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        from shared.version import __version__
        footer = QLabel(f"Version {__version__}")
        footer.setObjectName("SidebarFooter")
        outer.addWidget(footer)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(12, 8, 12, 12)
        self._layout.setSpacing(8)
        scroll.setWidget(content)

        self._route_buttons: List[QPushButton] = []  # only leaf/route buttons - these are what "one active at a time" applies to
        self._top_level_widgets: List[QWidget] = []  # everything added directly to the layout, for cleanup
        # An explicit group, not Qt's implicit parent-based autoExclusive -
        # that only groups buttons sharing the same immediate parent
        # widget, which breaks once route buttons are nested inside
        # separate per-group containers. This keeps "only one active at a
        # time" genuinely true across the whole sidebar, group or not.
        self._route_button_group = QButtonGroup(self)
        self._route_button_group.setExclusive(True)
        self._layout.addStretch(1)

        # Default PM menu so existing behavior is preserved
        self.set_menu(self.default_pm_menu(), title="Product Management")

    def set_menu(self, items: List[SidebarItem], title: Optional[str] = None) -> None:
        if title is not None:
            self._header.setText(title)

        # Remove everything from the previous menu - whole group
        # containers included, not just individual buttons.
        for w in self._top_level_widgets:
            self._layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._top_level_widgets = []
        self._route_buttons = []
        self._route_button_group = QButtonGroup(self)
        self._route_button_group.setExclusive(True)

        # Remove stretch temporarily
        stretch_item = self._layout.takeAt(self._layout.count() - 1)

        for item in items:
            if item.children:
                self._add_group(item)
            else:
                self._add_leaf(item, self._layout)

        # Re-add stretch
        self._layout.addItem(stretch_item)
        if self._route_buttons:
            self._route_buttons[0].setChecked(True)

    def _add_leaf(self, item: SidebarItem, target_layout) -> QPushButton:
        """A normal, clickable route button - same behavior every
        sidebar item has always had, whether it's standalone or sits
        inside a group's children."""
        btn = QPushButton(item.label)
        btn.setMinimumHeight(36)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, r=item.route: self.route_selected.emit(r))
        btn.setCheckable(True)
        self._route_button_group.addButton(btn)
        target_layout.addWidget(btn)
        self._route_buttons.append(btn)
        if target_layout is self._layout:
            self._top_level_widgets.append(btn)
        return btn

    def _add_group(self, item: SidebarItem) -> None:
        """A group header - "Profiles", "Catalog", "Orders" etc. Toggles
        a nested, indented container of its own children on click; never
        emits route_selected itself, since a group has no route of its
        own to navigate to. Starts collapsed - the whole point of
        grouping is fewer visible top-level items until one is opened."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        group_layout = QVBoxLayout(container)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(6)

        header_btn = QPushButton(f"\u25b8  {item.label}")
        header_btn.setMinimumHeight(36)
        header_btn.setCursor(Qt.PointingHandCursor)
        header_btn.setObjectName("SidebarGroupHeader")
        group_layout.addWidget(header_btn)

        children_box = QWidget()
        children_box.setStyleSheet("background: transparent;")
        children_layout = QVBoxLayout(children_box)
        children_layout.setContentsMargins(18, 0, 0, 0)  # indent, so children read as nested
        children_layout.setSpacing(6)
        for child in item.children:
            self._add_leaf(child, children_layout)
        children_box.setVisible(False)  # collapsed by default
        group_layout.addWidget(children_box)

        def _toggle(_checked=False):
            now_visible = not children_box.isVisible()
            children_box.setVisible(now_visible)
            arrow = "\u25be" if now_visible else "\u25b8"
            header_btn.setText(f"{arrow}  {item.label}")

        header_btn.clicked.connect(_toggle)

        self._layout.addWidget(container)
        self._top_level_widgets.append(container)


    @staticmethod
    def default_pm_menu() -> List[SidebarItem]:
        return [
            SidebarItem("Profiles", "profiles.customers"),
            SidebarItem("Catalog • Products", "catalog.products"),
            SidebarItem("Catalog • Sales", "catalog.sales"),   # ✅ NEW
            SidebarItem("Catalog • Categories", "catalog.categories"),
            SidebarItem("Inventory", "inventory.list"),
            SidebarItem("Procurements", "procurements.list"),
            SidebarItem("Suppliers", "suppliers.list"),
        ]