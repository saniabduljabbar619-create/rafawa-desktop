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


# ---------------------------------------------------------------- editing lines, confirming
def _cart(fert=80):
    return [{"product_id": FERT_ID, "product_type": "fertilizer", "name": "NPK", "unit_price": 10000,
             "unit_cost_at_sale": 9000, "qty_bags": fert, "total_weight_kg": 0}]


def _sourcing(bags=30, supplier=SUPPLIER_ID, cart=None):
    """80 bags wanted, 50 in stock, 30 being sourced."""
    return Q.create([{"product_id": FERT_ID, "qty_bags": bags, "unit_cost": 9300, "supplier_id": supplier}],
                    send=True, cart=_cart() if cart is None else cart)


def test_editing_a_line_keeps_the_rest_unassigned_until_routed_or_dropped():
    oid = _sourcing()
    (ln,) = Q.get(oid)["lines"]
    Q.edit_line(oid, ln["id"], qty=20, unit_cost=9400)
    a, rest = Q.get(oid)["lines"]
    assert (a["qty_bags"], a["unit_cost"], a["supplier_id"]) == (20, 9400, SUPPLIER_ID)
    assert (rest["qty_bags"], rest["assigned"], rest["state"]) == (10, False, "open")
    with pytest.raises(ValueError, match="Assign a supplier"):
        Q.plan_confirm(oid)
    Q.edit_line(oid, rest["id"], supplier_id=SUPPLIER_2, unit_cost=9500)       # another supplier has the rest
    assert Q.get(oid)["lines"][1]["routes"][0]["supplier"] == "Supplier Two"
    Q.edit_line(oid, a["id"], supplier_id=0)                                   # first one: not available after all
    assert not Q.get(oid)["lines"][0]["assigned"] and Q.get(oid)["lines"][0]["routes"][0]["outcome"] == "unavailable"
    Q.drop_line(oid, a["id"], "nobody has it")
    assert Q.get(oid)["lines"][0]["state"] == "dropped" and Q.get(oid)["status"] == "pending"
    with pytest.raises(ValueError, match="still waiting"):
        Q.edit_line(oid, a["id"], qty=5)
    Q.drop_line(oid, rest["id"])                                               # nothing left: order closes
    assert Q.get(oid)["status"] == "cancelled"


def test_receive_skips_lines_without_a_supplier():
    oid = _sourcing()
    (ln,) = Q.get(oid)["lines"]
    Q.edit_line(oid, ln["id"], qty=10)
    Q.receive(oid)
    o = Q.get(oid)
    assert [x["state"] for x in o["lines"]] == ["received", "open"] and o["status"] == "pending"
    assert stock(FERT_ID)[0] == 60


def test_confirm_buys_sells_and_records_the_payments():
    from database.models.payment import Payment
    oid = _sourcing()
    plan = Q.plan_confirm(oid)
    (sl,) = plan["sale_lines"]
    assert (sl["wanted"], sl["have"], sl["incoming"], sl["sell_qty"], sl["short"]) == (80, 50, 30, 80, 0)
    assert plan["suppliers"][0]["amount"] == 30 * 9300 and plan["open_total"] == 279000
    res = Q.confirm(oid, {FERT_ID: 10500}, supplier_paid={SUPPLIER_ID: 100000}, customer_paid=500000,
                    buyer_party_id=1)
    assert res["sale_total"] == 80 * 10500 and res["customer_owes"] == 340000
    assert res["paid_to_suppliers"] == 100000 and res["owed_to_suppliers"] == 179000 and not res["warnings"]
    o = Q.get(oid)
    assert o["status"] == "completed" and o["sale_id"] == res["sale_id"]
    assert stock(FERT_ID)[0] == 0                                              # 50 + 30 bought - 80 sold
    with get_session() as s:
        pays = {(p.entity_type, p.direction): float(p.amount) for p in s.query(Payment)}
        assert pays == {("purchase", "out"): 100000.0, ("sale", "in"): 500000.0}
        from database.models.sale_item import SaleItem
        costs = sorted((float(i.unit_cost_snapshot), i.qty_bags) for i in s.query(SaleItem))
        assert costs == [(9000.0, 50), (9300.0, 30)]      # from-stock lot at book cost, bought lot at the dealer's
    with pytest.raises(ValueError, match="already finished"):
        Q.confirm(oid, {FERT_ID: 10500})


def test_confirm_sells_only_what_could_be_sourced():
    oid = _sourcing(bags=30)
    (ln,) = Q.get(oid)["lines"]
    Q.edit_line(oid, ln["id"], qty=10)
    Q.drop_line(oid, Q.get(oid)["lines"][1]["id"])                            # the other 20 bags cannot be had
    (sl,) = Q.plan_confirm(oid)["sale_lines"]
    assert (sl["sell_qty"], sl["short"], sl["qp_part"], sl["stock_part"]) == (60, 20, 10, 50)
    res = Q.confirm(oid, {FERT_ID: 10000}, buyer_party_id=1)
    assert res["sale_total"] == 600000 and res["customer_owes"] == 600000 and res["owed_to_suppliers"] == 93000
    assert stock(FERT_ID)[0] == 0


def test_confirm_with_a_missing_price_buys_nothing_and_a_failed_sale_undoes_the_purchases(monkeypatch):
    from database.services.sales_service import SaleService
    oid = _sourcing()
    with pytest.raises(ValueError, match="selling price"):
        Q.confirm(oid, {}, buyer_party_id=1)
    assert stock(FERT_ID)[0] == 50 and Q.get(oid)["status"] == "pending"
    monkeypatch.setattr(SaleService, "record", staticmethod(lambda payload: (_ for _ in ()).throw(ValueError("boom"))))
    with pytest.raises(ValueError, match="boom"):
        Q.confirm(oid, {FERT_ID: 10000}, buyer_party_id=1)                    # the sale fails after buying
    assert stock(FERT_ID)[0] == 50 and Q.get(oid)["status"] == "pending"
    with get_session() as s:
        assert s.query(Purchase).count() == 0


def test_confirm_without_a_cart_only_receives_and_pays_the_suppliers():
    oid = _sourcing(cart=[])
    plan = Q.plan_confirm(oid)
    assert not plan["has_sale"]
    res = Q.confirm(oid, {}, supplier_paid={SUPPLIER_ID: 279000})
    assert res["sale_id"] is None and res["owed_to_suppliers"] == 0 and Q.get(oid)["status"] == "completed"
    assert stock(FERT_ID)[0] == 80


def test_a_pending_order_can_be_edited_as_a_whole():
    oid = Q.create(_lines(), send=True, customer_name="Musa")
    fert, crop = Q.get(oid)["lines"]
    Q.receive(oid, [fert["id"]])                                   # the fertilizer already arrived
    # edit the waiting maize (qty down, new cost, another supplier), add a new product, rename the customer
    Q.update_pending(oid, [
        {"id": crop["id"], "product_id": CROP_ID, "total_weight_kg": 200, "qty_bags": 4, "unit_cost": 90,
         "supplier_id": SUPPLIER_2},
        {"product_id": FERT_ID, "qty_bags": 5, "unit_cost": 9400, "supplier_id": SUPPLIER_ID}],
        customer_name="Musa Farms", notes="edited")
    o = Q.get(oid)
    assert o["customer_name"] == "Musa Farms" and o["notes"] == "edited" and o["status"] == "pending"
    by_state = [(x["product"], x["state"], x["qty"], x["unit_cost"], x["supplier"]) for x in o["lines"]]
    assert by_state == [("NPK", "received", 10, 9500, "Supplier One"), ("Maize", "open", 200, 90, "Supplier Two"),
                        ("NPK", "open", 5, 9400, "Supplier One")]                # no leftover line was created
    # a waiting line left out of the editor is given up; the received one is never touched
    Q.update_pending(oid, [{"id": o["lines"][2]["id"], "product_id": FERT_ID, "qty_bags": 5, "unit_cost": 9400,
                            "supplier_id": SUPPLIER_ID}])
    o = Q.get(oid)
    assert [x["state"] for x in o["lines"]] == ["received", "dropped", "open"]
    with pytest.raises(ValueError, match="at least one"):
        Q.update_pending(oid, [])
    with pytest.raises(ValueError, match="only possible when it is pending"):
        Q.update_pending(Q.create(_lines()), [{"product_id": FERT_ID, "qty_bags": 1}])     # a draft uses update_draft
