"""The unified Orders menu: one entry, tracking board, both order kinds, shared partners."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.services.company_service import CompanyService
from database.services.purchase_order_service import PurchaseOrderService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.services.sales_order_service import SalesOrderService
from tests.conftest import ADMIN_ID, CROP_ID, FERT_ID


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    return shown


def _orders():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    so = SalesOrderService.create_draft(c, [{"product_id": CROP_ID, "dispatched_weight_kg": 500,
                                             "dispatched_qty_bags": 5, "my_price": 110}], ADMIN_ID)
    po = PurchaseOrderService.create_draft(p, [{"product_id": FERT_ID, "ordered_qty_bags": 10,
                                                "expected_price": 9000}], ADMIN_ID)
    SalesOrderService.dispatch(so, ADMIN_ID)
    return so, po


def test_sidebar_has_a_single_orders_entry_in_both_workspaces(qapp):
    from apps.product_management_app.workspaces import build_workspaces
    ws = build_workspaces()
    for key, route in (("pm", "admin.orders"), ("cashier", "cashier.orders")):
        labels = [i.label for i in ws[key].menu]
        assert labels.count("Orders") == 1 and "Sales Orders" not in labels and "Purchase Orders" not in labels
        order_item = next(i for i in ws[key].menu if i.label == "Orders")
        assert order_item.route == route and not order_item.children
        assert ws[key].routes[route]() is not None


def test_board_shows_both_kinds_and_kpis(qapp):
    _orders()
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    v = OrdersHubView()
    assert v.board.table.rowCount() == 2
    assert v.kpi_sales.value.text() == "1" and v.kpi_purchase.value.text() == "0"
    kinds = {v.board.table.item(r, 0).text() for r in range(2)}
    assert kinds == {"Sales", "Purchase"}
    v.board.kind_seg.set_key("sales")
    v.board.reload()
    assert v.board.table.rowCount() == 1 and v.board.table.item(0, 2).text() == "Acme Foods"
    v.board.kind_seg.set_key("")
    v.board.status_seg.set_key("draft")
    v.board.reload()
    assert v.board.table.rowCount() == 1 and v.board.table.item(0, 0).text() == "Purchase"
    v.board.search.setText("nobody")
    assert v.board.table.rowCount() == 0


def test_tabs_switch_between_board_companies_and_suppliers(qapp):
    _orders()
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    v = OrdersHubView()
    v.board.btn_new_sales.click()
    assert v.tabs.key() == "sales" and v.stack.currentWidget() is v._pages["sales"]
    assert v._pages["sales"].table.rowCount() == 2                 # Acme + mirrored Farm Co
    v.board.btn_new_purchase.click()
    assert v.tabs.key() == "purchase" and v._pages["purchase"].table.rowCount() == 2
    v.tabs.set_key("board")
    v.show_tab("board")
    assert v.stack.currentWidget() is v.board


def test_partners_added_before_the_hub_existed_are_shared_when_it_opens(qapp):
    from database.models.company import Company
    from database.models.purchase_order_supplier import PurchaseOrderSupplier
    from database.session import get_session
    with get_session() as s:
        s.add(Company(name="Legacy Buyer", created_by_user_id=ADMIN_ID))
        s.add(PurchaseOrderSupplier(name="Legacy Seller", created_by_user_id=ADMIN_ID))
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    v = OrdersHubView()
    assert {c["name"] for c in PurchaseOrderSupplierService.search("")} == {"Legacy Buyer", "Legacy Seller"}
    assert {c["name"] for c in CompanyService.search("")} == {"Legacy Buyer", "Legacy Seller"}
    assert v is not None


def test_track_shows_history_and_requires_a_selection(qapp, boxes):
    so, _po = _orders()
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    v = OrdersHubView()
    v.board.btn_track.click()
    assert boxes[-1] == ("information", "Select an order first.")
    v.board.status_seg.set_key("in_transit")
    v.board.reload()
    v.board.table.selectRow(0)
    v.board.btn_track.click()
    msg = boxes[-1][1]
    assert "Created (draft)" in msg and "Dispatched" in msg


def test_double_click_opens_the_matching_detail_dialog(qapp, monkeypatch):
    so, po = _orders()
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    from apps.product_management_app.features.purchase_orders.dialogs import purchase_order_detail_dialog as pd
    from apps.product_management_app.features.sales_orders.dialogs import sales_order_detail_dialog as sd
    opened = []
    monkeypatch.setattr(sd.SalesOrderDetailDialog, "exec_", lambda self: opened.append(("sales", self)) or 0)
    monkeypatch.setattr(pd.PurchaseOrderDetailDialog, "exec_", lambda self: opened.append(("purchase", self)) or 0)
    v = OrdersHubView()
    for kind in ("sales", "purchase"):
        v.board.kind_seg.set_key(kind)
        v.board.reload()
        v.board.table.selectRow(0)
        v.board.table.cellDoubleClicked.emit(0, 0)
    assert [k for k, _ in opened] == ["sales", "purchase"]
