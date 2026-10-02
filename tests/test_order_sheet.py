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


# ----------------------------------------------------------------------------- review notes and corrections
def _confirmed(kind, rej=None, price=None):
    p = _partner(kind) if kind == "sales" else PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    oid = O.create(kind, p, _lines(price_crop=100, price_fert=9000))
    O.dispatch(kind, oid)
    O.confirm(kind, oid, _results(O.detail(kind, oid), rej=rej, price=price))
    return oid


@pytest.mark.parametrize("kind", ["sales", "purchase"])
def test_review_notes_work_at_any_stage_and_are_logged(kind):
    p = _partner(kind) if kind == "sales" else PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    oid = O.create(kind, p, _lines())
    O.add_review(kind, oid, "Delay", "Truck delayed at the border")                 # draft
    O.dispatch(kind, oid)
    O.add_review(kind, oid, "Damaged goods", "2 bags torn on the road")             # on the road
    O.confirm(kind, oid, _results(O.detail(kind, oid)))
    O.add_review(kind, oid, "bogus category", "called the driver")                  # confirmed; unknown -> Note
    r = O.detail(kind, oid)["reviews"]
    assert [(x["category"], x["correction"]) for x in r] == [("Delay", False), ("Damaged goods", False), ("Note", False)]
    assert r[0]["text"] == "Truck delayed at the border" and r[0]["by"] == "admin"
    with pytest.raises(ValueError):
        O.add_review(kind, oid, "Note", "  ")


def test_sales_correction_more_damaged_returns_stock_recalculates_and_logs():
    oid = _confirmed("sales")
    d = O.detail("sales", oid)
    assert d["total"] == 500 * 100 + 10 * 9000
    kg0, bags0 = stock(CROP_ID)[1], stock(CROP_ID)[0]
    crops = d["lines"][0]
    res = O.correct_line("sales", oid, crops["id"], "100 kg arrived wet and was refused later", rej_qty=100,
                         category="Damaged goods")
    assert res == {"total_before": 140000.0, "total_after": 130000.0, "stock_moved": True}
    assert stock(CROP_ID)[1] == kg0 + 100 and stock(CROP_ID)[0] == bags0 + 1              # 5 bags x 100/500
    d = O.detail("sales", oid)
    line = d["lines"][0]
    assert (line["rej_qty"], line["acc_qty"], line["final"]) == (100, 400, 40000) and d["total"] == 130000
    log = d["reviews"][-1]
    assert log["correction"] and log["category"] == "Damaged goods" and (log["total_before"], log["total_after"]) == (140000, 130000)
    assert d["balance"] == 130000


def test_sales_correction_without_stock_move_and_price_change_and_reversal():
    oid = _confirmed("sales")
    crops = O.detail("sales", oid)["lines"][0]
    kg0 = stock(CROP_ID)[1]
    O.correct_line("sales", oid, crops["id"], "destroyed in the buyer's store", rej_qty=100, stock_move=False)
    assert stock(CROP_ID)[1] == kg0 and O.detail("sales", oid)["total"] == 130000                # written off, not restocked
    O.correct_line("sales", oid, crops["id"], "agreed a discount", price=90, category="Price issue")
    assert O.detail("sales", oid)["lines"][0]["final"] == 400 * 90
    O.correct_line("sales", oid, crops["id"], "my mistake, nothing was damaged", rej_qty=0, stock_move=False)
    assert O.detail("sales", oid)["lines"][0]["acc_qty"] == 500


def test_purchase_correction_removes_damaged_goods_from_stock():
    oid = _confirmed("purchase")
    d = O.detail("purchase", oid)
    kg0 = stock(CROP_ID)[1]
    O.correct_line("purchase", oid, d["lines"][0]["id"], "found rotten in the warehouse", rej_qty=30)
    assert stock(CROP_ID)[1] == kg0 - 30
    d = O.detail("purchase", oid)
    assert d["lines"][0]["acc_qty"] == 470 and d["total"] == 470 * 100 + 10 * 9000
    O.correct_line("purchase", oid, d["lines"][0]["id"], "recounted - it was fine", rej_qty=0)    # reversing puts it back
    assert stock(CROP_ID)[1] == kg0 and O.detail("purchase", oid)["lines"][0]["acc_qty"] == 500


def test_correction_rules():
    oid = _confirmed("sales")
    d = O.detail("sales", oid)
    lid = d["lines"][0]["id"]
    with pytest.raises(ValueError, match="reason"):
        O.correct_line("sales", oid, lid, " ", rej_qty=5)
    with pytest.raises(ValueError, match="Nothing changed"):
        O.correct_line("sales", oid, lid, "x", rej_qty=0)
    with pytest.raises(ValueError, match="between 0"):
        O.correct_line("sales", oid, lid, "x", rej_qty=501)
    with pytest.raises(ValueError, match="negative"):
        O.correct_line("sales", oid, lid, "x", price=-1)
    with pytest.raises(ValueError, match="does not belong"):
        O.correct_line("sales", oid, 99999, "x", rej_qty=1)
    # not a confirmed order
    p = CompanyService.create("Other Co", ADMIN_ID)
    draft = O.create("sales", p, _lines())
    with pytest.raises(ValueError, match="confirmed"):
        O.correct_line("sales", draft, O.detail("sales", draft)["lines"][0]["id"], "x", rej_qty=1)
    assert O.detail("sales", oid)["reviews"] == []                                     # refusals logged nothing


def test_purchase_correction_refused_when_the_goods_are_no_longer_in_stock():
    oid = _confirmed("purchase")
    from database.services.sales_service import SaleService
    have = stock(CROP_ID)[1]
    SaleService.record({"buyer_party_id": 1, "created_by_user_id": ADMIN_ID,
                        "items": [{"product_id": CROP_ID, "qty_bags": 20, "total_weight_kg": have, "unit_price": 1}]})
    with pytest.raises(ValueError, match="Not enough"):
        O.correct_line("purchase", oid, O.detail("purchase", oid)["lines"][0]["id"], "rotten", rej_qty=30)
    assert O.detail("purchase", oid)["lines"][0]["rej_qty"] == 0                        # nothing half-applied


def test_overpayment_after_a_correction_is_shown_not_hidden():
    oid = _confirmed("sales")
    O.pay("sales", oid, 140000)
    d = O.detail("sales", oid)
    assert d["pay_status"] == "paid" and d["overpaid"] == 0
    O.correct_line("sales", oid, d["lines"][0]["id"], "damaged", rej_qty=100)
    d = O.detail("sales", oid)
    assert d["total"] == 130000 and d["balance"] == 0 and d["overpaid"] == 10000


def test_correction_is_admin_only_but_notes_are_for_everyone_who_works_orders():
    oid = _confirmed("sales")
    lid = O.detail("sales", oid)["lines"][0]["id"]
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    with pytest.raises(PermissionDenied):
        O.correct_line("sales", oid, lid, "x", rej_qty=1)
    O.add_review("sales", oid, "Note", "customer called")
    set_session(UserSession(user_id=CASHIER_A, username="w", role="weigher"))
    with pytest.raises(PermissionDenied):
        O.add_review("sales", oid, "Note", "x")
