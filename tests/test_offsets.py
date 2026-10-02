"""Settle unpaid sales with a purchase, goods-for-goods swaps, net-off and reversal."""
import pytest

from database.models.payment import Payment
from database.services.document_balance import document_balances
from database.services.offset_service import OffsetService as O, SwapService as SW
from database.services.party_balance_service import PartyBalanceService
from database.services.purchase_service import PurchaseService
from database.services.sales_service import SaleService
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import (ADMIN_ID, CASHIER_A, CROP_ID, CUSTOMER_ID, FERT_ID, SUPPLIER_ID, make_sale, pay,
                            stock)


def _purchase(party=SUPPLIER_ID, kg=1000, cost=50):                      # 50,000
    return PurchaseService.record({"supplier_party_id": party, "created_by_user_id": CASHIER_A,
        "items": [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
                   "qty_bags": 10, "total_weight_kg": kg, "unit_cost": cost}]})["purchase_id"]


def _b(kind, doc):
    with get_session() as s:
        return document_balances(s, kind, [doc])[doc]


def _p(party, kg, cost):
    return PurchaseService.record({"supplier_party_id": party, "created_by_user_id": CASHIER_A,
        "items": [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
                   "qty_bags": 5, "total_weight_kg": kg, "unit_cost": cost}]})["purchase_id"]


def test_purchase_settles_unpaid_sales_oldest_first_and_leaves_change():
    s1 = make_sale()["sale_id"]                          # 20,000
    s2 = make_sale()["sale_id"]                          # 20,000   -> customer owes 40,000
    p = _p(CUSTOMER_ID, 500, 100)                        # he brings goods worth 50,000
    r = O.apply(CUSTOMER_ID, purchase_ids=[p], kind="settlement")
    assert r["amount"] == 40000 and r["they_owe_left"] == 0 and r["we_owe_left"] == 10000
    assert _b("sale", s1).balance == 0 and _b("sale", s2).balance == 0
    pb = _b("purchase", p)
    assert pb.offset_applied == 40000 and pb.balance == 10000 and pb.status == "partial"
    assert pb.cash_paid == 0
    with get_session() as s:
        assert s.query(Payment).count() == 0               # no cash moved


def test_purchase_smaller_than_debt_leaves_him_owing_the_rest():
    s1 = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 100, 100)                        # 10,000 of goods vs 20,000 owed
    O.apply(CUSTOMER_ID, purchase_ids=[p])
    assert _b("sale", s1).balance == 10000 and _b("purchase", p).status == "paid"


def test_party_profile_balance_reflects_offsets():
    make_sale()
    p = _p(CUSTOMER_ID, 500, 100)
    before = PartyBalanceService.compute(CUSTOMER_ID, ADMIN_ID, "")["overall"]
    assert before["receivable"] == 20000 and before["payable"] == 50000
    O.apply(CUSTOMER_ID, purchase_ids=[p])
    after = PartyBalanceService.compute(CUSTOMER_ID, ADMIN_ID, "")["overall"]
    assert after["receivable"] == 0 and after["payable"] == 30000


def test_partial_amount_and_validation():
    make_sale()
    p = _p(CUSTOMER_ID, 500, 100)
    for bad in (0, -1, 20000.01):
        with pytest.raises(ValueError):
            O.apply(CUSTOMER_ID, purchase_ids=[p], amount=bad)
    O.apply(CUSTOMER_ID, purchase_ids=[p], amount=5000)
    assert _b("purchase", p).offset_applied == 5000
    with pytest.raises(ValueError):
        O.apply(SUPPLIER_ID)                              # owes nothing both ways
    assert O.preview(CUSTOMER_ID)["offsettable"] == 15000


def test_reverse_restores_both_sides_and_is_admin_only():
    s1 = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 500, 100)
    O.apply(CUSTOMER_ID, purchase_ids=[p])
    oid = O.list_for_party(CUSTOMER_ID)[0]["id"]
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    with pytest.raises(PermissionDenied):
        O.reverse(oid, "x")
    set_session(UserSession(user_id=ADMIN_ID, username="admin", role="admin"))
    with pytest.raises(ValueError):
        O.reverse(oid, "")
    O.reverse(oid, "keyed wrongly")
    assert _b("sale", s1).balance == 20000 and _b("purchase", p).balance == 50000
    assert O.list_for_party(CUSTOMER_ID)[0]["status"] == "reversed"


def test_voiding_the_purchase_releases_the_sale():
    s1 = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 500, 100)
    O.apply(CUSTOMER_ID, purchase_ids=[p])
    assert _b("sale", s1).balance == 0
    PurchaseService.void_purchase(p, ADMIN_ID, "wrong")
    assert _b("sale", s1).balance == 20000


def test_walkin_cannot_be_offset():
    from shared.config.constants import WALKIN_PARTY_ID
    with pytest.raises(ValueError):
        O.apply(WALKIN_PARTY_ID)


# ------------------------------------------------------------------------- swap
def _recv(kg=100, cost=100):                                   # 10,000
    return [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
             "qty_bags": 2, "total_weight_kg": kg, "unit_cost": cost}]


def _give(bags=1, price=10000):
    return [{"product_id": FERT_ID, "qty_bags": bags, "total_weight_kg": 0, "unit_price": price}]


def test_swap_he_gets_more_than_he_brings_so_he_owes_us_the_difference():
    maize0, npk0 = stock(CROP_ID)[1], stock(FERT_ID)[0]
    r = SW.record(CUSTOMER_ID, _recv(100, 100), _give(2, 10000), note="maize for NPK")   # 10,000 vs 20,000
    assert (r["purchase_total"], r["sale_total"], r["offset"]) == (10000, 20000, 10000)
    assert r["he_owes_us"] == 10000 and r["we_owe_him"] == 0
    assert _b("purchase", r["purchase_id"]).status == "paid"
    assert _b("sale", r["sale_id"]).balance == 10000
    assert stock(CROP_ID)[1] == maize0 + 100 and stock(FERT_ID)[0] == npk0 - 2


def test_swap_he_brings_more_so_we_owe_him_change():
    r = SW.record(CUSTOMER_ID, _recv(300, 100), _give(1, 10000))                          # 30,000 vs 10,000
    assert r["we_owe_him"] == 20000 and r["he_owes_us"] == 0
    assert _b("sale", r["sale_id"]).status == "paid"
    assert _b("purchase", r["purchase_id"]).balance == 20000
    # the change is then paid out like any other payment
    pay("purchase", r["purchase_id"], CUSTOMER_ID, "out", 20000)
    assert _b("purchase", r["purchase_id"]).status == "paid"


def test_swap_with_no_stock_records_nothing():
    from database.models.purchase import Purchase
    from database.models.sale import Sale
    with pytest.raises(Exception):
        SW.record(CUSTOMER_ID, _recv(), _give(bags=500))
    with get_session() as s:
        assert s.query(Purchase).count() == 0 and s.query(Sale).count() == 0


def test_swap_needs_both_sides_and_a_real_party():
    from shared.config.constants import WALKIN_PARTY_ID
    for args in ((CUSTOMER_ID, [], _give()), (CUSTOMER_ID, _recv(), []), (WALKIN_PARTY_ID, _recv(), _give())):
        with pytest.raises(ValueError):
            SW.record(*args)


def test_swap_flags_the_party_both_ways_and_lists_stock_to_give():
    SW.record(CUSTOMER_ID, _recv(), _give())
    from database.models.party import Party
    with get_session() as s:
        p = s.get(Party, CUSTOMER_ID)
        assert p.is_customer and p.is_supplier
    row = {r["id"]: r for r in SW.products_to_give()}[FERT_ID]
    assert row["bags"] == 49 and row["price"] == 10000


def test_cashier_may_swap_but_a_weigher_may_not():
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    SW.record(CUSTOMER_ID, _recv(), _give())
    set_session(UserSession(user_id=CASHIER_A, username="w", role="weigher"))
    with pytest.raises(PermissionDenied):
        SW.record(CUSTOMER_ID, _recv(), _give())


def test_net_off_between_a_two_way_party():
    p = _purchase()                                        # we owe supplier 50,000
    s = make_sale(buyer=SUPPLIER_ID)["sale_id"]            # supplier owes us 20,000
    O.apply(SUPPLIER_ID, kind="net_off")
    assert _b("purchase", p).balance == 30000 and _b("sale", s).balance == 0
