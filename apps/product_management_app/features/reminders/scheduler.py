# apps/product_management_app/features/reminders/scheduler.py
# -*- coding: utf-8 -*-
"""
Shows the daily "who still owes us" reminder to the logged-in admin / cashier - once a day, the first time the
program is open after REMINDER_HOUR (default 08:00), and again the next day even if the program was left open
overnight. Nothing is shown when nobody owes anything, or while a weigher is logged in.

Wired up once from the main window:  DailyReminderScheduler(main_window).start()
"""
from __future__ import annotations

import logging

from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QApplication

from shared.auth.permissions import INTAKE_ROLES, current_role
from shared.auth.session import get_session

from .reminder_state import ReminderState

log = logging.getLogger("rafawa.reminders")
CHECK_EVERY_MS = 10 * 60 * 1000


class DailyReminderScheduler(QObject):
    def __init__(self, window, state: ReminderState = None):
        super().__init__(window)
        self._window = window
        self._state = state or ReminderState()
        self._open = None                     # the dialog while it is on screen
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        self._timer.start(CHECK_EVERY_MS)
        QTimer.singleShot(3000, self.check)

    def check(self, force: bool = False) -> bool:
        """Show the reminder if it is due. Returns True when the dialog was shown."""
        try:
            if self._open is not None or QApplication.activeModalWidget() is not None:
                return False                          # never on top of a sale / payment being typed
            sess = get_session()
            uid = int(getattr(sess, "user_id", 0) or 0) if sess else 0
            if uid <= 0 or current_role() not in INTAKE_ROLES:
                return False
            if not force and not self._state.due(uid):
                return False
            from database.services.reminder_service import ReminderService
            if ReminderService.collect()["parties"] == 0:
                self._state.mark_done(uid)            # nothing owed today - do not look again until tomorrow
                return False
            from .views.debtor_reminders import DailyReminderDialog
            dlg = DailyReminderDialog(self._window)
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
            log.exception("Daily reminder failed")
            return False
