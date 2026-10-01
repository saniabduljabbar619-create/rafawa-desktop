import pytest

from database.models.sale import Sale
from database.session import get_session
from database.services.payment_service import PaymentService
from database.services.sales_service import SaleService
from shared.config.constants import WALKIN_PARTY_ID
from tests.conftest import (ADMIN_ID, CASHIER_A, CROP_ID, FERT_ID, make_sale, pay, stock)


def test_sale_deducts_bags_and_kg():
    res = make_sale()
    assert res["total_amount"] == 20000
    assert stock(CROP_ID) == (18, 1800)


def test_fertilizer_sale_is_bags_only():
    make_sale([{"product_id": FERT_ID, "qty_bags": 3, "total_weight_kg": 0, "unit_price": 10000}])
    assert stock(FERT_ID) == (47, 0)


def test_sale_without_buyer_goes_to_walkin_bucket():
    res = make_sale(buyer=0, walkin_name="Musa")
    with get_session() as s:
        sale = s.get(Sale, res["sale_id"])
        assert sale.buyer_party_id == WALKIN_PARTY_ID
        assert sale.walkin_name == "Musa"


def test_insufficient_bags_rejected_with_product_name():
    with pytest.raises(ValueError, match="Maize.*bag"):
        make_sale([{"product_id": CROP_ID, "qty_bags": 21, "total_weight_kg": 100, "unit_price": 100}])
    assert stock(CROP_ID) == (20, 2000)


def test_insufficient_kg_rejected_even_when_bags_suffice():
    # 5 bags is fine (20 on hand) but 2500 kg is more than the 2000 kg held
    with pytest.raises(ValueError, match="Maize.*kg"):
        make_sale([{"product_id": CROP_ID, "qty_bags": 5, "total_weight_kg": 2500, "unit_price": 100}])
    assert stock(CROP_ID) == (20, 2000)


def test_void_sale_restores_stock_and_keeps_record():
    res = make_sale()
    SaleService.void_sale(res["sale_id"], ADMIN_ID, "typo")
    assert stock(CROP_ID) == (20, 2000)
    with get_session() as s:
        assert s.get(Sale, res["sale_id"]).status == "voided"


def test_void_sale_is_idempotent():
    res = make_sale()
    SaleService.void_sale(res["sale_id"], ADMIN_ID)
    again = SaleService.void_sale(res["sale_id"], ADMIN_ID)
    assert again["already_voided"] is True
    assert stock(CROP_ID) == (20, 2000)


def test_void_sale_refused_once_paid():
    res = make_sale()
    pay("sale", res["sale_id"], 1, "in", 5000)
    with pytest.raises(ValueError, match="payment already exists"):
        SaleService.void_sale(res["sale_id"], ADMIN_ID)


def test_void_sale_refused_when_returns_exist():
    from database.services.return_service import ReturnService
    res = make_sale()
    item_id = res["sale_item_ids"][0]
    ReturnService.create_return(document_type="sale", document_id=res["sale_id"], item_id=item_id,
                                qty_kg=50, qty_bags=1, reason="bad", created_by_user_id=CASHIER_A)
    with pytest.raises(ValueError, match="returned"):
        SaleService.void_sale(res["sale_id"], ADMIN_ID)


def test_product_search_finds_products_beyond_the_limit():
    from database.models.product import Product
    with get_session() as s:
        for i in range(10, 40):
            s.add(Product(id=i, name=f"Filler{i}", product_type="crops", sku=f"F{i}"))
    # Maize is the OLDEST product; with limit=5 the old code never found it
    found = SaleService.list_products_for_sale(q="maize", limit=5, include_zero_stock=False)
    assert [p["name"] for p in found] == ["Maize"]


def test_payment_is_positive_and_voidable():
    res = make_sale()
    pid = pay("sale", res["sale_id"], 1, "in", 1000)
    with pytest.raises(ValueError):
        pay("sale", res["sale_id"], 1, "in", 0)
    PaymentService.void_payment(pid, ADMIN_ID, "oops")
    PaymentService.void_payment(pid, ADMIN_ID, "oops")  # idempotent
