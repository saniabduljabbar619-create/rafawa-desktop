"""
GUI smoke tests: build every screen of both workspaces against a seeded database,
headless (Qt 'offscreen' platform). Catches import errors, constructor crashes and
broken queries in the views that unit tests on the services cannot see.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide2")

from PySide2.QtWidgets import QApplication, QWidget  # noqa: E402

from database.services.advance_service import AdvanceService  # noqa: E402
from database.services.purchase_service import PurchaseService  # noqa: E402
from tests.conftest import (ADMIN_ID, CASHIER_A, CUSTOMER_ID, SUPPLIER_ID, make_sale, pay)  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    from shared.uix.theme import apply_theme
    apply_theme(app)
    return app


@pytest.fixture
def world(qapp):
    """Some real activity so tables/summary cards have something to render."""
    sale = make_sale()
    pay("sale", sale["sale_id"], CUSTOMER_ID, "in", 5000)
    PurchaseService.record({"supplier_party_id": SUPPLIER_ID, "created_by_user_id": CASHIER_A,
                            "items": [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
                                       "qty_bags": 4, "total_weight_kg": 400, "unit_cost": 80}]})
    AdvanceService.record_advance_in(CUSTOMER_ID, 3000, ADMIN_ID, notes="deposit")
    # the in-memory caches the screens read from
    from apps.product_management_app.app_state import state
    state._load_customers_from_db()
    state._load_walkins_from_db()
    return sale


def _all_routes():
    from apps.product_management_app.workspaces import build_workspaces
    specs = build_workspaces()
    return [(key, route) for key, spec in specs.items() for route in spec.routes]


@pytest.mark.parametrize("workspace,route", _all_routes(), ids=lambda v: str(v))
def test_every_route_builds_and_renders(qapp, world, workspace, route):
    from apps.product_management_app.workspaces import build_workspaces
    widget = build_workspaces()[workspace].routes[route]()
    assert isinstance(widget, QWidget)
    widget.resize(1100, 700)
    widget.show()
    qapp.processEvents()
    widget.close()
    widget.deleteLater()


# ---------------------------------------------------------------- deeper interaction
@pytest.fixture
def no_modals(monkeypatch):
    """Message boxes would block a headless run: answer them automatically and record them."""
    from PySide2.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a)) or QMessageBox.Yes))
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a)) or QMessageBox.Ok))
    return shown


def _show(qapp, w):
    w.resize(1100, 700)
    w.show()
    qapp.processEvents()


@pytest.mark.parametrize("mode", ["admin", "cashier"])
def test_customer_profile_fills_every_tab(qapp, world, mode):
    from apps.product_management_app.app_state import state
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
    customer = next(c for c in state.customers if c.id == CUSTOMER_ID)
    view = CustomerProfileView(customer=customer, mode=mode)
    _show(qapp, view)
    assert view.activity_table.rowCount() >= 2            # the sale and its payment
    assert view.payments_table.rowCount() >= 1
    assert view.advances_table.rowCount() == 1
    assert "20,000.00" in view.sum_receivable.text() or "15,000.00" in view.sum_receivable.text()
    # a second refresh must not raise or duplicate rows (operator cache is reset)
    before = view.activity_table.rowCount()
    view.refresh_all()
    assert view.activity_table.rowCount() == before
    assert hasattr(view, "revert_table") == (mode != "cashier")


def test_walkin_profile_renders(qapp, world):
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
    make_sale(buyer=0, walkin_name="Musa")
    view = CustomerProfileView(customer=None, mode="admin", walkin_name="Musa")
    _show(qapp, view)
    assert view.activity_table.rowCount() == 1


def test_returns_screen_creates_a_return_through_the_ui(qapp, world, no_modals):
    from database.models.sale_return import SaleReturn
    from database.session import get_session
    from apps.product_management_app.features.profiling.views.admin_returns import AdminReturnsView
    view = AdminReturnsView(preselect_party_id=CUSTOMER_ID)
    _show(qapp, view)
    assert view.sales_table.rowCount() == 1
    view.sales_table.cellWidget(0, 0).setChecked(True)          # tick the sale -> items load
    assert view.items_table.rowCount() == 1
    view.items_table.cellWidget(0, 0).setChecked(True)
    view.items_table.cellWidget(0, 10).setValue(1)              # bags
    view.items_table.cellWidget(0, 11).setValue(100.0)          # kg
    view.submit_btn.click()
    with get_session() as s:
        assert s.query(SaleReturn).count() == 1
        assert abs(float(s.query(SaleReturn).one().amount)) == 10000.0
    assert any(kind == "information" for kind, _ in no_modals)  # "Success" dialog


def test_drawer_panel_and_screen_render(qapp, world):
    from apps.product_management_app.features.admin_users.views.drawer_screen import DrawerPanel, DrawerScreen
    panel = DrawerPanel(CASHIER_A)
    _show(qapp, panel)
    screen = DrawerScreen(CASHIER_A)
    _show(qapp, screen)
    screen.close()


def test_business_history_and_party_dialogs_render(qapp, world):
    from apps.product_management_app.features.admin_users.dialogs.party_totals_dialog import PartyTotalsDialog
    from apps.product_management_app.features.business_history.dialogs.cashier_business_history_dialog import (
        CashierBusinessHistoryDialog)
    for dlg in (PartyTotalsDialog(CASHIER_A, "alice"), CashierBusinessHistoryDialog(CASHIER_A, "alice")):
        _show(qapp, dlg)
        dlg.close()


def test_cashier_sales_lists_products_for_the_chosen_category(qapp, world):
    from apps.product_management_app.features.cashier.views.cashier_sales import CashierSalesView
    view = CashierSalesView()
    _show(qapp, view)
    view.category_cb.setCurrentText("Crops")
    names = [view.prod_table.item(r, 0).text() for r in range(view.prod_table.rowCount())]
    assert names == ["Maize"]
    view.category_cb.setCurrentText("Fertilizer")
    assert [view.prod_table.item(r, 0).text() for r in range(view.prod_table.rowCount())] == ["NPK"]


def test_login_screen_shows_lockout_message(qapp, world):
    from apps.product_management_app.launcher import LauncherView
    from database.models.user import User
    from database.session import get_session
    from shared.utils.password_hash import hash_password
    from shared.auth import throttle
    from shared.auth.session import set_session
    with get_session() as s:
        s.get(User, CASHIER_A).password_hash = hash_password("right-pass")
    set_session(None)
    view = LauncherView()
    _show(qapp, view)
    view.username.setText("alice")
    for _ in range(throttle.MAX_FREE_FAILURES):
        view.password.setText("wrong")
        view._attempt_login()
    view.password.setText("right-pass")
    view._attempt_login()                                       # correct, but locked out
    assert "Try again in" in view.auth_error.text()


# ---------------------------------------------------------------- developer tool
@pytest.mark.skipif(not os.getenv("RAFAWA_SCREENSHOT_DIR"), reason="set RAFAWA_SCREENSHOT_DIR to capture screens")
def test_capture_screenshots(qapp, world):
    """Render every screen to a PNG (visual review of UI changes):
    RAFAWA_SCREENSHOT_DIR=/tmp/shots QT_QPA_PLATFORM=offscreen pytest tests/test_gui_smoke.py -k screenshots"""
    from pathlib import Path
    out = Path(os.environ["RAFAWA_SCREENSHOT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    from apps.product_management_app.workspaces import build_workspaces
    from apps.product_management_app.launcher import LauncherView
    from shared.auth.session import set_session
    for workspace, spec in build_workspaces().items():
        for route, factory in spec.routes.items():
            w = factory()
            w.resize(1280, 800)
            w.show()
            qapp.processEvents()
            w.grab().save(str(out / f"{workspace}_{route}.png"))
            w.close()
    set_session(None)
    launcher = LauncherView()
    launcher.resize(1280, 800)
    launcher.show()
    qapp.processEvents()
    launcher.grab().save(str(out / "launcher_login.png"))


def test_reports_screen_shows_figures_and_exports(qapp, world, no_modals, monkeypatch, tmp_path):
    from PySide2.QtWidgets import QFileDialog
    from apps.product_management_app.features.reports.views.reports_view import ReportsView
    view = ReportsView()
    _show(qapp, view)
    summary = {view.summary.item(r, 0).text(): view.summary.item(r, 1).text() for r in range(view.summary.rowCount())}
    assert summary["Sales - gross"] == "20,000.00" and summary["Cash received"] == "5,000.00"
    assert summary["Sales (documents)"] == "1"
    assert view.receivables.item(0, 0).text() == "Customer One"
    assert view.receivables.item(view.receivables.rowCount() - 1, 0).text() == "TOTAL"
    assert view.stock.rowCount() >= 2
    out = tmp_path / "x.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    view.btn_export.click()
    assert out.exists() and any(k == "information" for k, _ in no_modals)


@pytest.mark.skipif(not os.getenv("RAFAWA_SCREENSHOT_DIR"), reason="set RAFAWA_SCREENSHOT_DIR to capture screens")
def test_capture_full_window_screenshots(qapp, world):
    """The whole main window (sidebar + top bar + a screen) for both workspaces."""
    from pathlib import Path
    from apps.product_management_app.main import ProductManagementMainWindow
    out = Path(os.environ["RAFAWA_SCREENSHOT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    win = ProductManagementMainWindow()
    win.resize(1360, 820)
    win.show()
    qapp.processEvents()
    for workspace, routes in (("pm", ["admin.dashboard", "profiles.customers", "catalog.products", "admin.reports"]),
                              ("cashier", ["cashier.sales", "cashier.payments"])):
        win.open_workspace(workspace)
        for route in routes:
            win.navigate(route)
            qapp.processEvents()
            win.grab().save(str(out / f"window_{workspace}_{route}.png"))
    win.close()
