"""Customer profile: Sales / Purchases / Products / Transaction history tabs and the swap page."""
import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox

from database.services.customer_records_service import CustomerRecordsService as R
from database.services.document_balance import document_balances
from database.services.offset_service import SwapService as SW
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import CASHIER_A, CROP_ID, CUSTOMER_ID, FERT_ID, make_sale, pay, stock
from tests.test_offsets import _p


def _bal(kind, doc):
    with get_session() as s:
        return document_balances(s, kind, [doc])[doc]


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    return shown


# ----------------------------------------------------------------- the lists
def test_sales_and_purchases_lists_have_balances_and_selectable_flags():
    s1 = make_sale()["sale_id"]                              # 20,000
    s2 = make_sale()["sale_id"]
    pay("sale", s2, CUSTOMER_ID, "in", 20000)                # fully paid
    p = _p(CUSTOMER_ID, 500, 100)                            # 50,000 purchase from him
    sales = {d["id"]: d for d in R.sales(CUSTOMER_ID)}
    assert sales[s1]["balance"] == 20000 and sales[s1]["selectable"] and sales[s1]["ref"] == f"SAL-{s1}"
    assert sales[s2]["status"] == "paid" and not sales[s2]["selectable"]
    assert "Maize" in sales[s1]["summary"]
    purchases = R.purchases(CUSTOMER_ID)
    assert [d["id"] for d in purchases] == [p] and purchases[0]["balance"] == 50000


def test_products_tab_shows_bought_and_sold_per_product():
    make_sale()                                              # sold 200 kg maize @100 = 20,000
    _p(CUSTOMER_ID, 500, 100)                                # bought 500 kg maize @100 = 50,000
    rows = {r["name"]: r for r in R.products(CUSTOMER_ID)}
    m = rows["Maize"]
    assert m["sold_value"] == 20000 and m["bought_value"] == 50000 and m["net"] == -30000
    assert m["sold_kg"] == 200 and m["bought_kg"] == 500


def test_history_is_in_date_order_with_a_running_position():
    s1 = make_sale()["sale_id"]
    pay("sale", s1, CUSTOMER_ID, "in", 5000)
    p = _p(CUSTOMER_ID, 100, 100)                            # 10,000 we owe him
    ev = R.history(CUSTOMER_ID)
    assert [e["type"] for e in ev] == ["Sale", "Payment in", "Purchase"]
    assert [e["running"] for e in ev] == [20000, 15000, 5000]
    SW.record_from_records(CUSTOMER_ID, [p], [s1], [], [], "")  # sets his purchase against his sale
    assert R.history(CUSTOMER_ID)[-1]["type"] == "Swap"


# ----------------------------------------------------------------- swap from ticked records
def _give(kg=100, price=100):
    return [{"product_id": CROP_ID, "qty_bags": 1, "total_weight_kg": kg, "unit_price": price}]


def test_swap_ticked_purchases_against_new_goods_we_give():
    """We bought goods from him (unpaid, 50,000); he comes back and takes goods worth 30,000."""
    p = _p(CUSTOMER_ID, 500, 100)
    bags, kg = stock(CROP_ID)
    r = SW.record_from_records(CUSTOMER_ID, [p], [], [], _give(kg=300), "maize for maize")
    assert r["sale_id"] and r["purchase_id"] is None
    assert r["offset"] == 30000 and r["we_owe_him"] == 20000 and r["he_owes_us"] == 0
    assert _bal("purchase", p).balance == 20000 and _bal("sale", r["sale_id"]).balance == 0
    assert stock(CROP_ID)[1] == kg - 300 + 0                   # the goods left the shelf


def test_swap_ticked_sales_against_new_goods_he_brings():
    s1 = make_sale()["sale_id"]                              # he owes 20,000
    recv = [{"product_name": "Maize", "sku": "CROP-MAIZE", "product_type": "crops", "qty_bags": 5,
             "total_weight_kg": 300, "unit_cost": 100}]       # brings 30,000 of goods
    r = SW.record_from_records(CUSTOMER_ID, [], [s1], recv, [], "")
    assert r["purchase_id"] and r["sale_id"] is None
    assert _bal("sale", s1).balance == 0 and _bal("purchase", r["purchase_id"]).balance == 10000


def test_swap_mixes_ticked_records_and_new_goods_on_both_sides():
    s1 = make_sale()["sale_id"]                              # 20,000 owed by him
    p = _p(CUSTOMER_ID, 100, 100)                            # 10,000 owed to him
    recv = [{"product_name": "Maize", "sku": "CROP-MAIZE", "product_type": "crops", "qty_bags": 5,
             "total_weight_kg": 100, "unit_cost": 100}]       # +10,000 he brings
    r = SW.record_from_records(CUSTOMER_ID, [p], [s1], recv, _give(kg=100, price=100), "")   # +10,000 we give
    assert r["purchase_total"] == 20000 and r["sale_total"] == 30000 and r["offset"] == 20000
    assert r["he_owes_us"] == 10000 and r["we_owe_him"] == 0


def test_swap_refuses_before_writing_anything():
    s1 = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 100, 100)
    pay("purchase", p, CUSTOMER_ID, "out", 10000)            # he was already paid in cash: nothing owed on it
    with get_session() as s:
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        before = (s.query(Purchase).count(), s.query(Sale).count())
    for args in (([p], [], [], _give()),                      # purchase is settled: not an open record
                 ([], [s1], [], []),                          # nothing on the other side
                 ([], [], [], _give()),
                 ([999], [s1], [], [])):                      # not his
        with pytest.raises(ValueError):
            SW.record_from_records(CUSTOMER_ID, *args, "")
    with get_session() as s:
        assert (s.query(Purchase).count(), s.query(Sale).count()) == before


def test_swap_with_goods_out_of_stock_leaves_nothing_behind():
    p = _p(CUSTOMER_ID, 500, 100)
    with get_session() as s:
        from database.models.sale import Sale
        n = s.query(Sale).count()
    with pytest.raises(Exception):
        SW.record_from_records(CUSTOMER_ID, [p], [], [], _give(kg=10_000_000), "")
    with get_session() as s:
        assert s.query(Sale).filter(Sale.status != "voided").count() == n


def test_swap_needs_a_role_that_may_take_goods_in():
    p = _p(CUSTOMER_ID, 500, 100)
    set_session(UserSession(user_id=CASHIER_A, username="x", role="weigher"))
    with pytest.raises(PermissionDenied):
        SW.record_from_records(CUSTOMER_ID, [p], [], [], _give(), "")


def test_products_to_give_carry_a_category():
    assert all("category" in p for p in SW.products_to_give())


# ----------------------------------------------------------------- the screens
def _profile(qapp, mode="admin"):
    from apps.product_management_app.app_state import state
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
    state._load_customers_from_db()
    customer = next(c for c in state.customers if c.id == CUSTOMER_ID)
    v = CustomerProfileView(customer=customer, mode=mode)
    v.resize(1200, 700)
    v.show()
    qapp.processEvents()
    return v


def test_profile_has_the_new_tabs_and_rows_can_be_ticked(qapp, boxes):
    s1 = make_sale()["sale_id"]
    s2 = make_sale()["sale_id"]
    pay("sale", s2, CUSTOMER_ID, "in", 20000)
    p = _p(CUSTOMER_ID, 500, 100)
    v = _profile(qapp)
    names = [v.tabs.tabText(i) for i in range(v.tabs.count())]
    assert names[:5] == ["Activity", "Sales", "Purchases", "Products", "Transaction history"]
    assert v.tab_sales.table.rowCount() == 2 and v.tab_purchases.table.rowCount() == 1
    assert v.tab_products.table.rowCount() >= 1 and v.tab_history.table.rowCount() >= 3
    flags = {v.tab_sales.table.item(i, 0).data(Qt.UserRole): bool(v.tab_sales.table.item(i, 0).flags() & Qt.ItemIsUserCheckable)
             for i in range(2)}
    assert flags == {s1: True, s2: False}                      # the paid sale cannot be ticked
    v.tab_sales.btn_unpaid.click()
    v.tab_purchases.btn_unpaid.click()
    assert v.tab_sales.ticked_ids() == [s1] and v.tab_purchases.ticked_ids() == [p]
    assert "1 ticked" in v.tab_sales.lbl.text()
    v.tab_sales.btn_clear.click()
    assert v.tab_sales.ticked_ids() == []


def test_walkin_profile_has_no_record_tabs(qapp):
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
    make_sale(buyer=0, walkin_name="Musa")
    v = CustomerProfileView(customer=None, mode="admin", walkin_name="Musa")
    assert v.tab_sales is None and "Sales" not in [v.tabs.tabText(i) for i in range(v.tabs.count())]


def _sale_dialog(monkeypatch, **by_product):
    """Drive the checkout's own SaleItemDialog: {product name: (bags, kg, price or None to keep the default)}."""
    from apps.product_management_app.features.cashier.dialogs.sale_item_dialog import SaleItemDialog
    opened = []

    def fake_exec(self):
        bags, kg, price = by_product[self.product_name]
        opened.append((self.product_name, self.price.value()))      # the price it was opened with
        self.qty_bags.setValue(bags)
        if not self.is_fert:
            self.total_weight_kg.setValue(kg)
        if price is not None:
            self.price.setValue(price)
        self._confirm()
        return self.result()
    monkeypatch.setattr(SaleItemDialog, "exec_", fake_exec)
    return opened


def _row(d, name):
    return next(i for i in range(d.tbl_give.rowCount()) if d.tbl_give.item(i, 1).text() == name)


def test_swap_page_picks_products_by_category_and_finalizes(qapp, boxes, monkeypatch):
    p = _p(CUSTOMER_ID, 500, 100)                            # we owe him 50,000
    opened = _sale_dialog(monkeypatch, NPK=(10, 0, 2000), Maize=(1, 100, None))
    d = _dialog(purchase_ids=[p])
    assert d.lbl_owe.text() == "We owe him: 50,000.00" and d.tbl_give.rowCount() == 2
    d.category.setCurrentText("Fertilizer")                  # filter by category
    assert d.tbl_give.rowCount() == 1 and d.tbl_give.item(0, 1).text() == "NPK"
    d.category.setCurrentText("All categories")
    d.tbl_give.item(_row(d, "NPK"), 0).setCheckState(Qt.Checked)        # ticking opens the sale dialog
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Checked)
    assert [o[0] for o in opened] == ["NPK", "Maize"] and opened[1][1] == 100     # Maize opened at its price
    assert d.tbl_give.item(_row(d, "NPK"), 5).text() == "10 bags  =  20,000.00"
    assert d.lbl_recv.text() == "He owes us: 30,000.00"
    assert d.lbl_result.text() == "After the swap we still owe him 20,000.00"
    d.note.setText("goods back")
    d._save()
    assert d.result_data["offset"] == 30000 and boxes[-1][0] == "information"
    assert _bal("purchase", p).balance == 20000
    assert stock(FERT_ID)[0] == 40


def test_cancelling_the_sale_dialog_unticks_and_unticking_removes_the_line(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.dialogs.sale_item_dialog import SaleItemDialog
    p = _p(CUSTOMER_ID, 500, 100)
    d = _dialog(purchase_ids=[p])
    monkeypatch.setattr(SaleItemDialog, "exec_", lambda self: 0)         # cancelled
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Checked)
    assert d.tbl_give.item(_row(d, "Maize"), 0).checkState() == Qt.Unchecked and not d.lines
    _sale_dialog(monkeypatch, Maize=(1, 100, None))
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Checked)
    assert len(d.lines) == 1
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Unchecked)
    assert not d.lines and d.lbl_recv.text() == "He owes us: 0.00"


def test_double_click_changes_a_line_and_the_totals_follow(qapp, boxes, monkeypatch):
    p = _p(CUSTOMER_ID, 500, 100)
    d = _dialog(purchase_ids=[p])
    _sale_dialog(monkeypatch, Maize=(1, 100, None))
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Checked)
    assert d.lbl_recv.text() == "He owes us: 10,000.00"
    _sale_dialog(monkeypatch, Maize=(2, 300, 120))
    d.tbl_give.cellDoubleClicked.emit(_row(d, "Maize"), 1)
    assert d.lbl_recv.text() == "He owes us: 36,000.00" and len(d.lines) == 1


def test_saving_with_nothing_picked_asks_for_the_goods(qapp, boxes):
    p = _p(CUSTOMER_ID, 500, 100)
    d = _dialog(purchase_ids=[p])
    d._save()
    assert boxes[-1][0] == "warning" and _bal("purchase", p).balance == 50000


def test_swap_button_on_the_profile_uses_the_ticked_rows(qapp, boxes, monkeypatch):
    s1 = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 100, 100)
    v = _profile(qapp)
    v.tab_sales.btn_unpaid.click()
    v.tab_purchases.btn_unpaid.click()
    seen = {}
    from apps.product_management_app.features.profiling.dialogs import swap_records_dialog as sd

    class Fake:
        Accepted = 1

        def __init__(self, parent, **kw):
            seen.update(kw)

        def exec_(self):
            return 1
    monkeypatch.setattr(sd, "SwapRecordsDialog", Fake)
    v.tab_purchases.btn_swap.click()
    assert seen["purchase_ids"] == [p] and seen["sale_ids"] == [s1] and seen["party_id"] == CUSTOMER_ID


# ----------------------------------------------------------------- review fixes: routing, prices, outcome
def _dialog(purchase_ids=(), sale_ids=()):
    from apps.product_management_app.features.profiling.dialogs.swap_records_dialog import SwapRecordsDialog
    return SwapRecordsDialog(None, CUSTOMER_ID, "Customer One", purchase_ids=purchase_ids, sale_ids=sale_ids)


def test_ticked_purchases_open_the_sale_workspace_and_ticked_sales_the_purchase_workspace(qapp):
    p = _p(CUSTOMER_ID, 500, 100)
    s1 = make_sale()["sale_id"]
    d = _dialog(purchase_ids=[p])
    assert not d.tbl_give.isHidden() or d.show_give
    assert d.show_give and not d.show_recv and "Sale workspace" in d.windowTitle()
    d = _dialog(sale_ids=[s1])
    assert d.show_recv and not d.show_give and "Purchase workspace" in d.windowTitle()
    d = _dialog(purchase_ids=[p], sale_ids=[s1])
    assert d.show_give and d.show_recv
    d = _dialog()
    assert d.show_give and d.show_recv


def test_selling_price_is_filled_when_the_product_has_one_and_must_be_typed_when_not(qapp, boxes, monkeypatch):
    from database.models.product import Product
    with get_session() as s:
        s.get(Product, FERT_ID).default_sale_rate = None         # NPK has no selling price
    p = _p(CUSTOMER_ID, 500, 100)
    d = _dialog(purchase_ids=[p])
    assert d.tbl_give.item(_row(d, "Maize"), 4).text() == "100.00"
    assert d.tbl_give.item(_row(d, "NPK"), 4).text() == "not set"
    opened = _sale_dialog(monkeypatch, NPK=(2, 0, None), Maize=(1, 100, None))      # NPK: price left at 0
    d.tbl_give.item(_row(d, "NPK"), 0).setCheckState(Qt.Checked)
    assert opened == [("NPK", 0)] and not d.lines and boxes[-1][0] == "warning" and "selling price" in boxes[-1][1]
    assert d.tbl_give.item(_row(d, "NPK"), 0).checkState() == Qt.Unchecked
    _sale_dialog(monkeypatch, NPK=(2, 0, 9000))                                       # the user types it
    d.tbl_give.item(_row(d, "NPK"), 0).setCheckState(Qt.Checked)
    assert d.lbl_recv.text() == "He owes us: 18,000.00"
    d._save()
    assert d.result_data["offset"] == 18000


def test_outcome_is_read_from_the_real_balances(qapp, boxes):
    """Goods bigger than what we owe: the rest stays OPEN on the new sale (to be collected later)."""
    p = _p(CUSTOMER_ID, 100, 100)                                    # we owe him 10,000
    r = SW.record_from_records(CUSTOMER_ID, [p], [], [], _give(kg=300, price=100), "")   # gives 30,000
    assert r["offset"] == 10000 and r["he_owes_us"] == 20000 and r["we_owe_him"] == 0
    assert _bal("sale", r["sale_id"]).balance == 20000 and _bal("purchase", p).status == "paid"


def test_matching_values_settle_completely():
    p = _p(CUSTOMER_ID, 300, 100)                                    # 30,000
    r = SW.record_from_records(CUSTOMER_ID, [p], [], [], _give(kg=300, price=100), "")
    assert r["offset"] == 30000 and r["we_owe_him"] == 0 and r["he_owes_us"] == 0
    assert _bal("sale", r["sale_id"]).status == "paid" and _bal("purchase", p).status == "paid"


def test_crops_are_totalled_by_kg_and_a_missing_kg_or_price_is_refused_before_writing():
    p = _p(CUSTOMER_ID, 500, 100)
    from database.models.sale import Sale
    with get_session() as s:
        n = s.query(Sale).count()
    for line in ({"product_id": CROP_ID, "qty_bags": 5, "total_weight_kg": 0, "unit_price": 100},     # no kg
                 {"product_id": CROP_ID, "qty_bags": 1, "total_weight_kg": 100, "unit_price": 0}):    # no price
        with pytest.raises(ValueError):
            SW.record_from_records(CUSTOMER_ID, [p], [], [], [line], "")
    with get_session() as s:
        assert s.query(Sale).count() == n and _bal("purchase", p).balance == 50000


def test_a_failed_attempt_then_a_good_one_records_exactly_one_swap(qapp, boxes, monkeypatch):
    from database.models.sale import Sale
    p = _p(CUSTOMER_ID, 500, 100)
    d = _dialog(purchase_ids=[p])
    _sale_dialog(monkeypatch, Maize=(1, 10_000_000, None))           # far more than the shelf holds
    d.tbl_give.item(_row(d, "Maize"), 0).setCheckState(Qt.Checked)
    d._save()
    assert boxes[-1][0] == "warning" and "Nothing was saved" in boxes[-1][1] and d.btn_save.isEnabled()
    _sale_dialog(monkeypatch, Maize=(1, 100, None))
    d.tbl_give.cellDoubleClicked.emit(_row(d, "Maize"), 1)           # correct the same line
    d._save()
    assert d.result_data["offset"] == 10000
    with get_session() as s:
        assert s.query(Sale).filter(Sale.status != "voided").count() == 1
    assert _bal("purchase", p).balance == 40000


def test_voided_documents_are_hidden_on_the_tabs_unless_asked_for(qapp):
    from database.services.sales_service import SaleService
    from shared.auth.permissions import system_context
    s1 = make_sale()["sale_id"]
    s2 = make_sale()["sale_id"]
    with system_context():
        SaleService.void_sale(s2, CASHIER_A, "typed twice")
    v = _profile(qapp)
    assert v.tab_sales.table.rowCount() == 1
    v.tab_sales.chk_voided.setChecked(True)
    assert v.tab_sales.table.rowCount() == 2 and s1
