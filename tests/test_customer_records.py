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


def test_swap_page_picks_products_by_category_and_finalizes(qapp, boxes):
    from apps.product_management_app.features.profiling.dialogs.swap_records_dialog import SwapRecordsDialog
    p = _p(CUSTOMER_ID, 500, 100)                            # we owe him 50,000
    d = SwapRecordsDialog(None, CUSTOMER_ID, "Customer One", purchase_ids=[p], sale_ids=[])
    assert d.lbl_owe.text() == "We owe him: 50,000.00" and d.tbl_give.rowCount() == 2
    d.category.setCurrentText("Fertilizer")                  # filter by category
    assert d.tbl_give.rowCount() == 1 and d.tbl_give.item(0, 1).text() == "NPK"
    d.category.setCurrentText("All categories")
    rows = {d.tbl_give.item(i, 1).text(): i for i in range(d.tbl_give.rowCount())}
    # tick NPK (10 bags @ its price) and Maize (100 kg), by typing quantities
    npk, maize = rows["NPK"], rows["Maize"]
    d.tbl_give.item(npk, 4).setText("10")
    d.tbl_give.item(npk, 6).setText("2000")
    d.tbl_give.item(maize, 4).setText("1")
    d.tbl_give.item(maize, 5).setText("100")
    d.tbl_give.item(maize, 6).setText("100")
    assert d.tbl_give.item(npk, 0).checkState() == Qt.Checked          # typing a quantity ticks the row
    assert d.lbl_recv.text() == "He owes us: 30,000.00"
    assert d.lbl_result.text() == "After the swap we still owe him 20,000.00"
    d.note.setText("goods back")
    d._save()
    assert d.result_data["offset"] == 30000 and boxes[-1][0] == "information"
    assert _bal("purchase", p).balance == 20000
    assert stock(FERT_ID)[0] == 40


def test_swap_page_warns_when_a_ticked_product_has_no_quantity(qapp, boxes):
    from apps.product_management_app.features.profiling.dialogs.swap_records_dialog import SwapRecordsDialog
    p = _p(CUSTOMER_ID, 500, 100)
    d = SwapRecordsDialog(None, CUSTOMER_ID, "Customer One", purchase_ids=[p], sale_ids=[])
    d.tbl_give.item(0, 0).setCheckState(Qt.Checked)
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
