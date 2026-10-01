"""
Brute-force protection for the login screen.

Per-process and in memory on purpose (a desktop app, no schema change needed):
after MAX_FREE_FAILURES wrong passwords for a username, further attempts are
refused for a growing delay. A successful login clears the counter.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

MAX_FREE_FAILURES = 5
BASE_LOCK_SECONDS = 30
MAX_LOCK_SECONDS = 15 * 60

_lock = threading.Lock()
_failures: Dict[str, Tuple[int, float]] = {}   # username -> (count, locked_until epoch)


class AccountLocked(ValueError):
    def __init__(self, seconds: int):
        self.seconds = int(seconds)
        super().__init__(f"Too many failed attempts. Try again in {self.seconds} second(s).")


def _key(username: str) -> str:
    return (username or "").strip().lower()


def check(username: str, now: float | None = None) -> None:
    """Raise AccountLocked if this username is currently locked out."""
    now = time.time() if now is None else now
    with _lock:
        _count, locked_until = _failures.get(_key(username), (0, 0.0))
    if locked_until > now:
        raise AccountLocked(int(locked_until - now) + 1)


def record_failure(username: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    k = _key(username)
    with _lock:
        count, _ = _failures.get(k, (0, 0.0))
        count += 1
        locked_until = 0.0
        if count >= MAX_FREE_FAILURES:
            delay = min(BASE_LOCK_SECONDS * 2 ** (count - MAX_FREE_FAILURES), MAX_LOCK_SECONDS)
            locked_until = now + delay
        _failures[k] = (count, locked_until)


def record_success(username: str) -> None:
    with _lock:
        _failures.pop(_key(username), None)


def reset() -> None:
    with _lock:
        _failures.clear()
