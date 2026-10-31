# apps/product_management_app/main.py
from apps.product_management_app.app_state import state
from database.services.seed_service import SeedService
import sys
from dataclasses import dataclass
from PySide2.QtWidgets import (QApplication, QDialog, QFrame, QMainWindow, QScrollArea, QStackedWidget,
                               QTableView)
from PySide2.QtCore import QEvent, QObject, Qt, QTimer
from shared.uix.layout.shell import AppShell
from apps.product_management_app.routes import Router
from apps.product_management_app.launcher import LauncherView
from apps.product_management_app.workspaces import build_workspaces
from apps.product_management_app.services.cloud_sync_worker import CloudSyncWorker
from shared.uix.theme import APP_STYLESHEET


def _scrolled(view):
    """
    Host a screen inside a scroll area. A screen whose contents are taller/wider than the window used to push
    the whole window past the edge of the monitor (or get cut off with nothing to scroll); now the window keeps
    its size and the screen scrolls instead.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(view)
    return area


class _FitToScreen(QObject):
    """Keeps every dialog / pop-up window inside the monitor it opens on (shrinks one that is too big)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog) and obj.isWindow():
            try:
                screen = QApplication.screenAt(obj.mapToGlobal(obj.rect().center())) or QApplication.primaryScreen()
                avail = screen.availableGeometry()
                max_w, max_h = int(avail.width() * 0.96), int(avail.height() * 0.94)
                if obj.minimumWidth() > max_w:
                    obj.setMinimumWidth(max_w)
                if obj.minimumHeight() > max_h:
                    obj.setMinimumHeight(max_h)
                if obj.width() > max_w or obj.height() > max_h:
                    obj.resize(min(obj.width(), max_w), min(obj.height(), max_h))
                    obj.move(avail.left() + (avail.width() - obj.width()) // 2,
                             avail.top() + (avail.height() - obj.height()) // 2)
            except Exception:
                pass
        return False


class _FitTables(QObject):
    """The first time any table is shown, size its columns to their content (see shared.uix.widgets.table_fit)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QTableView) and not obj.property("autofit_done"):
            from shared.uix.widgets.table_fit import autofit
            autofit(obj)
        return False


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

        # never bigger than the screen it opens on, and small enough to snap into half a monitor
        avail = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(760, avail.width()), min(520, avail.height()))
        self.resize(min(1280, avail.width()), min(820, avail.height()))

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
        self.cloud_sync_worker.sync_detail.connect(self.shell.sync_status_bar.on_sync_detail)
        # Started a moment after the window is up (never delaying startup). It was never started at all before -
        # the bar just kept its first text, "not configured", whatever the settings were.
        QTimer.singleShot(2500, self._start_cloud_sync)

        # Global TopBar actions
        from shared.auth.session import set_session, require_user_id
        self.shell.topbar.back_requested.connect(self.return_to_launcher)
        self.shell.topbar.logout_requested.connect(self._logout_and_return)



        self._root_stack.addWidget(_scrolled(self.launcher))  # index 0
        self._root_stack.addWidget(self.shell)     # index 1
        self._root_stack.setCurrentIndex(0)

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

        # A cashier opens only Cashier, a weigher only Weigher; admin may open any.
        from shared.auth.permissions import current_role, workspace_allowed
        if not workspace_allowed(current_role(), workspace_key):
            from PySide2.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Not allowed", "Your account cannot open that workspace.")
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

        host = _scrolled(view)
        self.shell.stack.addWidget(host)
        self.shell.stack.setCurrentWidget(host)

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
        self._root_stack.setCurrentIndex(0)

    def _start_cloud_sync(self):
        worker = getattr(self, "cloud_sync_worker", None)
        if worker is not None and not worker.isRunning():
            worker.start()

    def closeEvent(self, event):
        # Stop the sync worker and genuinely wait for its current sleep/
        # sync attempt to notice and exit, rather than letting a still-
        # running background thread hang or crash the app on close.
        if hasattr(self, "cloud_sync_worker"):
            self.cloud_sync_worker.stop()
            self.cloud_sync_worker.wait(3000)
        super().closeEvent(event)


def _auto_backup_in_background():
    """Once-a-day automatic backup when AUTO_BACKUP=1 (never blocks or crashes startup)."""
    import os
    import threading
    if os.getenv("AUTO_BACKUP", "0").lower() not in ("1", "true", "yes"):
        return

    def _job():
        import logging
        try:
            from database.backup import backup_database, backup_due
            if backup_due():
                backup_database(keep=int(os.getenv("BACKUP_KEEP", "14")))
        except Exception:
            logging.getLogger("rafawa.backup").exception("Automatic backup failed")

    threading.Thread(target=_job, name="auto-backup", daemon=True).start()


def _warn_if_schema_outdated(parent):
    try:
        from database.schema_check import check_schema
        status = check_schema()
        if not status.up_to_date:
            from PySide2.QtWidgets import QMessageBox
            QMessageBox.warning(parent, "Database update needed", status.message)
    except Exception:
        import logging
        logging.getLogger("rafawa").exception("Schema check failed")


def _notify_if_update_available(parent):
    try:
        from shared.updater import check_for_update
        info = check_for_update()
        if info:
            from PySide2.QtWidgets import QMessageBox
            QMessageBox.information(
                parent, "Update available",
                f"Version {info.version} is available.\n\n{info.notes}\n\nDownload: {info.url}")
    except Exception:
        import logging
        logging.getLogger("rafawa").exception("Update check failed")


def run():
    from shared.logging_setup import setup_logging
    from shared.crash import install_excepthooks
    from shared.version import __version__
    setup_logging()

    # Learn this PC's screen first and fit the whole interface to it (see shared/uix/screen_profile.py)
    from shared.uix.screen_profile import enable_high_dpi, prepare_screen_scaling
    screen_info = prepare_screen_scaling()
    enable_high_dpi()
    app = QApplication(sys.argv)
    app.setProperty("rafawa_screen", f"{screen_info.width}x{screen_info.height} scale {screen_info.scale:g} ({screen_info.source})")

    def _notify_crash(message, report_path):
        from PySide2.QtWidgets import QMessageBox
        QMessageBox.critical(
            None, "Something went wrong",
            f"The application hit an unexpected error:\n\n{message}\n\n"
            f"A report was saved to:\n{report_path}\n\nPlease send it to support.")
    install_excepthooks(_notify_crash)
    # Fusion is the one Qt style that fully, reliably respects QSS - the
    # native Windows style ("windowsvista") only partially honors
    # stylesheet rules for certain things (combobox popup rendering,
    # scrollbar appearance), instead falling back to the OS's own theme
    # colors - which is exactly why a combobox popup could render with a
    # black background and invisible text when Windows is in dark mode,
    # and why scrollbars could end up effectively invisible everywhere,
    # even though this app's own QSS defines visible colors for both.
    app.setStyle("Fusion")
    from shared.uix.theme import apply_theme
    apply_theme(app)
    fit = _FitToScreen(app)
    app.installEventFilter(fit)
    tables = _FitTables(app)
    app.installEventFilter(tables)
    win = ProductManagementMainWindow()
    win.setWindowTitle(f"Rafawa Desktop {__version__}")
    win.showMaximized()
    _warn_if_schema_outdated(win)
    _notify_if_update_available(win)
    _auto_backup_in_background()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run()