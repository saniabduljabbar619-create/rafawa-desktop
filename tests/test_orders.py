import pytest

from database.models.company import Company
from database.models.purchase_order_supplier import PurchaseOrderSupplier
from database.services.purchase_order_service import PurchaseOrderService
from database.services.sales_order_service import SalesOrderService
from database.session import get_session
from tests.conftest import ADMIN_ID, CROP_ID, FERT_ID, stock


@pytest.fixture
def company():
    with get_session() as s:
        c = Company(name="Acme Foods", created_by_user_id=ADMIN_ID)
        s.add(c); s.flush()
        return c.id


@pytest.fixture
def po_supplier():
    with get_session() as s:
        c = PurchaseOrderSupplier(name="Farm Co", created_by_user_id=ADMIN_ID)
        s.add(c); s.flush()
        return c.id


def _crop_order(company, kg=500, bags=5):
    oid = SalesOrderService.create_draft(company, [{
        "product_id": CROP_ID, "dispatched_weight_kg": kg, "dispatched_qty_bags": bags,
        "my_price": 110}], ADMIN_ID)
    SalesOrderService.dispatch(oid, ADMIN_ID)
    return oid


def _item_id(oid):
    return SalesOrderService.get_order_detail(oid)["items"][0]["item_id"]


def test_crop_dispatch_deducts_kg_and_bags(company):
    _crop_order(company)
    assert stock(CROP_ID) == (15, 1500)


def test_dispatch_checks_bag_stock_for_crops(company):
    oid = SalesOrderService.create_draft(company, [{
        "product_id": CROP_ID, "dispatched_weight_kg": 100, "dispatched_qty_bags": 99,
        "my_price": 1}], ADMIN_ID)
    with pytest.raises(ValueError, match="Insufficient stock"):
        SalesOrderService.dispatch(oid, ADMIN_ID)


def test_dispatch_requires_a_user(company):
    oid = SalesOrderService.create_draft(company, [{
        "product_id": FERT_ID, "dispatched_qty_bags": 1, "my_price": 1}], ADMIN_ID)
    with pytest.raises(ValueError, match="dispatched_by_user_id"):
        SalesOrderService.dispatch(oid, 0)


def test_rejected_crops_return_proportional_bags(company):
    oid = _crop_order(company, kg=500, bags=5)
    item = _item_id(oid)
    SalesOrderService.confirm(oid, [{
        "item_id": item, "company_weight_kg": 500, "company_price": 100,
        "accepted_weight_kg": 300, "rejected_weight_kg": 200}], ADMIN_ID)
    # 200 of 500 kg came back -> 2 of 5 bags
    assert stock(CROP_ID) == (17, 1700)
    assert SalesOrderService.get_order_detail(oid)["final_total_amount"] == 30000


def test_confirm_rejects_negative_numbers(company):
    oid = _crop_order(company)
    item = _item_id(oid)
    with pytest.raises(ValueError, match="negative"):
        SalesOrderService.confirm(oid, [{
            "item_id": item, "company_weight_kg": 100, "company_price": 100,
            "accepted_weight_kg": 150, "rejected_weight_kg": -50}], ADMIN_ID)


def test_purchase_order_confirm_brings_in_proportional_bags(po_supplier):
    oid = PurchaseOrderService.create_draft(po_supplier, [{
        "product_id": CROP_ID, "ordered_weight_kg": 1000, "ordered_qty_bags": 10,
        "expected_price": 70}], ADMIN_ID)
    PurchaseOrderService.mark_in_transit(oid, ADMIN_ID)
    detail = PurchaseOrderService.get_order_detail(oid)
    item = detail["items"][0]["item_id"]
    PurchaseOrderService.confirm(oid, [{
        "item_id": item, "received_weight_kg": 1000, "received_price": 70,
        "accepted_weight_kg": 800, "rejected_weight_kg": 200}], ADMIN_ID)
    assert stock(CROP_ID) == (28, 2800)             # +8 of 10 bags, +800 kg
