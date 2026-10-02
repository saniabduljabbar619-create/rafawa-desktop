"""Unified Orders: shared partner pool, tracking board, overview, timeline."""
from datetime import datetime, timedelta

from database.models.company import Company
from database.models.purchase_order import PurchaseOrder
from database.models.purchase_order_supplier import PurchaseOrderSupplier
from database.models.sales_order import SalesOrder
from database.services.company_service import CompanyService
from database.services.order_hub_service import OrderHubService as H
from database.services.purchase_order_service import PurchaseOrderService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.services.sales_order_service import SalesOrderService
from database.session import get_session
from tests.conftest import ADMIN_ID, CROP_ID, FERT_ID


def _names(model):
    with get_session() as s:
        return sorted(r[0] for r in s.query(model.name).all())


def test_new_company_also_becomes_a_po_supplier_and_vice_versa():
    CompanyService.create("Acme Foods", ADMIN_ID, phone="0801", contact_person="Ada")
    PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID, address="Kano")
    assert _names(Company) == ["Acme Foods", "Farm Co"]
    assert _names(PurchaseOrderSupplier) == ["Acme Foods", "Farm Co"]
    with get_session() as s:
        mirrored = s.query(PurchaseOrderSupplier).filter_by(name="Acme Foods").one()
        assert mirrored.phone == "0801" and mirrored.contact_person == "Ada"
        assert s.query(Company).filter_by(name="Farm Co").one().address == "Kano"
    # the pickers (search) see both on each side
    assert {c["name"] for c in CompanyService.search("")} == {"Acme Foods", "Farm Co"}
    assert {c["name"] for c in PurchaseOrderSupplierService.search("")} == {"Acme Foods", "Farm Co"}


def test_sync_brings_existing_one_sided_partners_across_and_is_idempotent():
    with get_session() as s:
        s.add(Company(name="Old Buyer", created_by_user_id=ADMIN_ID))
        s.add(PurchaseOrderSupplier(name="old buyer", created_by_user_id=ADMIN_ID))   # same name, other case
        s.add(PurchaseOrderSupplier(name="Old Seller", created_by_user_id=ADMIN_ID))
    assert H.sync_partners() == 1                          # only "Old Seller" was missing
    assert H.sync_partners() == 0
    assert "Old Seller" in _names(Company) and len(_names(PurchaseOrderSupplier)) == 2


def test_duplicate_names_are_still_refused_on_each_side():
    import pytest
    CompanyService.create("Acme", ADMIN_ID)
    with pytest.raises(ValueError):
        CompanyService.create("Acme", ADMIN_ID)
    with pytest.raises(ValueError):
        PurchaseOrderSupplierService.create("Acme", ADMIN_ID)      # already mirrored


def _so(company, price=110):
    return SalesOrderService.create_draft(company, [{
        "product_id": CROP_ID, "dispatched_weight_kg": 500, "dispatched_qty_bags": 5, "my_price": price}], ADMIN_ID)


def _po(supplier):
    return PurchaseOrderService.create_draft(supplier, [{
        "product_id": FERT_ID, "ordered_qty_bags": 10, "expected_price": 9000}], ADMIN_ID)


def test_board_lists_both_kinds_with_values_and_filters():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    so, po = _so(c), _po(p)
    SalesOrderService.dispatch(so, ADMIN_ID)
    rows = H.board()
    assert sorted((r["kind"], r["status"]) for r in rows) == [("purchase", "draft"), ("sales", "in_transit")]
    assert next(r for r in rows if r["kind"] == "sales")["order_no"] == f"SO-{so}"
    sales = next(r for r in rows if r["kind"] == "sales")
    purch = next(r for r in rows if r["kind"] == "purchase")
    assert sales["value"] == 55000 and sales["estimated"] and sales["partner"] == "Acme Foods"
    assert purch["value"] == 90000 and purch["partner"] == "Farm Co"
    assert [r["kind"] for r in H.board(kind="sales")] == ["sales"]
    assert [r["status"] for r in H.board(status="in_transit")] == ["in_transit"]
    assert len(H.board(status="active")) == 2
    assert [r["partner"] for r in H.board(search="farm")] == ["Farm Co"]
    assert H.board(status="cancelled") == []


def test_overdue_in_transit_orders_are_flagged_and_counted():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    so = _so(c)
    SalesOrderService.dispatch(so, ADMIN_ID)
    with get_session() as s:
        o = s.get(SalesOrder, so)
        o.expected_delivery_date = datetime.now() - timedelta(days=2)
        o.dispatched_at = datetime.now() - timedelta(days=5)
    r = H.board()[0]
    assert r["overdue"] and r["days_out"] == 5
    ov = H.overview()
    assert ov["sales"]["in_transit"] == 1 and ov["sales"]["overdue"] == 1 and ov["purchase"]["in_transit"] == 0
    assert ov["sales"]["in_transit_value"] == 55000


def test_confirmed_orders_show_final_value_and_outstanding_balance():
    from database.services.order_payment_service import OrderPaymentService
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    so = _so(c)
    SalesOrderService.dispatch(so, ADMIN_ID)
    item = SalesOrderService.get_order_detail(so)["items"][0]["item_id"]
    SalesOrderService.confirm(so, [{"item_id": item, "company_weight_kg": 500, "company_price": 100,
                                    "accepted_weight_kg": 500, "rejected_weight_kg": 0}], ADMIN_ID)
    r = H.board()[0]
    assert r["status"] == "confirmed" and not r["estimated"] and r["value"] == 50000
    assert r["balance"] == 50000 and r["payment_status"] == "unpaid"
    OrderPaymentService.record_payment("sales_order", so, 20000, ADMIN_ID, method="cash")
    assert H.board()[0]["balance"] == 30000 and H.overview()["sales"]["outstanding"] == 30000


def test_timeline_lists_what_happened_in_order():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    so = _so(c)
    SalesOrderService.dispatch(so, ADMIN_ID)
    SalesOrderService.revert_to_draft(so, ADMIN_ID)
    SalesOrderService.cancel(so, ADMIN_ID, "changed mind")
    events = [e["event"] for e in H.timeline("sales", so)]
    assert events[0] == "Created (draft)" and events[-1] == "Cancelled"
    assert H.timeline("sales", so)[0]["by"] == "admin"
    import pytest
    with pytest.raises(ValueError):
        H.timeline("purchase", 9999)
