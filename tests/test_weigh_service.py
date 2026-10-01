"""Weigher workspace backend: the weigher records what arrived; admin/cashier price, pay, finalize."""
import pytest

from database.models.audit_log import AuditLog
from database.models.party import Party
from database.models.stock_movement import StockMovement
from database.services.weigh_service import WeighService as W
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CASHIER_B, CUSTOMER_ID, SUPPLIER_ID, CROP_ID, FERT_ID, stock

WEIGHER = CASHIER_A          # users 2/3 exist in the test world; the session role is what matters
WEIGHER_2 = CASHIER_B
CASHIER = 4


def as_role(role, uid):
    set_session(UserSession(user_id=uid, username=f"{role}{uid}", role=role))


def _ticket(party=SUPPLIER_ID, role="supplier"):
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(party, role)
    line = W.add_line(tid, CROP_ID, area="Gwarzo", grade="A")
    return tid, line


def test_weigher_records_rounds_and_totals_are_automatic():
    tid, line = _ticket()
    W.record_round(line, 3, 245.5)
    W.record_round(line, 2, 160)
    W.record_round(line, 0.5, 21.25)               # a partial bag
    t = W.get_ticket(tid)
    assert (t["total_bags"], t["total_kg"]) == (5.5, 426.75)
    assert [r["round_no"] for r in t["lines"][0]["rounds"]] == [1, 2, 3]
    assert t["ticket_no"] == f"WT-{tid:05d}" and t["status"] == "open"


def test_round_validation_for_the_weigher():
    _tid, line = _ticket()
    for bags, kg in ((4, 100), (0, 100), (1, 0), (1, 9999), (-1, 5)):
        with pytest.raises(ValueError):
            W.record_round(line, bags, kg)


def test_weigher_never_sees_prices_and_cannot_price_or_finalize():
    tid, line = _ticket()
    W.record_round(line, 3, 100)
    W.submit_ticket(tid, "full")
    as_role("cashier", CASHIER)
    W.set_line_price(line, 450)
    as_role("weigher", WEIGHER)
    t = W.get_ticket(tid)
    assert t["total_amount"] is None and t["lines"][0]["unit_price"] is None and t["lines"][0]["amount"] is None
    assert all(r["total_amount"] is None for r in W.list_tickets())
    with pytest.raises(PermissionDenied):
        W.set_line_price(line, 1)
    with pytest.raises(PermissionDenied):
        W.finalize_ticket(tid, {line: 450})


def test_submit_locks_the_ticket_for_the_weigher():
    tid, line = _ticket()
    rnd = W.record_round(line, 1, 50)
    W.submit_ticket(tid, "summary")
    assert W.get_ticket(tid)["status"] == "submitted" and W.get_ticket(tid)["receipt_type"] == "summary"
    with pytest.raises(PermissionDenied):
        W.record_round(line, 1, 50)
    with pytest.raises(PermissionDenied):
        W.remove_round(rnd["id"])
    with pytest.raises(PermissionDenied):
        W.submit_ticket(tid)                                  # already submitted


def test_cannot_submit_without_rounds_and_empty_lines_are_dropped():
    tid, line = _ticket()
    with pytest.raises(ValueError):
        W.submit_ticket(tid)
    W.record_round(line, 1, 50)
    W.add_line(tid, CROP_ID, area="Other")         # never weighed
    t = W.submit_ticket(tid)
    assert len(t["lines"]) == 1


def test_a_weigher_only_touches_their_own_tickets():
    tid, line = _ticket()
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        W.get_ticket(tid)
    with pytest.raises(PermissionDenied):
        W.record_round(line, 1, 10)
    with pytest.raises(PermissionDenied):
        W.cancel_ticket(tid)
    assert W.list_tickets() == []


def test_products_must_be_active_crops_and_party_must_exist():
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    with pytest.raises(ValueError):
        W.add_line(tid, FERT_ID)
    with pytest.raises(ValueError):
        W.create_ticket(9999, "supplier")
    with pytest.raises(ValueError):
        W.create_ticket(SUPPLIER_ID, "walkin")


def test_register_party_and_search_no_duplicate_phones():
    as_role("weigher", WEIGHER)
    p = W.register_party("supplier", "Musa Farms", "08031234567", "Kano")
    assert p["kind"] == "supplier"
    with pytest.raises(ValueError):
        W.register_party("customer", "Someone Else", "08031234567")
    with pytest.raises(ValueError):
        W.register_party("customer", "A", "123")
    assert [x["name"] for x in W.search_parties("musa")] == ["Musa Farms"]
    assert any(x["name"] == "Musa Farms" for x in W.search_parties("0803123"))
    with get_session() as s:
        row = s.get(Party, p["id"])
        assert row.is_supplier and not row.is_customer


def test_cashier_verifies_edits_prices_and_finalizes_into_a_purchase():
    tid, line = _ticket()
    r1 = W.record_round(line, 3, 245)
    W.record_round(line, 2, 100)
    W.submit_ticket(tid)
    before = stock(CROP_ID)

    as_role("cashier", CASHIER)
    W.update_round(r1["id"], weight_kg=250)                 # verify & edit: scale misread
    extra = W.record_round(line, 1, 50)                     # a missed round
    t = W.get_ticket(tid)
    assert t["total_kg"] == 400 and t["total_bags"] == 6
    with pytest.raises(ValueError):
        W.finalize_ticket(tid)                              # price not confirmed yet
    res = W.finalize_ticket(tid, {line: 450})
    assert res["total_amount"] == 400 * 450

    t = W.get_ticket(tid)
    assert t["status"] == "finalized" and t["purchase_id"] == res["purchase_id"]
    assert t["total_amount"] == 180000 and t["lines"][0]["unit_price"] == 450
    assert stock(CROP_ID) == (before[0] + 6, before[1] + 400)   # stock came in
    with pytest.raises(ValueError):
        W.finalize_ticket(tid, {line: 450})                  # never twice
    with pytest.raises(ValueError):
        W.update_round(extra["id"], bags=2)                  # finalized tickets are frozen

    with get_session() as s:
        actions = {a.action for a in s.query(AuditLog).all()}
        assert {"weigh.create", "weigh.submit", "weigh.round_edited", "weigh.round_added",
                "weigh.finalize"} <= actions
        assert s.query(StockMovement).count() >= 1


def test_cashier_cannot_edit_a_ticket_still_being_weighed_but_admin_can_cancel():
    tid, line = _ticket()
    rnd = W.record_round(line, 1, 50)
    as_role("cashier", CASHIER)
    with pytest.raises(PermissionDenied):
        W.update_round(rnd["id"], weight_kg=60)
    as_role("admin", ADMIN_ID)
    W.cancel_ticket(tid, "customer left")
    t = W.get_ticket(tid)
    assert t["status"] == "cancelled" and t["cancel_reason"] == "customer left"
    with pytest.raises(ValueError):
        W.finalize_ticket(tid, {line: 1})


def test_party_role_decides_whether_a_customer_becomes_a_supplier():
    tid, line = _ticket(CUSTOMER_ID, "customer")
    W.record_round(line, 1, 100)
    W.submit_ticket(tid)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid, {line: 100})
    with get_session() as s:
        assert not s.get(Party, CUSTOMER_ID).is_supplier       # a customer selling to us stays a customer
    tid, line = _ticket(SUPPLIER_ID, "supplier")
    W.record_round(line, 1, 100)
    W.submit_ticket(tid)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid, {line: 100})
    with get_session() as s:
        assert s.get(Party, SUPPLIER_ID).is_supplier


def test_awaiting_count_and_status_filters():
    tid, line = _ticket()
    W.record_round(line, 1, 10)
    assert W.count_awaiting() == 0
    W.submit_ticket(tid)
    assert W.count_awaiting() == 1
    as_role("cashier", CASHIER)
    assert [t["id"] for t in W.list_tickets(status="submitted")] == [tid]
    assert W.list_tickets(status=["open"]) == []
    assert W.list_tickets(status="submitted")[0]["total_amount"] == 0


def test_weigher_can_correct_own_round_while_open_but_not_after_submitting():
    tid, line = _ticket()
    rnd = W.record_round(line, 2, 100)
    W.update_round(rnd["id"], weight_kg=105)
    assert W.get_ticket(tid)["total_kg"] == 105
    with pytest.raises(ValueError):
        W.update_round(rnd["id"], bags=4)                       # still the 3-bag limit
    W.submit_ticket(tid)
    with pytest.raises(PermissionDenied):
        W.update_round(rnd["id"], weight_kg=1)
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        W.update_round(rnd["id"], weight_kg=1)                  # and never another weigher's
