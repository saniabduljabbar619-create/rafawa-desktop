"""Where the app keeps its writable files (logs, crash reports, backups)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """
    Per-user data folder: %LOCALAPPDATA%\\RafawaDesktop on Windows, ~/.rafawa-desktop elsewhere.
    Override with RAFAWA_DATA_DIR (used by tests and portable installs).
    """
    override = os.getenv("RAFAWA_DATA_DIR", "").strip()
    if override:
        base = Path(override)
    elif sys.platform.startswith("win") and os.getenv("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "RafawaDesktop"
    else:
        base = Path.home() / ".rafawa-desktop"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub_dir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p
