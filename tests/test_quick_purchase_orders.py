"""Quick Purchase orders: draft -> pending -> completed, routing to another supplier, cancel / sell available / delete."""
import pytest

from database.models.party import Party
from database.models.purchase import Purchase
from database.services.quick_purchase_order_service import QuickPurchaseOrderService as Q
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import CROP_ID, FERT_ID, SUPPLIER_ID, stock

SUPPLIER_2 = 40


@pytest.fixture(autouse=True)
def second_supplier():
    with get_session() as s:
        s.add(Party(id=SUPPLIER_2, party_type="supplier", display_name="Supplier Two", is_supplier=True))


def _lines(supplier=SUPPLIER_ID):
    return [{"product_id": FERT_ID, "qty_bags": 10, "unit_cost": 9500, "supplier_id": supplier},
            {"product_id": CROP_ID, "total_weight_kg": 300, "qty_bags": 6, "unit_cost": 85, "supplier_id": supplier}]


def test_a_new_quick_purchase_is_a_draft_that_touches_nothing():
    before = stock(FERT_ID)
    oid = Q.create(_lines(), customer_name="Musa", notes="urgent")
    o = Q.get(oid)
    assert o["status"] == "draft" and o["order_no"] == f"QP-{oid:05d}" and o["customer_name"] == "Musa"
    assert o["total"] == 10 * 9500 + 300 * 85
    assert stock(FERT_ID) == before
    Q.update_draft(oid, _lines()[:1])
    assert len(Q.get(oid)["lines"]) == 1


def test_sending_needs_a_supplier_and_a_cost_then_goes_pending_without_stock_effect():
    oid = Q.create([{"product_id": FERT_ID, "qty_bags": 5, "unit_cost": 9000}])
    with pytest.raises(ValueError, match="supplier"):
        Q.send(oid)
    oid2 = Q.create([{"product_id": FERT_ID, "qty_bags": 5, "supplier_id": SUPPLIER_ID}])
    with pytest.raises(ValueError, match="cost"):
        Q.send(oid2)
    before = stock(FERT_ID)
    oid3 = Q.create(_lines(), send=True)
    assert Q.get(oid3)["status"] == "pending" and stock(FERT_ID) == before
    with pytest.raises(ValueError, match="only possible when it is draft"):
        Q.update_draft(oid3, _lines())


def test_receiving_buys_from_the_supplier_and_completes_the_order():
    oid = Q.create(_lines(), send=True)
    res = Q.receive(oid)
    assert res["status"] == "completed" and len(res["purchases"]) == 1 and not res["errors"]
    assert stock(FERT_ID)[0] == 60 and stock(CROP_ID)[1] == 2300
    with get_session() as s:
        p = s.get(Purchase, res["purchases"][0])
        assert p.is_quick and p.supplier_party_id == SUPPLIER_ID and float(p.total_amount) == 10 * 9500 + 300 * 85
    assert all(ln["state"] == "received" and ln["routes"][0]["outcome"] == "received"
               for ln in Q.get(oid)["lines"])
    with pytest.raises(ValueError):
        Q.receive(oid)                                     # nothing left to receive


def test_routing_the_unavailable_line_to_another_supplier_completes_it_in_two_purchases():
    oid = Q.create(_lines(), send=True)
    fert, crop = Q.get(oid)["lines"]
    Q.receive(oid, [fert["id"]])                           # supplier one only has the fertilizer
    o = Q.get(oid)
    assert o["status"] == "pending" and stock(FERT_ID)[0] == 60 and stock(CROP_ID)[1] == 2000
    with pytest.raises(ValueError, match="already asked"):
        Q.route(oid, [crop["id"]], SUPPLIER_ID)
    with pytest.raises(ValueError, match="still waiting"):
        Q.route(oid, [fert["id"]], SUPPLIER_2)             # already received
    Q.route(oid, [crop["id"]], SUPPLIER_2, note="no maize", unit_cost=90)
    line = Q.get(oid)["lines"][1]
    assert line["supplier_id"] == SUPPLIER_2 and line["unit_cost"] == 90
    assert [(r["supplier"], r["outcome"]) for r in line["routes"]] == [
        ("Supplier One", "unavailable"), ("Supplier Two", "asked")]
    assert line["routes"][0]["note"] == "no maize"
    res = Q.receive(oid)
    assert res["status"] == "completed" and stock(CROP_ID)[1] == 2300
    with get_session() as s:
        assert s.query(Purchase).filter(Purchase.is_quick.is_(True)).count() == 2


def test_cancel_and_delete_never_touch_stock():
    before = stock(FERT_ID)
    oid = Q.create(_lines(), send=True)
    Q.cancel(oid, "customer changed his mind")
    o = Q.get(oid)
    assert o["status"] == "cancelled" and o["close_reason"] == "customer changed his mind"
    assert stock(FERT_ID) == before
    with pytest.raises(ValueError):
        Q.send(oid)
    Q.delete(oid)
    assert Q.list_orders() == []
    d = Q.create(_lines())
    Q.delete(d)                                            # drafts and pending ones can go too
    p = Q.create(_lines(), send=True)
    Q.delete(p)
    assert Q.list_orders() == []


def test_sell_available_gives_up_the_unsourced_lines():
    oid = Q.create(_lines(), send=True)
    res = Q.sell_available(oid)
    assert Q.get(oid)["status"] == "cancelled" and "NPK" in res["in_stock"]
    assert all(ln["state"] == "dropped" for ln in Q.get(oid)["lines"])
    # part already received: the order closes as completed with what arrived
    oid = Q.create(_lines(), send=True)
    Q.receive(oid, [Q.get(oid)["lines"][0]["id"]])
    with pytest.raises(ValueError, match="already received"):
        Q.cancel(oid)
    Q.sell_available(oid)
    o = Q.get(oid)
    assert o["status"] == "completed" and [x["state"] for x in o["lines"]] == ["received", "dropped"]
    assert o["total"] == 10 * 9500                         # dropped lines are not owed
    with pytest.raises(ValueError, match="cannot be deleted"):
        Q.delete(oid)


def test_list_filters_by_status_and_staff_only():
    a = Q.create(_lines())
    Q.create(_lines(), send=True)
    assert [o["id"] for o in Q.list_orders("draft")] == [a] and len(Q.list_orders("pending")) == 1
    set_session(UserSession(user_id=2, username="w", role="weigher"))
    with pytest.raises(PermissionDenied):
        Q.list_orders()
