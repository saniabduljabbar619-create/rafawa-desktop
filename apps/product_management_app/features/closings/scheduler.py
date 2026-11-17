# apps/product_management_app/features/closings/scheduler.py
# -*- coding: utf-8 -*-
"""
Shows the daily Morning Customer Closings review & confirmation dialog to the
logged-in Admin once a day after CLOSING_HOUR (default 08:00).
"""
from __future__ import annotations

import logging

from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QApplication

from shared.auth.permissions import current_role
from shared.auth.session import get_session

from .closing_state import ClosingReminderState

log = logging.getLogger("rafawa.closings")
CHECK_EVERY_MS = 10 * 60 * 1000


class DailyClosingScheduler(QObject):
    def __init__(self, window, state: ClosingReminderState = None):
        super().__init__(window)
        self._window = window
        self._state = state or ClosingReminderState()
        self._open = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        self._timer.start(CHECK_EVERY_MS)
        QTimer.singleShot(6000, self.check)

    def check(self, force: bool = False) -> bool:
        """Show the morning closing review if due for Admin. Returns True when shown."""
        try:
            if self._open is not None or QApplication.activeModalWidget() is not None:
                return False
            sess = get_session()
            uid = int(getattr(sess, "user_id", 0) or 0) if sess else 0
            if uid <= 0 or current_role() != "admin":
                return False
            if not force and not self._state.due(uid):
                return False
            from database.services.closing_service import ClosingService
            previews = ClosingService.preview_pending_closings()
            if not previews:
                self._state.mark_done(uid)
                return False
            from .morning_closing_dialog import MorningClosingDialog
            dlg = MorningClosingDialog(uid, previews=previews, parent=self._window)
            self._open = dlg
            dlg.exec_()
            if dlg.choice == "later":
                self._state.snooze(uid)
            else:
                self._state.mark_done(uid)
            self._open = None
            return True
        except Exception:
            self._open = None
            log.exception("Daily closing review failed")
            return False
