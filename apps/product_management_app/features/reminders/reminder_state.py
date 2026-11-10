# apps/product_management_app/features/reminders/reminder_state.py
"""
When is the daily debtor reminder due? Pure logic (no Qt, no database) so it is easy to test.

Rules, per logged-in user:
  * never before REMINDER_HOUR (default 08:00, set REMINDER_HOUR=0..23 to change it)
  * at most once per calendar day ("Done for today")
  * "Remind me in 2 hours" postpones it, but only within the same day
The memory is a small JSON file in the app's data folder, so it survives closing and reopening the program.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from shared.paths import data_dir

log = logging.getLogger("rafawa.reminders")
SNOOZE_HOURS = 2
_FILE = "reminders_state.json"


def reminder_hour() -> int:
    try:
        return min(max(int(os.getenv("REMINDER_HOUR", "8")), 0), 23)
    except ValueError:
        return 8


class ReminderState:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else data_dir() / _FILE

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        try:
            self._path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            log.exception("Could not save the reminder state")

    def due(self, user_id: int, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if now.hour < reminder_hour():
            return False
        entry = self._load().get(str(int(user_id)), {})
        if entry.get("last_shown") == now.date().isoformat():
            return False
        until = entry.get("snooze_until")
        if until:
            try:
                if now < datetime.fromisoformat(until):
                    return False
            except ValueError:
                pass
        return True

    def mark_done(self, user_id: int, today: Optional[date] = None) -> None:
        data = self._load()
        data[str(int(user_id))] = {"last_shown": (today or date.today()).isoformat()}
        self._save(data)

    def snooze(self, user_id: int, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        data = self._load()
        data[str(int(user_id))] = {"snooze_until": (now + timedelta(hours=SNOOZE_HOURS)).isoformat()}
        self._save(data)
