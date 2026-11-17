# apps/product_management_app/features/closings/closing_state.py
"""
Tracks when the daily morning Customer Closing review is due for an Admin user.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from shared.paths import data_dir

log = logging.getLogger("rafawa.closings")
SNOOZE_HOURS = 2
_FILE = "closings_state.json"


def closing_hour() -> int:
    try:
        return min(max(int(os.getenv("CLOSING_HOUR", os.getenv("REMINDER_HOUR", "8"))), 0), 23)
    except ValueError:
        return 8


class ClosingReminderState:
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
            log.exception("Could not save the closing reminder state")

    def due(self, user_id: int, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if now.hour < closing_hour():
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
