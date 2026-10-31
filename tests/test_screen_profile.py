"""The app learns its screen and fits itself: the scale maths, the start-up wiring, and that no screen scrolls sideways."""
import os
import subprocess
import sys

import pytest

from shared.uix import screen_profile as sp


@pytest.mark.parametrize("w,h,expected", [
    (1920, 1040, 1.0),       # a normal full-HD monitor (taskbar taken off): left as designed
    (1366, 728, 1.0),        # the common laptop
    (1280, 680, 1.0),        # exactly the design area
    (1280, 640, 0.95),       # a touch short: a touch smaller
    (1024, 728, 0.8),        # an old 1024 x 768 monitor
    (1093, 580, 0.85),       # a 1366 x 768 laptop with Windows scaling at 125 %
    (800, 560, 0.7),         # tiny: never below the floor
    (2560, 1400, 1.35),      # a big monitor: enlarged a little
    (3840, 2100, 1.5),       # 4K at 100 %: capped
])
def test_scale_for_a_screen(w, h, expected):
    assert sp.compute_scale(w, h) == pytest.approx(expected, abs=0.001)


def test_after_scaling_the_layouts_get_at_least_the_design_area():
    for w, h in [(1024, 728), (1093, 580), (1152, 600), (1280, 640), (1366, 728), (800, 560)]:
        s = sp.compute_scale(w, h)
        ew, eh = w / s, h / s
        assert ew >= sp.DESIGN_W - 25 or s == sp.MIN_SCALE
        assert eh >= sp.DESIGN_H - 25 or s == sp.MIN_SCALE


def test_prepare_sets_the_scale_factor_in_the_environment():
    env = {}
    info = sp.prepare_screen_scaling(env, probe=lambda: (1024, 728))
    assert (info.scale, info.source, env["QT_SCALE_FACTOR"]) == (0.8, "auto", "0.8")
    assert info.effective == (1280, 910)
    env = {}
    sp.prepare_screen_scaling(env, probe=lambda: (1920, 1040))
    assert "QT_SCALE_FACTOR" not in env                                     # a normal screen needs nothing


def test_manual_off_and_existing_settings_win():
    env = {"UI_SCALE": "0.9"}
    assert sp.prepare_screen_scaling(env, probe=lambda: (1024, 728)).source == "manual" and env["QT_SCALE_FACTOR"] == "0.9"
    env = {"UI_SCALE_AUTO": "0"}
    assert sp.prepare_screen_scaling(env, probe=lambda: (1024, 728)).scale == 1.0 and "QT_SCALE_FACTOR" not in env
    env = {"QT_SCALE_FACTOR": "1.2"}
    assert sp.prepare_screen_scaling(env, probe=lambda: (1024, 728)).source == "env" and env["QT_SCALE_FACTOR"] == "1.2"


def test_a_failing_probe_leaves_the_interface_alone():
    def boom():
        raise RuntimeError("no screen")
    env = {}
    info = sp.prepare_screen_scaling(env, probe=boom)
    assert info.scale == 1.0 and info.source == "failed" and "QT_SCALE_FACTOR" not in env
    assert sp.prepare_screen_scaling({}, probe=lambda: None).source == "failed"


def test_the_real_probe_then_a_scaled_application_works_in_one_process():
    """Probe with a throw-away QGuiApplication, set the factor, then build the real QApplication - as run() does."""
    code = (
        "import os, sys\n"
        "os.environ['QT_QPA_PLATFORM']='offscreen'\n"
        "from shared.uix.screen_profile import prepare_screen_scaling, enable_high_dpi\n"
        "info = prepare_screen_scaling()\n"
        "enable_high_dpi()\n"
        "from PySide2.QtWidgets import QApplication, QWidget\n"
        "app = QApplication(sys.argv)\n"
        "g = app.primaryScreen().availableGeometry()\n"
        "w = QWidget(); w.resize(400, 300); w.show()\n"
        "print(info.width, info.height, info.scale, g.width(), g.height(), w.width())\n")
    env = {k: v for k, v in os.environ.items() if k not in ("QT_SCALE_FACTOR", "UI_SCALE", "UI_SCALE_AUTO")}
    env["PYTHONPATH"] = os.getcwd()
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    w, h, scale, gw, gh, ww = out.stdout.split()[-6:]
    assert float(scale) < 1.0                                               # the offscreen test screen is small
    assert abs(int(gw) - int(int(w) / float(scale))) <= 2                   # the real app sees the screen scaled
    assert int(ww) == 400                                                   # widgets keep their logical size


def test_window_minimum_never_exceeds_the_usable_screen(qapp):
    from apps.product_management_app.main import ProductManagementMainWindow
    win = ProductManagementMainWindow()
    avail = qapp.primaryScreen().availableGeometry()
    assert win.minimumWidth() <= avail.width() and win.minimumHeight() <= avail.height()
