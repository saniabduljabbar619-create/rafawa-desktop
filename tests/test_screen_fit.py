"""No screen of the app needs a sideways scrollbar on the smallest supported usable area (and on a common laptop)."""
import pytest
from PySide2.QtWidgets import QAbstractScrollArea

from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID

SIZES = [(1280, 680), (1366, 728)]          # usable window sizes (the 1280 x 680 design area, and a 1366 x 768 laptop)


def _sideways(win):
    bad = []
    for sa in win.findChildren(QAbstractScrollArea):
        bar = sa.horizontalScrollBar()
        if sa.isVisibleTo(win) and bar.isVisibleTo(win) and bar.maximum() > 0:
            bad.append(f"{type(sa).__name__} in {type(sa.parent()).__name__} (needs {bar.maximum()}px more)")
    return bad


@pytest.fixture
def window(qapp):
    from apps.product_management_app.main import ProductManagementMainWindow, _FitTables, _FitToScreen
    from shared.uix.theme import apply_theme
    apply_theme(qapp)
    tables, dialogs = _FitTables(qapp), _FitToScreen(qapp)        # the same app-wide helpers run() installs
    qapp.installEventFilter(tables)
    qapp.installEventFilter(dialogs)
    set_session(UserSession(user_id=ADMIN_ID, username="admin", role="admin"))
    win = ProductManagementMainWindow()
    yield win
    qapp.removeEventFilter(tables)
    qapp.removeEventFilter(dialogs)
    win.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_screen_fits_without_a_sideways_scrollbar(qapp, window, size):
    problems = {}
    for key, spec in window.workspaces.items():
        window.open_workspace(key)
        window.resize(*size)
        window.show()
        for route in spec.routes:
            window.navigate(route)
            for _ in range(3):
                qapp.processEvents()
            bad = _sideways(window)
            if bad:
                problems[f"{key}:{route}"] = bad
    assert not problems, "sideways scrolling at %dx%d:\n%s" % (size + ("\n".join(f"  {k}: {v}" for k, v in problems.items()),))
