# shared/uix/screen_profile.py
# -*- coding: utf-8 -*-
"""
The app learns the size of the PC it is running on and fits itself to it.

Every screen of the app is laid out to fit a 1280 x 680 area without a sideways scrollbar (toolbars wrap, cards
share the width, wide tables narrow their text columns - see shared.uix.layout.flow and widgets.table_fit). A PC whose
usable screen is SMALLER than that (an old 1024 x 768 monitor, a laptop with Windows display scaling at 125 / 150 %)
gets the whole interface scaled down by the right factor so the same layouts still fit; a very large monitor is scaled
up a little so nothing looks tiny. Windows display scaling (DPI) is honoured as well.

How: before the real QApplication exists, a throw-away QGuiApplication reads the usable screen size, we work out the
factor, put it in QT_SCALE_FACTOR (Qt applies it uniformly to every widget, font and dialog), and only then is the
real application created. Settings (.env or environment):
    UI_SCALE=0.9       fix the factor by hand
    UI_SCALE_AUTO=0    switch automatic fitting off (factor 1)
A QT_SCALE_FACTOR already set in the environment is respected and left alone.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("rafawa")

DESIGN_W, DESIGN_H = 1280, 680       # every screen is built to fit this usable area (width, height) with no sideways scroll
MIN_SCALE, MAX_SCALE = 0.7, 1.5
_STEP = 0.05


@dataclass(frozen=True)
class ScreenInfo:
    width: int            # usable width Qt sees before our scaling (after Windows display scaling), pixels
    height: int
    scale: float          # the factor applied
    source: str           # "auto" | "manual" | "env" | "off" | "failed"

    @property
    def effective(self):
        """The usable size the layouts actually get after scaling."""
        return int(self.width / self.scale), int(self.height / self.scale)


def compute_scale(width: int, height: int) -> float:
    """The factor that makes a (width x height) usable screen hold the 1280 x 680 design area."""
    width, height = max(int(width), 1), max(int(height), 1)
    fit = min(width / DESIGN_W, height / DESIGN_H)
    if fit < 0.97:                                            # smaller than the design: shrink to fit
        return max(round(fit / _STEP) * _STEP, MIN_SCALE)
    if width >= 2400:                                         # a big monitor: enlarge a little, never beyond 1.5
        return min(round(width / 1920 / _STEP) * _STEP, MAX_SCALE)
    return 1.0


def probe_logical_screen() -> Optional[Tuple[int, int]]:
    """Usable (width, height) of the primary screen as Qt reports it, via a throw-away QGuiApplication."""
    from PySide2.QtCore import Qt
    from PySide2.QtGui import QGuiApplication
    QGuiApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:                                         # older Qt: 125 / 150 % are rounded by the system
        pass
    probe = QGuiApplication(["rafawa-screen-probe"])
    try:
        screen = probe.primaryScreen()
        if screen is None:
            return None
        g = screen.availableGeometry()
        return int(g.width()), int(g.height())
    finally:
        probe.shutdown() if hasattr(probe, "shutdown") else None
        del probe


def prepare_screen_scaling(env: Optional[Dict[str, str]] = None,
                           probe: Callable[[], Optional[Tuple[int, int]]] = probe_logical_screen) -> ScreenInfo:
    """Work out the scale for this PC and put it in the environment. Call BEFORE creating the QApplication.
    Never raises: any problem leaves the interface at scale 1."""
    env = os.environ if env is None else env
    try:
        manual = (env.get("UI_SCALE") or "").strip()
        if env.get("QT_SCALE_FACTOR"):
            return ScreenInfo(0, 0, float(env["QT_SCALE_FACTOR"]), "env")
        if manual:
            scale = min(max(float(manual), 0.5), 2.5)
            env["QT_SCALE_FACTOR"] = f"{scale:g}"
            return ScreenInfo(0, 0, scale, "manual")
        if (env.get("UI_SCALE_AUTO") or "1").strip().lower() in ("0", "false", "no", "off"):
            return ScreenInfo(0, 0, 1.0, "off")
        size = probe()
        if not size:
            return ScreenInfo(0, 0, 1.0, "failed")
        scale = compute_scale(*size)
        if abs(scale - 1.0) > 0.01:
            env["QT_SCALE_FACTOR"] = f"{scale:g}"
        info = ScreenInfo(size[0], size[1], scale, "auto")
        log.info("Screen %sx%s usable -> interface scale %s (layouts get %sx%s)", size[0], size[1], scale, *info.effective)
        return info
    except Exception:
        log.exception("Screen detection failed - using scale 1")
        return ScreenInfo(0, 0, 1.0, "failed")


def enable_high_dpi() -> None:
    """Make Qt follow Windows display scaling (125 %, 150 % ...) exactly. Call before creating the QApplication."""
    from PySide2.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    try:
        from PySide2.QtGui import QGuiApplication
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
