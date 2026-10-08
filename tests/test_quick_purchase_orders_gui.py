"""Quick Purchases screen: status filters, routing, receiving, the editor and the checkout 'save as pending' choice."""
import pytest
from PySide2.QtWidgets import QApplication, QMessageBox

from database.models.party import Party
from database.services.quick_purchase_order_service import QuickPurchaseOrderService as Q
from database.session import get_session
from tests.conftest import CROP_ID, FERT_ID, SUPPLIER_ID, stock

SUPPLIER_2 = 40


@pytest.fixture(autouse=True)
def second_supplier():
    with get_session() as s:
        s.add(Party(id=SUPPLIER_2, party_type="supplier", display_name="Supplier Two", is_supplier=True))


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


def _lines():
    return [{"product_id": FERT_ID, "qty_bags": 10, "unit_cost": 9500, "supplier_id": SUPPLIER_ID},
            {"product_id": CROP_ID, "total_weight_kg": 300, "qty_bags": 6, "unit_cost": 85, "supplier_id": SUPPLIER_ID}]


def _view():
    from apps.product_management_app.features.cashier.views.quick_purchases import QuickPurchasesView
    return QuickPurchasesView()


def _select(v, oid):
    for r in range(v.table.rowCount()):
        if v.table.item(r, 0).data(0x0100) == oid:
            v.table.selectRow(r)
            QApplication.processEvents()
            return
    raise AssertionError("order not listed")


def test_buttons_follow_the_status(qapp, boxes):
    d, p = Q.create(_lines()), Q.create(_lines(), send=True)
    v = _view()
    _select(v, d)
    assert v.btn_edit.isEnabled() and v.btn_send.isEnabled() and not v.btn_receive.isEnabled()
    assert v.btn_delete.isEnabled()
    _select(v, p)
    assert v.btn_receive.isEnabled() and v.btn_route.isEnabled() and v.btn_stock.isEnabled()
    assert not v.btn_edit.isEnabled() and v.btn_cancel.isEnabled()


def test_send_receive_and_filter(qapp, boxes):
    oid = Q.create(_lines())
    v = _view()
    _select(v, oid)
    v.btn_send.click()
    assert Q.get(oid)["status"] == "pending"
    _select(v, oid)
    v.btn_receive.click()
    assert Q.get(oid)["status"] == "completed" and stock(FERT_ID)[0] == 60
    assert v.detail.rowCount() == 2 and v.detail.item(0, 5).text() == "received"


def test_route_dialog_moves_the_selected_line_to_another_supplier(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.dialogs import quick_purchase_order_dialog as d
    from apps.product_management_app.features.cashier.views import quick_purchases as qv
    oid = Q.create(_lines(), send=True)
    v = _view()
    _select(v, oid)
    v.detail.selectRow(1)                                  # the maize line only
    monkeypatch.setattr(qv.RouteDialog, "exec_", lambda self: self.Accepted)
    monkeypatch.setattr(qv.RouteDialog, "values",
                        lambda self: {"supplier_id": SUPPLIER_2, "note": "no maize", "unit_cost": 90.0})
    v.btn_route.click()
    lines = Q.get(oid)["lines"]
    assert lines[0]["supplier"] == "Supplier One" and lines[1]["supplier"] == "Supplier Two"
    assert lines[1]["unit_cost"] == 90
    assert "Supplier One (unavailable)" in v.detail.item(1, 6).text()
    dlg = d.RouteDialog(None, ["Maize"], "Supplier One", SUPPLIER_ID)
    assert dlg.supplier.findData(SUPPLIER_ID) == -1 and dlg.supplier.findData(SUPPLIER_2) > 0


def test_cancel_stock_and_delete_buttons(qapp, boxes):
    a, b, c = (Q.create(_lines(), send=True) for _ in range(3))
    v = _view()
    _select(v, a)
    v.btn_cancel.click()
    assert Q.get(a)["status"] == "cancelled"
    _select(v, b)
    v.btn_stock.click()
    assert Q.get(b)["status"] == "cancelled" and any("NPK" in m[1] for m in boxes if m[0] == "information")
    _select(v, c)
    v.btn_delete.click()
    assert [o["id"] for o in Q.list_orders()] == [b, a]


def test_editor_builds_a_draft_and_can_send_it(qapp, boxes):
    from apps.product_management_app.features.cashier.dialogs.quick_purchase_order_dialog import (
        QuickPurchaseOrderDialog)
    dlg = QuickPurchaseOrderDialog()
    dlg.customer.setText("Musa")
    dlg.product.setCurrentIndex(next(i for i in range(dlg.product.count()) if dlg.product.itemText(i).startswith("NPK")))
    dlg.bags.setValue(4)
    dlg.cost.setValue(9100)
    dlg.supplier.setCurrentIndex(dlg.supplier.findData(SUPPLIER_ID))
    dlg.btn_add.click()
    assert len(dlg.lines) == 1
    dlg.btn_send.click()
    o = Q.get(dlg.saved_id)
    assert o["status"] == "pending" and o["customer_name"] == "Musa" and o["lines"][0]["qty_bags"] == 4
    d2 = QuickPurchaseOrderDialog()
    d2.btn_draft.click()                                    # no lines: refused, nothing saved
    assert d2.saved_id is None and boxes[-1][0] == "warning"
    d3 = QuickPurchaseOrderDialog(order_id=dlg.saved_id)
    d3.btn_draft.click()                                    # a pending one cannot be re-saved as a draft
    assert boxes[-1][0] == "warning"


def test_checkout_interlude_can_save_the_shortfall_as_pending(qapp, boxes):
    from apps.product_management_app.features.cashier.dialogs.quick_purchase_dialog import (
        DECISION_SAVE, QuickPurchaseDialog)
    from database.services.quick_purchase_service import QuickPurchaseService
    av = QuickPurchaseService.check_availability(
        [{"product_id": FERT_ID, "product_type": "fertilizer", "qty_bags": 80, "total_weight_kg": 0,
          "unit_price": 10000}])
    dlg = QuickPurchaseDialog(av)
    dlg.rb_save.setChecked(True)
    dlg._sync_quick_panel()
    dlg.supplier_cb.setCurrentIndex(dlg.supplier_cb.findData(SUPPLIER_ID))
    dlg._cost_spins[FERT_ID].setValue(9300)
    dlg._accept()
    ch = dlg.decision()
    assert ch["decision"] == DECISION_SAVE and ch["supplier_party_id"] == SUPPLIER_ID
    assert ch["selected_short"][0]["short_qty_bags"] == 30
