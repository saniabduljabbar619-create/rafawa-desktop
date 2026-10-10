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
    assert v.btn_edit.isEnabled() and v.btn_cancel.isEnabled()          # pending orders stay editable


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


def _sales_view():
    from apps.product_management_app.features.cashier.views.cashier_sales import CashierSalesView
    return CashierSalesView()


def test_saving_the_shortfall_keeps_the_cart_and_the_order_rebuilds_it(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.views import cashier_sales as cs
    v = _sales_view()
    v.cart = [cs.CartItem(product_id=FERT_ID, product_type="fertilizer", name="NPK", unit_price=10000,
                          unit_cost_at_sale=9000, qty_bags=80, total_weight_kg=0)]
    short = {"product_id": FERT_ID, "product_type": "fertilizer", "short_qty_bags": 30, "short_weight_kg": 0}

    class FakeDialog:
        def __init__(self, *a, **k):
            pass
        Accepted = 1

        def exec_(self):
            return 1

        def decision(self):
            return {"decision": cs.DECISION_SAVE, "supplier_party_id": SUPPLIER_ID, "selected_short": [short],
                    "unselected_short": [], "dealer_cost_by_pid": {FERT_ID: 9300}, "payment_intention": "credit"}

    monkeypatch.setattr(cs, "QuickPurchaseDialog", FakeDialog)
    assert v._maybe_quick_purchase() is False                 # checkout stops, the cart stays
    assert len(v.cart) == 1
    (o,) = Q.list_orders("pending")
    assert o["cart"][0]["qty_bags"] == 80 and o["lines"][0]["qty_bags"] == 30 and o["lines"][0]["unit_cost"] == 9300

    # goods arrive; a fresh Sales Console brings the whole sale back
    Q.receive(o["id"])
    v2 = _sales_view()
    assert v2._load_quick_purchase_cart(Q.get(o["id"])) is True
    assert [(c.product_id, c.qty_bags, c.unit_price) for c in v2.cart] == [(FERT_ID, 80, 10000)]
    assert v2.total_lbl.text().endswith("800,000.00")


def test_load_button_only_for_finished_orders_with_a_cart(qapp, boxes):
    loaded = []
    cart = [{"product_id": FERT_ID, "product_type": "fertilizer", "name": "NPK", "unit_price": 1,
             "unit_cost_at_sale": 1, "qty_bags": 2, "total_weight_kg": 0}]
    oid = Q.create(_lines(), send=True, cart=cart)
    from apps.product_management_app.features.cashier.views.quick_purchases import QuickPurchasesView
    v = QuickPurchasesView(load_callback=loaded.append)
    _select(v, oid)
    assert not v.btn_load.isEnabled()                         # still pending: receive or close it first
    v.btn_receive.click()
    _select(v, oid)
    assert v.btn_load.isEnabled()
    v.btn_load.click()
    assert loaded and loaded[0]["id"] == oid


def _standalone(qapp, product_id, qty, bags=0, cost=9300, supplier=SUPPLIER_ID):
    from apps.product_management_app.features.cashier.dialogs.standalone_quick_purchase_dialog import (
        StandaloneQuickPurchaseDialog)
    dlg = StandaloneQuickPurchaseDialog()
    meta = next(m for m in dlg._row_meta.values() if m["product_id"] == product_id)
    meta["chk"].setChecked(True)
    meta["qty_spin"].setValue(qty)
    if bags:
        meta["bags_spin"].setValue(bags)
    meta["cost_spin"].setValue(cost)
    assert meta["supplier_cb"].currentData() == supplier or supplier is None   # a real supplier is pre-picked
    return dlg


def test_standalone_dialog_adds_to_cart_with_a_preselected_supplier_and_crop_bags(qapp, boxes):
    dlg = _standalone(qapp, CROP_ID, 300)
    assert dlg.btn_ok.text() == "Add to Cart"
    dlg.btn_ok.click()
    assert boxes[-1][0] == "warning" and "bags" in boxes[-1][1]       # a crop needs its bag count
    dlg.table.cellWidget(0, 0)  # (rows exist)
    meta = next(m for m in dlg._row_meta.values() if m["product_id"] == CROP_ID)
    meta["bags_spin"].setValue(6)
    dlg.btn_ok.click()
    assert dlg.result() == dlg.Accepted
    (line,) = dlg.new_cart_lines
    assert line.pop("key") == dlg.pending_quick_purchases[0]["short_lines"][0]["key"]   # ties row to its plan
    assert line == {"product_id": CROP_ID, "product_type": "crops", "name": "Maize", "qty": 300.0, "bags": 6,
                    "unit_cost": 9300.0}
    assert dlg.pending_quick_purchases[0]["short_lines"][0]["qty_bags"] == 6


def _cart_with_planned_fertilizer(qapp, monkeypatch):
    from apps.product_management_app.features.cashier.views import cashier_sales as cs
    v = _sales_view()
    v.walkin_name.setText("Musa")
    v.cart = [cs.CartItem(product_id=FERT_ID, product_type="fertilizer", name="NPK", unit_price=10000,
                          unit_cost_at_sale=9000, qty_bags=20, total_weight_kg=0)]     # from our own stock
    v._pending_standalone_qp = [{"supplier_party_id": SUPPLIER_ID, "short_lines": [
        {"key": "k1", "product_id": FERT_ID, "product_type": "fertilizer", "qty_bags": 30, "total_weight_kg": 0.0,
         "unit_cost": 9300}]}]
    v.cart.append(cs.CartItem(product_id=FERT_ID, product_type="fertilizer", name="NPK", unit_price=10000,
                              unit_cost_at_sale=9300, qty_bags=30, total_weight_kg=0, qp_key="k1"))
    return v


def _fake_checkout(monkeypatch, v):
    """Stand in for the checkout / payment dialogs - the rest of the real flow runs."""
    from apps.product_management_app.features.cashier.views import cashier_sales as cs

    class Checkout:
        Accepted = 1

        def __init__(self, *a, **k):
            self.total = k.get("total", 0)

        def exec_(self):
            return 1

        def payload(self):
            return {"discount": 0.0, "grand_subtotal": self.total, "rate_overrides": {}, "discount_overrides": {},
                    "note": ""}

    class Payment:
        def __init__(self, *a, **k):
            pass

        def exec_(self):
            return 1

    monkeypatch.setattr(cs, "SaleCheckoutDialog", Checkout)
    monkeypatch.setattr(cs, "SalePaymentDialog", Payment)
    import apps.product_management_app.features.profiling.dialogs.advance_choice_dialog as adv
    monkeypatch.setattr(adv, "prompt_and_apply_advance", lambda *a, **k: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))


def test_status_between_carting_and_checkout_pending_continues_and_the_order_is_completed(qapp, boxes, monkeypatch):
    from database.models.sale_item import SaleItem
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    _fake_checkout(monkeypatch, v)
    seen = {}
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: seen.setdefault("s", "pending"))
    v._checkout()
    (o,) = Q.list_orders()
    assert o["status"] == "completed" and o["sale_id"] and o["lines"][0]["state"] == "received"
    assert [(c["qty_bags"], c["qp"]) for c in o["cart"]] == [(20, False), (30, True)]
    assert v.cart == [] and v._qp_order_id is None and stock(FERT_ID)[0] == 30          # 50 + 30 bought - 50 sold
    with get_session() as s:
        costs = sorted((float(i.unit_cost_snapshot), i.qty_bags) for i in s.query(SaleItem))
    assert costs == [(9000.0, 20), (9300.0, 30)]               # stock lot at book cost, bought lot at the dealer's


@pytest.mark.parametrize("choice,status", [("draft", "draft")])
def test_checkout_can_save_standalone_quick_purchase_as_draft(qapp, boxes, monkeypatch, choice, status):
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: choice)
    v._checkout()
    (o,) = Q.list_orders(status)
    assert o["lines"][0]["qty_bags"] == 30 and o["lines"][0]["supplier_id"] == SUPPLIER_ID
    assert [(c["product_id"], c["qty_bags"]) for c in o["cart"]] == [(FERT_ID, 20), (FERT_ID, 30)]
    assert v.cart == [] and v._pending_standalone_qp == [] and v._qp_order_id is None
    assert stock(FERT_ID)[0] == 50                                           # nothing bought yet


def test_removing_a_quick_purchase_cart_row_drops_its_planned_purchase_and_order_line(qapp, boxes, monkeypatch):
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: "pending")
    assert v._save_quick_purchase_order("pending") is True
    oid = v._qp_order_id
    v.cart_table.setRowCount(2)
    v.cart_table.selectRow(1)                                  # the Quick Purchase row
    v._remove_selected()
    assert [c.qp_key for c in v.cart] == [""] and v._pending_standalone_qp == []
    assert Q.get(oid)["status"] == "cancelled" and v._qp_order_id is None
    # a cart row from our own stock never touches a plan
    v2 = _cart_with_planned_fertilizer(qapp, monkeypatch)
    v2.cart_table.setRowCount(2)
    v2.cart_table.selectRow(0)
    v2._remove_selected()
    assert len(v2._pending_standalone_qp) == 1


def test_a_failed_sale_keeps_the_plan_and_buys_nothing(qapp, boxes, monkeypatch):
    from database.services.sales_service import SaleService
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    _fake_checkout(monkeypatch, v)
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: "pending")
    monkeypatch.setattr(SaleService, "record", staticmethod(lambda payload: (_ for _ in ()).throw(ValueError("boom"))))
    v._checkout()
    assert stock(FERT_ID)[0] == 50 and len(v._pending_standalone_qp) == 1 and len(v.cart) == 2
    assert [o["status"] for o in Q.list_orders()] == ["pending"] and v._qp_order_id


def test_a_pending_order_edited_elsewhere_comes_back_live_into_the_cart(qapp, boxes, monkeypatch):
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: "pending")
    v._save_quick_purchase_order("pending")
    oid = v._qp_order_id
    (ln,) = Q.get(oid)["lines"]
    Q.edit_line(oid, ln["id"], qty=12, unit_cost=9400, supplier_id=SUPPLIER_2)       # the supplier has only 12
    Q.drop_line(oid, Q.get(oid)["lines"][1]["id"], "nobody has the rest")
    v2 = _sales_view()
    assert v2._load_quick_purchase_cart(Q.get(oid)) is True
    assert [(c.qty_bags, c.unit_cost_at_sale, bool(c.qp_key)) for c in v2.cart] == [(20, 9000, False),
                                                                                    (12, 9400, True)]
    assert v2._qp_order_id == oid and v2._pending_standalone_qp[0]["supplier_party_id"] == SUPPLIER_2
    assert v2.cart[1].unit_price == 10000                                        # price carried from the cart
    _fake_checkout(monkeypatch, v2)
    v2.walkin_name.setText("Musa")
    v2._checkout()                                                               # straight to checkout, no status ask
    assert Q.get(oid)["status"] == "completed" and stock(FERT_ID)[0] == 50 + 12 - 32


def test_checkout_back_to_cart_changes_nothing(qapp, boxes, monkeypatch):
    v = _cart_with_planned_fertilizer(qapp, monkeypatch)
    monkeypatch.setattr(type(v), "_ask_quick_purchase_status", lambda self: None)
    v._checkout()
    assert Q.list_orders() == [] and len(v.cart) == 2 and v._pending_standalone_qp


# ---------------------------------------------------------------- edit lines / confirm from the screen
def _sourcing(bags=30, supplier=SUPPLIER_ID):
    cart = [{"product_id": FERT_ID, "product_type": "fertilizer", "name": "NPK", "unit_price": 10000,
             "unit_cost_at_sale": 9000, "qty_bags": 80, "total_weight_kg": 0}]
    return Q.create([{"product_id": FERT_ID, "qty_bags": bags, "unit_cost": 9300, "supplier_id": supplier}],
                    send=True, cart=cart, buyer_party_id=1, customer_name="Customer One")


def test_edit_line_dialog_splits_the_order_and_routes_the_rest(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.views import quick_purchases as qv
    oid = _sourcing()
    v = _view()
    _select(v, oid)
    assert not v.btn_line.isEnabled()
    v.detail.selectRow(0)
    assert v.btn_line.isEnabled() and v.btn_drop.isEnabled()
    monkeypatch.setattr(qv.LineEditDialog, "exec_", lambda self: self.Accepted)
    monkeypatch.setattr(qv.LineEditDialog, "values", lambda self: {"qty": 12.0, "unit_cost": 9400.0,
                                                                   "supplier_id": SUPPLIER_ID, "note": ""})
    v.btn_line.click()
    lines = Q.get(oid)["lines"]
    assert [(x["qty_bags"], x["assigned"]) for x in lines] == [(12, True), (18, False)]
    assert v.detail.item(1, 5).text() == "needs a supplier"
    monkeypatch.setattr(qv.LineEditDialog, "values", lambda self: {"qty": 18.0, "unit_cost": 9500.0,
                                                                   "supplier_id": SUPPLIER_2, "note": "price"})
    v.detail.selectRow(1)
    v.btn_line.click()
    assert Q.get(oid)["lines"][1]["supplier"] == "Supplier Two"
    v.detail.selectRow(0)
    v.btn_drop.click()
    assert Q.get(oid)["lines"][0]["state"] == "dropped"


def test_line_edit_dialog_reads_back_what_was_typed(qapp):
    from apps.product_management_app.features.cashier.dialogs.quick_purchase_order_dialog import LineEditDialog
    oid = _sourcing()
    ln = Q.get(oid)["lines"][0]
    dlg = LineEditDialog(None, ln)
    assert dlg.qty.value() == 30 and dlg.supplier.currentData() == SUPPLIER_ID
    dlg.qty.setValue(20)
    dlg.supplier.setCurrentIndex(dlg.supplier.findData(0))
    assert dlg.values()["qty"] == 20 and dlg.values()["supplier_id"] == 0


def test_confirm_runs_the_consoles_checkout_then_payment_dialogs(qapp, boxes, monkeypatch):
    from database.models.payment import Payment
    from apps.product_management_app.features.cashier.views import quick_purchases as qv
    oid = _sourcing()
    seen = {}

    class Checkout:
        Accepted = 1

        def __init__(self, parent, **k):
            seen["lines"], seen["total"] = k["lines"], k["total"]

        def exec_(self):
            return 1

        def payload(self):                      # price 10,500 each, a 5,000 discount, 300,000 paid now
            return {"rate_overrides": {FERT_ID: 10500.0}, "discount_overrides": {FERT_ID: 5000.0}, "discount": 5000.0,
                    "payment_mode": "paid", "amount_paid": 300000.0, "method": "transfer", "note": "urgent"}

    class SalePay:
        def __init__(self, **k):
            seen["sale_pay_for"] = k["party"].display_name

        def exec_(self):
            seen["sale_pay_opened"] = True

    class BuyPay:
        def __init__(self, **k):
            seen.setdefault("buy_pay_for", []).append(k["party"].display_name)

        def exec_(self):
            pass

    monkeypatch.setattr(qv, "SaleCheckoutDialog", Checkout)
    monkeypatch.setattr(qv, "SalePaymentDialog", SalePay)
    monkeypatch.setattr(qv, "PurchasePaymentDialog", BuyPay)
    monkeypatch.setattr(qv, "prompt_and_apply_advance", lambda *a, **k: seen.setdefault("advance", k["balance"]))
    v = _view()
    _select(v, oid)
    assert v.btn_confirm.isEnabled()
    v.btn_confirm.click()
    (line,) = seen["lines"]
    assert (line["bags"], line["rate"], seen["total"]) == (80, 10000, 800000)          # the order's cart, as sold
    o = Q.get(oid)
    assert o["status"] == "completed" and o["sale_id"]
    with get_session() as s:
        from database.models.sale import Sale
        sale = s.get(Sale, o["sale_id"])
        assert float(sale.total_amount) == 80 * 10500 - 5000 and float(sale.discount) == 5000
        pays = {(p.entity_type, p.direction, p.method): float(p.amount) for p in s.query(Payment)}
    assert pays == {("sale", "in", "transfer"): 300000.0}                              # "paid now" is recorded
    assert seen["advance"] == 535000.0 and seen["sale_pay_opened"] and seen["sale_pay_for"] == "Customer One"
    assert seen["buy_pay_for"] == ["Supplier One"]                                      # suppliers: asked, paid there
    assert not v.btn_confirm.isEnabled()                                                # already sold


def test_confirm_back_out_of_checkout_changes_nothing(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.views import quick_purchases as qv
    oid = _sourcing()

    class Checkout:
        Accepted = 1

        def __init__(self, *a, **k):
            pass

        def exec_(self):
            return 0

    monkeypatch.setattr(qv, "SaleCheckoutDialog", Checkout)
    v = _view()
    _select(v, oid)
    v.btn_confirm.click()
    assert Q.get(oid)["status"] == "pending" and stock(FERT_ID)[0] == 50


def test_editor_opens_a_pending_order_and_saves_changes(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.dialogs import quick_purchase_order_dialog as d
    oid = _sourcing()
    dlg = d.QuickPurchaseOrderDialog(order_id=oid)
    assert dlg.pending and dlg.btn_send.text() == "Save Changes" and not dlg.btn_draft.isVisible()
    assert len(dlg.lines) == 1 and dlg.lines[0]["id"]
    monkeypatch.setattr(d.LineEditDialog, "exec_", lambda self: self.Accepted)
    monkeypatch.setattr(d.LineEditDialog, "values", lambda self: {"qty": 18.0, "unit_cost": 9500.0,
                                                                  "supplier_id": SUPPLIER_2, "note": ""})
    dlg._edit_line(0)                                          # double-click the row
    dlg.customer.setText("Musa Farms")
    dlg.btn_send.click()                                       # Save Changes
    o = Q.get(oid)
    assert o["status"] == "pending" and o["customer_name"] == "Musa Farms"
    assert [(x["qty_bags"], x["unit_cost"], x["supplier"]) for x in o["lines"]] == [(18, 9500, "Supplier Two")]
    d2 = d.QuickPurchaseOrderDialog(order_id=oid)
    d2.table.selectRow(0)
    d2.btn_remove.click()                                      # removing the only line is refused
    d2.btn_send.click()
    assert boxes[-1][0] == "warning" and Q.get(oid)["lines"][0]["state"] == "open"
