# apps/product_management_app/main.py
from apps.product_management_app.app_state import state
from database.services.seed_service import SeedService
import sys
from dataclasses import dataclass
from PySide2.QtWidgets import QApplication, QMainWindow, QStackedWidget
from PySide2.QtCore import Qt
from shared.uix.layout.shell import AppShell
from apps.product_management_app.routes import Router
from apps.product_management_app.launcher import LauncherView
from apps.product_management_app.workspaces import build_workspaces
from apps.product_management_app.services.cloud_sync_worker import CloudSyncWorker
from shared.uix.theme import APP_STYLESHEET


class ProductManagementMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        SeedService.ensure_bootstrap_data()
        # `state` is a module-level singleton that already loads customers
        # and walkins from the DB the moment app_state.py is first
        # imported (see AppState.__init__), which happens above this
        # class body. Calling bootstrap_db_state() again here just repeats
        # those same DB round trips for no benefit, doubling that part of
        # startup latency.

        self.resize(1280, 820)

        # Workspace registry
        self.workspaces = build_workspaces()

        # Central stack: Launcher -> AppShell
        self._root_stack = QStackedWidget()
        self.setCentralWidget(self._root_stack)

        self.launcher = LauncherView()
        self.shell = AppShell()

        # Cloud sync - started once, lives for the whole session,
        # regardless of who's logged in or which workspace is open. The
        # status bar lives inside the shell itself, so it's visible on
        # every screen already - this just wires the worker's signals to
        # it, all cross-thread delivery handled safely by Qt itself.
        self.cloud_sync_worker = CloudSyncWorker(self)
        self.cloud_sync_worker.not_configured.connect(self.shell.sync_status_bar.on_not_configured)
        self.cloud_sync_worker.sync_started.connect(self.shell.sync_status_bar.on_sync_started)
        self.cloud_sync_worker.table_progress.connect(self.shell.sync_status_bar.on_table_progress)
        self.cloud_sync_worker.sync_finished.connect(self.shell.sync_status_bar.on_sync_finished)
        # self.cloud_sync_worker.start()

        # Global TopBar actions
        from shared.auth.session import set_session, require_user_id
        self.shell.topbar.back_requested.connect(self.return_to_launcher)
        self.shell.topbar.logout_requested.connect(self._logout_and_return)



        self._root_stack.addWidget(self.launcher)  # index 0
        self._root_stack.addWidget(self.shell)     # index 1
        self._root_stack.setCurrentWidget(self.launcher)

        # Router for the active workspace
        self.router = Router()
        self.title_map = {}
        self.active_workspace_key = None

        # Connect launcher
        self.launcher.workspace_selected.connect(self.open_workspace)

        # Sidebar navigation
        self.shell.sidebar.route_selected.connect(self.navigate)

        # Window title for launcher
        self.setWindowTitle("Rafawa – App Launcher")

    def open_workspace(self, workspace_key: str):
        # Phase 3.1: block access if not logged in
        try:
            from shared.auth.session import require_user_id
            require_user_id()
        except Exception as e:
            from PySide2.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Login Required", str(e))
            return

        if workspace_key not in self.workspaces:
            return

        spec = self.workspaces[workspace_key]
        self.active_workspace_key = workspace_key

        # Set window title to match workspace
        self.setWindowTitle(spec.window_title)

        # Configure sidebar/menu header for this workspace
        self.shell.sidebar.set_menu(spec.menu, title=spec.sidebar_title)

        # Reset router (fresh instance is simplest and safest)
        self.router = Router()
        for route, factory in spec.routes.items():
            self.router.register(route, factory)

        # Title mapping for topbar
        self.title_map = spec.title_map

        # Switch to shell "as if another application is open"
        self._root_stack.setCurrentWidget(self.shell)

        # Navigate to default route
        self.navigate(spec.default_route)


    def navigate(self, route: str):
        try:
            view = self.router.resolve(route)
        except KeyError:
            # If route isn't registered, do nothing (or fallback)
            return

        self.shell.set_title(self.title_map.get(route, route))

        # Clear previous view(s)
        while self.shell.stack.count() > 0:
            w = self.shell.stack.widget(0)
            self.shell.stack.removeWidget(w)
            w.deleteLater()

        self.shell.stack.addWidget(view)
        self.shell.stack.setCurrentWidget(view)

    def _logout_and_return(self):
        # Phase 3.1: logout clears the session
        try:
            from shared.auth.session import set_session
            set_session(None)
        except Exception:
            pass
        self.return_to_launcher()


    def return_to_launcher(self):
        # Reset window title
        self.setWindowTitle("Rafawa – App Launcher")

        # Reset router and workspace state
        self.router = Router()
        self.title_map = {}
        self.active_workspace_key = None

        # Clear current workspace views
        while self.shell.stack.count() > 0:
            w = self.shell.stack.widget(0)
            self.shell.stack.removeWidget(w)
            w.deleteLater()

        # Switch back to launcher
        self._root_stack.setCurrentWidget(self.launcher)

    def closeEvent(self, event):
        # Stop the sync worker and genuinely wait for its current sleep/
        # sync attempt to notice and exit, rather than letting a still-
        # running background thread hang or crash the app on close.
        if hasattr(self, "cloud_sync_worker"):
            self.cloud_sync_worker.stop()
            self.cloud_sync_worker.wait(3000)
        super().closeEvent(event)


def run():
    app = QApplication(sys.argv)
    # Fusion is the one Qt style that fully, reliably respects QSS - the
    # native Windows style ("windowsvista") only partially honors
    # stylesheet rules for certain things (combobox popup rendering,
    # scrollbar appearance), instead falling back to the OS's own theme
    # colors - which is exactly why a combobox popup could render with a
    # black background and invisible text when Windows is in dark mode,
    # and why scrollbars could end up effectively invisible everywhere,
    # even though this app's own QSS defines visible colors for both.
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    win = ProductManagementMainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run()