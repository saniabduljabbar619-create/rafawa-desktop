"""
Unhandled-exception handling: write a crash report the user can send to support,
and tell the user - instead of the app silently vanishing or printing to a
console nobody sees.
"""
from __future__ import annotations

import logging
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from shared.paths import sub_dir
from shared.version import __version__

_log = logging.getLogger("rafawa.crash")


def format_crash_report(exc_type, exc, tb, extra: Optional[dict] = None) -> str:
    lines = [
        f"Rafawa Desktop crash report - {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Version : {__version__}",
        f"Python  : {sys.version.split()[0]} on {platform.platform()}",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"{k:8s}: {v}")
    lines += ["", "".join(traceback.format_exception(exc_type, exc, tb))]
    return "\n".join(lines)


def write_crash_report(exc_type, exc, tb, extra: Optional[dict] = None) -> Path:
    path = sub_dir("crashes") / f"crash-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
    path.write_text(format_crash_report(exc_type, exc, tb, extra), encoding="utf-8")
    return path


def install_excepthooks(notify: Optional[Callable[[str, Path], None]] = None) -> None:
    """
    Route unhandled exceptions (main thread and worker threads) to the log and a
    crash file. `notify(message, report_path)` shows the user a dialog; it is
    optional so this can be used without a GUI.
    """
    def _handle(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            path = write_crash_report(exc_type, exc, tb)
        except Exception:                                   # never let reporting itself crash
            _log.exception("Could not write crash report")
            return
        if notify is not None:
            try:
                notify(f"{exc_type.__name__}: {exc}", path)
            except Exception:
                _log.exception("Crash notification failed")

    sys.excepthook = _handle
    threading.excepthook = lambda a: _handle(a.exc_type, a.exc_value, a.exc_traceback)
