import pytest

from database.models.product import Product
from database.services.purchase_service import PurchaseService
from database.services.return_service import ReturnService
from database.session import get_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, SUPPLIER_ID, pay, stock


def _buy(sku="CROP-MAIZE", name="Maize", bags=4, kg=400, cost=80, supplier=SUPPLIER_ID):
    return PurchaseService.record({
        "supplier_party_id": supplier, "created_by_user_id": CASHIER_A,
        "items": [{"sku": sku, "product_name": name, "product_type": "crops",
                   "qty_bags": bags, "total_weight_kg": kg, "unit_cost": cost}]})


def test_purchase_adds_stock_and_totals():
    res = _buy()
    assert res["total_amount"] == 32000
    assert stock(CROP_ID) == (24, 2400)


@pytest.mark.parametrize("bad", [0, None, "", "abc"])
def test_supplier_id_is_validated(bad):
    with pytest.raises(ValueError, match="supplier_party_id"):
        PurchaseService.record({"supplier_party_id": bad, "created_by_user_id": 1,
                                "items": [{"product_name": "x", "sku": "x", "product_type": "crops",
                                           "qty_bags": 1, "total_weight_kg": 1, "unit_cost": 1}]})


def test_blank_sku_does_not_overwrite_an_unrelated_product():
    with get_session() as s:
        s.add(Product(id=50, name="Rice", product_type="crops", sku=None, default_cost_rate=5))
    _buy(sku="", name="Beans", bags=1, kg=50, cost=70)
    with get_session() as s:
        rice = s.get(Product, 50)
        assert rice.name == "Rice" and float(rice.default_cost_rate) == 5.0
        assert s.query(Product).filter(Product.name == "Beans").count() == 1


def test_void_purchase_removes_its_stock():
    res = _buy()
    PurchaseService.void_purchase(res["purchase_id"], ADMIN_ID, "wrong")
    assert stock(CROP_ID) == (20, 2000)


def test_void_purchase_refused_after_payment_or_return():
    res = _buy()
    pay("purchase", res["purchase_id"], SUPPLIER_ID, "out", 1000)
    with pytest.raises(ValueError, match="payment"):
        PurchaseService.void_purchase(res["purchase_id"], ADMIN_ID)

    res2 = _buy()
    ReturnService.create_return(document_type="purchase", document_id=res2["purchase_id"],
                                item_id=res2["purchase_item_ids"][0], qty_kg=50, qty_bags=1,
                                reason="bad", created_by_user_id=CASHIER_A)
    with pytest.raises(ValueError, match="returned"):
        PurchaseService.void_purchase(res2["purchase_id"], ADMIN_ID)
