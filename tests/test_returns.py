import pytest

from database.models.payment import Payment
from database.models.sale_return import SaleReturn
from database.services.return_service import ReturnService
from database.session import get_session
from tests.conftest import CASHIER_A, CROP_ID, FERT_ID, make_sale, pay, stock


def _return(res, kg=0, bags=0, item=0):
    return ReturnService.create_return(
        document_type="sale", document_id=res["sale_id"], item_id=res["sale_item_ids"][item],
        qty_kg=kg, qty_bags=bags, reason="t", created_by_user_id=CASHIER_A)


def _amount(rid):
    with get_session() as s:
        r = s.get(SaleReturn, rid)
        return abs(float(r.amount)), float(r.unit_price_snapshot)


def test_return_restores_stock_at_list_price_without_discount():
    res = make_sale()                                   # 200 kg @ 100
    rid = _return(res, kg=100, bags=1)
    assert _amount(rid) == (10000.0, 100.0)
    assert stock(CROP_ID) == (19, 1900)                 # 18/1800 after sale, +1/+100


def test_return_refunds_at_the_discounted_price_actually_paid():
    # 200 kg @ 100 = 20000, with a 2000 line discount -> customer paid 18000 (90/kg)
    res = make_sale([{"product_id": CROP_ID, "qty_bags": 2, "total_weight_kg": 200,
                      "unit_price": 100, "discount": 2000}], discount=2000, total=18000)
    rid = _return(res, kg=100, bags=1)
    amount, rate = _amount(rid)
    assert rate == 90.0
    assert amount == 9000.0


def test_header_only_discount_is_spread_over_lines():
    res = make_sale([
        {"product_id": CROP_ID, "qty_bags": 2, "total_weight_kg": 100, "unit_price": 100},   # 10000
        {"product_id": FERT_ID, "qty_bags": 1, "total_weight_kg": 0, "unit_price": 10000},   # 10000
    ], discount=2000, total=18000)
    rid = _return(res, bags=1, item=1)                  # whole fertilizer line
    assert _amount(rid)[0] == 9000.0                    # 10000 less its half of the 2000


def test_cannot_return_more_than_sold_and_float_noise_is_tolerated():
    res = make_sale([{"product_id": CROP_ID, "qty_bags": 3, "total_weight_kg": 100.1, "unit_price": 100}])
    _return(res, kg=33.7, bags=1)
    _return(res, kg=66.4, bags=2)                       # 100.1 - 33.7 = 66.39999999999999
    with pytest.raises(ValueError, match="exceed"):
        _return(res, kg=0.5, bags=0)


def test_negative_quantities_rejected():
    res = make_sale()
    with pytest.raises(ValueError, match="negative"):
        _return(res, kg=10, bags=-1)


def test_refund_is_capped_at_cash_actually_paid():
    res = make_sale()                                   # total 20000
    pay("sale", res["sale_id"], 1, "in", 3000)          # customer paid only 3000
    rid = _return(res, kg=100, bags=1)                  # return worth 10000
    ReturnService.approve_sale_return(rid, CASHIER_A)
    with get_session() as s:
        refunds = s.query(Payment).filter(Payment.method == "refund").all()
        assert [float(r.amount) for r in refunds] == [3000.0]
        assert refunds[0].direction == "out"


def test_nothing_paid_means_no_refund():
    res = make_sale()
    rid = _return(res, kg=100, bags=1)
    ReturnService.approve_sale_return(rid, CASHIER_A)
    with get_session() as s:
        assert s.query(Payment).count() == 0
