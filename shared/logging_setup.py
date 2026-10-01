"""
Central logging: rotating files in the data folder (never next to the exe,
which may be read-only) plus the console.

  app.log       everything INFO and above (rafawa.* loggers + libraries' warnings)
  security.log  rafawa.security / rafawa.audit only - logins, denials, voids, deletes
  crashes/      one text file per unhandled exception (see crash.py)
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from shared.paths import sub_dir
from shared.version import __version__

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def setup_logging(level: int = logging.INFO, console: bool = True) -> None:
    global _configured
    if _configured:
        return
    log_dir = sub_dir("logs")
    fmt = logging.Formatter(_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)

    app_file = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    app_file.setFormatter(fmt)
    root.addHandler(app_file)

    sec_file = logging.handlers.RotatingFileHandler(
        log_dir / "security.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    sec_file.setFormatter(fmt)
    sec_file.addFilter(lambda r: r.name.startswith(("rafawa.security", "rafawa.audit")))
    root.addHandler(sec_file)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.setLevel(logging.WARNING)
        root.addHandler(stream)

    _configured = True
    logging.getLogger("rafawa").info("Rafawa Desktop %s starting (python %s)", __version__, sys.version.split()[0])
