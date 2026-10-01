# shared/uix/layout/shell.py
from typing import Dict, Callable

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QLabel

from .sidebar import Sidebar, SidebarItem
from .topbar import TopBar
from .sync_status_bar import SyncStatusBar


class AppShell(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._routes: Dict[str, Callable[[], QWidget]] = {}
        self._route_to_index: Dict[str, int] = {}
        self._title_map: Dict[str, str] = {}

        # Outer layout: the sync status bar spans the full window width,
        # sitting above everything else - sidebar and content included -
        # so it's visible on every screen, every role, no matter what's
        # navigated to below it.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sync_status_bar = SyncStatusBar()      # added at the bottom, below the content

        row = QWidget()
        root = QHBoxLayout(row)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        outer.addWidget(row, 1)

        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        root.addLayout(right, 1)

        self.topbar = TopBar("Dashboard")
        right.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        right.addWidget(self.stack, 1)

        outer.addWidget(self.sync_status_bar)

        self.sidebar.route_selected.connect(self.navigate)

    def set_title(self, title: str):
        self.topbar.set_title(title)

    from typing import Optional

    def set_workspace(self, title: str, menu_items: list, routes: Dict, title_map: Optional[Dict[str, str]] = None):


        # Python 3.9 NOTE:
        # Replace the signature above with:
        # def set_workspace(..., title_map=None):

        self.sidebar.set_menu(menu_items, title=title)
        self.set_title(title)

        self._routes = routes
        self._route_to_index.clear()
        self._title_map = title_map or {}

        # Clear stack safely
        while self.stack.count() > 0:
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()

        # Workspace home (optional)
        home = QLabel(f"{title} Workspace")
        home.setStyleSheet("font-size: 16px; padding: 16px;")
        self.stack.addWidget(home)
        self.stack.setCurrentIndex(0)

    def navigate(self, route_key: str):
        if route_key not in self._routes:
            self.set_title("Not implemented")
            return

        if route_key in self._route_to_index:
            self.stack.setCurrentIndex(self._route_to_index[route_key])
            self.set_title(self._title_map.get(route_key, route_key))
            return

        view = self._routes[route_key]()
        idx = self.stack.addWidget(view)
        self._route_to_index[route_key] = idx
        self.stack.setCurrentIndex(idx)
        self.set_title(self._title_map.get(route_key, route_key))