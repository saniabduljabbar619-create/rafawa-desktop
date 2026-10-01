"""
Authorization enforced where the work happens (the service layer), not just
by hiding buttons in the UI.

Usage::

    @staticmethod
    @require_admin
    def void_payment(...): ...

A call is allowed when the logged-in session's role is permitted. With no
session at all it is DENIED - code that legitimately runs outside a login
(first-run setup, migrations, scripts) wraps itself in ``system_context()``.
"""
from __future__ import annotations

import functools
import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterable

from shared.auth.session import get_session

_log = logging.getLogger("rafawa.security")

ADMIN_ROLES = frozenset({"admin", "pm"})


class PermissionDenied(PermissionError):
    """The current user's role may not perform this action."""


_state = threading.local()


@contextmanager
def system_context():
    """Trusted, non-interactive code path (setup, migration, maintenance script)."""
    previous = getattr(_state, "system", False)
    _state.system = True
    try:
        yield
    finally:
        _state.system = previous


def current_role() -> str:
    sess = get_session()
    return str(getattr(sess, "role", "") or "").strip().lower() if sess else ""


def has_role(*roles: str) -> bool:
    if getattr(_state, "system", False):
        return True
    return current_role() in {r.strip().lower() for r in roles}


def require_role(*roles: str) -> Callable:
    allowed = frozenset(r.strip().lower() for r in roles)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not getattr(_state, "system", False) and current_role() not in allowed:
                sess = get_session()
                _log.warning(
                    "DENIED %s: user=%s role=%r needs one of %s",
                    fn.__qualname__, getattr(sess, "username", None), current_role(), sorted(allowed))
                raise PermissionDenied(
                    "You do not have permission to do this. An administrator is required.")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(fn: Callable) -> Callable:
    return require_role(*ADMIN_ROLES)(fn)


def roles_allowed(roles: Iterable[str]) -> frozenset:
    return frozenset(r.strip().lower() for r in roles)
