# apps/product_management_app/services/cloud_sync_worker.py
# -*- coding: utf-8 -*-
"""
Runs CloudSyncEngine periodically, on its own thread. Blocking DB I/O
(both local and remote, over the internet) must never run on the UI
thread - a slow or unreachable cloud connection would otherwise freeze
the entire app for however long that attempt takes. Qt signals emitted
from this thread are safely, automatically queued onto the main thread
by Qt itself when connected to a main-thread slot (the default
AutoConnection becomes a QueuedConnection across threads) - so the UI
side never needs to think about threading at all, only respond to
signals.

Started once, at app startup, and lives for the whole session -
matches the actual intent: this is about the whole business's data
safety, not tied to any specific logged-in user or workspace.
"""
from __future__ import annotations

from PySide2.QtCore import QThread, Signal

from shared.config.settings import settings
from database.services.cloud_sync_service import CloudSyncEngine


class CloudSyncWorker(QThread):
    # Emitted right before a sync pass begins - lets the UI switch to
    # "syncing..." state, or show "not configured" instead of a spinner
    # that would never actually move.
    sync_started = Signal()
    not_configured = Signal()

    # (table_name, index, total, ok, pushed) - once per table, so the UI
    # can show real, table-by-table progress, not just a vague spinner.
    table_progress = Signal(str, int, int, bool, int)

    # (all_ok, total_pushed, failed_tables) - once per full pass
    sync_finished = Signal(bool, int, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = False

    def run(self):
        while not self._stop_requested:
            if CloudSyncEngine.is_configured():
                self.sync_started.emit()

                failed_tables = []
                total_pushed = 0

                def _on_table_done(table_name, index, total, result):
                    nonlocal total_pushed, failed_tables
                    ok = bool(result.get("ok"))
                    pushed = int(result.get("pushed") or 0)
                    if ok:
                        total_pushed += pushed
                    else:
                        failed_tables.append(table_name)
                    self.table_progress.emit(table_name, index, total, ok, pushed)

                CloudSyncEngine.sync_all(progress_callback=_on_table_done)
                self.sync_finished.emit(len(failed_tables) == 0, total_pushed, failed_tables)
            else:
                self.not_configured.emit()

            self._sleep_interruptibly(settings.CLOUD_SYNC_INTERVAL_SECONDS)

    def _sleep_interruptibly(self, total_seconds: int):
        """
        Sleeps in small increments rather than one long call, so
        stop() takes effect within about a second rather than waiting
        out the full interval - matters for a clean, prompt app exit.
        """
        for _ in range(max(1, total_seconds)):
            if self._stop_requested:
                return
            self.msleep(1000)

    def stop(self):
        self._stop_requested = True