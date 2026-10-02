"""The order sheet's service layer: one flow for Sales and Purchase Orders."""
from datetime import date, timedelta

import pytest

from database.services.company_service import CompanyService
from database.services.order_sheet_service import OrderSheetService as O
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, FERT_ID, stock


def _partner(kind):
    return (CompanyService.create("Acme Foods", ADMIN_ID) if kind == "sales"
            else PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID))


def _lines(price_crop=100, price_fert=9000):
    return [{"product_id": CROP_ID, "name": "Maize", "type": "crops", "kg": 500, "bags": 5, "price": price_crop},
            {"product_id": FERT_ID, "name": "NPK", "type": "fertilizer", "kg": 0, "bags": 10, "price": price_fert}]


def _results(order, rcv=None, rej=None, price=None):
    out = []
    for l in order["lines"]:
        out.append({"id": l["id"], "type": l["type"], "rcv_qty": (rcv or {}).get(l["type"], l["kg"] or l["bags"]),
                    "rej_qty": (rej or {}).get(l["type"], 0), "rcv_price": (price or {}).get(l["type"], l["price"])})
    return out


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_create_reads_back_normalised_with_an_estimate(kind):
    p = _partner(kind)
    oid = O.create(kind, p, _lines(), notes="rush", expected=date.today() + timedelta(days=3))
    d = O.detail(kind, oid)
    assert d["status"] == "draft" and d["partner_name"] == ("Acme Foods" if kind == "sales" else "Farm Co")
    assert d["order_no"] == (f"SO-{oid}" if kind == "sales" else d["order_no"]) and d["notes"] == "rush"
    assert d["estimate"] == 500 * 100 + 10 * 9000 == d["total"] and d["estimated"] and d["balance"] is None
    crops, fert = d["lines"]
    assert (crops["kg"], crops["bags"], crops["price"], crops["qty_unit"]) == (500, 5, 100, "kg")
    assert (fert["kg"], fert["bags"], fert["qty_unit"]) == (0, 10, "bags")
    assert d["timeline"][0]["event"].startswith("Created")


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_draft_is_editable_and_validated(kind):
    p = _partner(kind)
    oid = O.create(kind, p, _lines()[:1])
    d = O.detail(kind, oid)
    lines = d["lines"] + [_lines()[1]]
    O.save_draft(kind, oid, lines, notes="two lines", expected=None)
    assert len(O.detail(kind, oid)["lines"]) == 2
    bad = [{**lines[0], "kg": 0}]
    with pytest.raises(ValueError):
        O.save_draft(kind, oid, bad)
    with pytest.raises(ValueError):
        O.save_draft(kind, oid, [])
    assert len(O.detail(kind, oid)["lines"]) == 2                  # a refused save changes nothing


def test_sales_flow_dispatch_deducts_stock_confirm_returns_rejected_and_totals():
    p = _partner("sales")
    oid = O.create("sales", p, _lines())
    kg0, bags0 = stock(CROP_ID)[1], stock(FERT_ID)[0]
    O.dispatch("sales", oid)
    assert stock(CROP_ID)[1] == kg0 - 500 and stock(FERT_ID)[0] == bags0 - 10
    d = O.detail("sales", oid)
    assert d["status"] == "in_transit" and d["dispatched_at"]
    res = O.confirm("sales", oid, _results(d, rej={"crops": 50}, price={"crops": 90, "fertilizer": 9000}))
    assert res["final_total_amount"] == 450 * 90 + 10 * 9000
    assert stock(CROP_ID)[1] == kg0 - 450                          # rejected 50 kg came back
    d = O.detail("sales", oid)
    assert d["status"] == "confirmed" and not d["estimated"] and d["total"] == 130500 and d["balance"] == 130500
    assert d["lines"][0]["rej_qty"] == 50 and d["lines"][0]["acc_qty"] == 450 and d["lines"][0]["final"] == 40500


def test_purchase_flow_stock_only_comes_in_at_confirmation_for_accepted_goods():
    p = _partner("purchase")
    oid = O.create("purchase", p, _lines(price_crop=80, price_fert=8000))
    kg0, bags0 = stock(CROP_ID)[1], stock(FERT_ID)[0]
    O.dispatch("purchase", oid)
    assert stock(CROP_ID)[1] == kg0 and stock(FERT_ID)[0] == bags0           # nothing yet
    d = O.detail("purchase", oid)
    O.confirm("purchase", oid, _results(d, rcv={"crops": 480}, rej={"crops": 30, "fertilizer": 1}))
    assert stock(CROP_ID)[1] == kg0 + 450 and stock(FERT_ID)[0] == bags0 + 9
    assert O.detail("purchase", oid)["total"] == 450 * 80 + 9 * 8000


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_progress_is_kept_and_confirm_needs_every_line(kind):
    p = _partner(kind)
    oid = O.create(kind, p, _lines())
    O.dispatch(kind, oid)
    d = O.detail(kind, oid)
    first = _results(d)[:1]
    O.save_result(kind, oid, first)
    d = O.detail(kind, oid)
    assert d["lines"][0]["rcv_qty"] == 500 and d["lines"][0]["acc_qty"] == 500 and d["lines"][1]["rcv_qty"] is None
    with pytest.raises(ValueError, match="Every line"):
        O.confirm(kind, oid, first)
    with pytest.raises(ValueError, match="must equal|cannot be negative"):
        O.confirm(kind, oid, _results(d, rej={"crops": 600}))                     # more rejected than received
    assert O.detail(kind, oid)["status"] == "in_transit"


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_revert_and_cancel_rules(kind):
    p = _partner(kind)
    oid = O.create(kind, p, _lines())
    O.dispatch(kind, oid)
    with pytest.raises(ValueError):
        O.cancel(kind, oid)                                                       # only a draft can be cancelled
    O.revert(kind, oid)
    assert O.detail(kind, oid)["status"] == "draft"
    with pytest.raises(ValueError):
        O.save_result(kind, oid, _results(O.detail(kind, oid)))                   # not on the road
    O.cancel(kind, oid, "customer changed mind")
    d = O.detail(kind, oid)
    assert d["status"] == "cancelled" and "changed mind" in d["notes"]
    with pytest.raises(ValueError, match="cancelled"):
        O.pay(kind, oid, 100)


def test_dispatch_needs_stock_and_is_all_or_nothing():
    p = _partner("sales")
    oid = O.create("sales", p, [{"product_id": CROP_ID, "name": "Maize", "type": "crops", "kg": 99999, "bags": 5, "price": 1}])
    with pytest.raises(ValueError, match="Insufficient stock"):
        O.dispatch("sales", oid)
    assert O.detail("sales", oid)["status"] == "draft"


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_payments_advance_before_confirmation_then_capped_and_voidable(kind):
    p = _partner(kind)
    oid = O.create(kind, p, _lines())
    O.pay(kind, oid, 50000, "transfer", "advance at order time")                  # no final price yet -> advance
    d = O.detail(kind, oid)
    assert d["paid"] == 50000 and d["balance"] is None and "Advance" in d["payments"][0]["notes"]    # only one so far
    O.dispatch(kind, oid)
    O.confirm(kind, oid, _results(O.detail(kind, oid)))
    d = O.detail(kind, oid)
    assert d["total"] == 500 * 100 + 10 * 9000 and d["balance"] == d["total"] - 50000 and d["pay_status"] == "partial"
    with pytest.raises(ValueError, match="exceeds"):
        O.pay(kind, oid, d["balance"] + 1)
    O.pay(kind, oid, d["balance"], "cash")
    d = O.detail(kind, oid)
    assert d["balance"] == 0 and d["pay_status"] == "paid"
    advance = next(p for p in d["payments"] if p["amount"] == 50000)
    with pytest.raises(ValueError, match="reason"):
        O.void_payment(advance["id"], "")
    O.void_payment(advance["id"], "keyed wrongly")
    assert O.detail(kind, oid)["balance"] == 50000 and O.detail(kind, oid)["paid"] == d["total"] - 50000


def test_permissions_cashier_works_the_delivery_and_money_but_cannot_create_or_dispatch():
    p = _partner("sales")
    oid = O.create("sales", p, _lines())
    O.dispatch("sales", oid)
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    for call in (lambda: O.create("sales", p, _lines()), lambda: O.save_draft("sales", oid, _lines()),
                 lambda: O.dispatch("sales", oid), lambda: O.revert("sales", oid), lambda: O.cancel("sales", oid)):
        with pytest.raises(PermissionDenied):
            call()
    d = O.detail("sales", oid)
    O.confirm("sales", oid, _results(d))
    O.pay("sales", oid, 1000)
    set_session(UserSession(user_id=CASHIER_A, username="w", role="weigher"))
    with pytest.raises(PermissionDenied):
        O.detail("sales", oid)


def test_partners_list_orders_of_and_products():
    set_session(UserSession(user_id=ADMIN_ID, username="admin", role="admin"))
    p = _partner("sales")
    oid = O.create("sales", p, _lines())
    O.dispatch("sales", oid)
    O.confirm("sales", oid, _results(O.detail("sales", oid)))
    rows = O.partners("sales")
    mine = next(r for r in rows if r["name"] == "Acme Foods")
    assert mine["orders"] == 1 and mine["outstanding"] == 140000 and mine["active"] == 0
    assert any(r["name"] == "Acme Foods" for r in O.partners("purchase"))          # shared pool
    assert [o["order_no"] for o in O.orders_of("sales", p)] == [f"SO-{oid}"]
    prods = {x["id"]: x for x in O.products("sales")}
    assert prods[CROP_ID]["price"] == 100 and prods[FERT_ID]["type"] == "fertilizer"
    assert {x["id"]: x for x in O.products("purchase")}[CROP_ID]["price"] == 80
