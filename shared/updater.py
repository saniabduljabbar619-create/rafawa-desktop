"""
Update check (notify only - the app never downloads or runs anything by itself).

Set UPDATE_MANIFEST_URL in .env to an https:// URL or a file/UNC path holding::

    {"version": "3.6.0", "url": "https://.../RafawaAppSetup.exe", "notes": "What changed"}

At start-up the app compares it with shared.version.__version__ and, if newer,
tells the user where to get it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from shared.version import __version__

_log = logging.getLogger("rafawa.updater")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    notes: str


def parse_version(v: str) -> Tuple[int, ...]:
    """'3.10.2' -> (3, 10, 2); ignores a leading 'v' and any suffix like '-beta'."""
    nums = re.findall(r"\d+", str(v).split("-")[0].lstrip("vV"))
    if not nums:
        raise ValueError(f"not a version: {v!r}")
    return tuple(int(n) for n in nums)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _read_manifest(source: str, timeout: float) -> dict:
    if source.lower().startswith(("http://", "https://")):
        import httpx
        r = httpx.get(source, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    return json.loads(Path(source).read_text(encoding="utf-8"))


def check_for_update(source: Optional[str] = None, timeout: float = 3.0) -> Optional[UpdateInfo]:
    """The newer release described by the manifest, or None (also None on ANY error - never raises)."""
    source = (source if source is not None else os.getenv("UPDATE_MANIFEST_URL", "")).strip()
    if not source:
        return None
    try:
        data = _read_manifest(source, timeout)
        version = str(data["version"])
        if is_newer(version):
            return UpdateInfo(version=version, url=str(data.get("url", "")), notes=str(data.get("notes", "")))
    except Exception as e:                              # offline, bad JSON, bad version...
        _log.info("Update check skipped: %s", e)
    return None
