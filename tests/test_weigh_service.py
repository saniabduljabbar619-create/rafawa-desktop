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


def _submit(tid, line, price=450, receipt="full"):
    """The weigher's normal ending: price the line after weighing, then submit."""
    W.set_line_price(line, price)
    return W.submit_ticket(tid, receipt)


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


def test_weigher_sets_the_price_and_sees_the_amount_but_cannot_finalize():
    tid, line = _ticket()
    W.record_round(line, 3, 100)
    with pytest.raises(ValueError):
        W.set_line_price(line, 0)
    W.set_line_price(line, 450)
    t = W.get_ticket(tid)
    assert t["lines"][0]["unit_price"] == 450 and t["lines"][0]["amount"] == 45000 and t["total_amount"] == 45000
    assert W.list_tickets()[0]["total_amount"] == 45000
    with pytest.raises(PermissionDenied):
        W.finalize_ticket(tid, {line: 450})                    # finalizing and payment stay with the cashier
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        W.set_line_price(line, 1)                              # nobody else's ticket
    as_role("cashier", CASHIER)
    as_role("weigher", WEIGHER)
    W.submit_ticket(tid)
    as_role("cashier", CASHIER)
    W.set_line_price(line, 460)
    assert W.get_ticket(tid)["total_amount"] == 46000


def test_set_ticket_prices_is_all_or_nothing_and_audited():
    tid, line = _ticket()
    line2 = W.add_line(tid, CROP_ID, area="Other")
    W.record_round(line, 1, 100)
    W.record_round(line2, 1, 50)
    with pytest.raises(ValueError, match="price per kg"):
        W.set_ticket_prices(tid, {line: 400})                  # the second line has none
    assert W.get_ticket(tid)["lines"][0]["unit_price"] is None
    W.set_ticket_prices(tid, {line: 400, line2: 500})
    t = W.get_ticket(tid)
    assert t["total_amount"] == 100 * 400 + 50 * 500
    with get_session() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "weigh.price").count() == 2


def test_crop_products_carry_the_usual_price():
    from database.models.product import Product
    with get_session() as s:
        s.get(Product, CROP_ID).default_cost_rate = 420
    as_role("weigher", WEIGHER)
    assert {p["id"]: p["price"] for p in W.crop_products()}[CROP_ID] == 420


def test_submitting_needs_a_price_on_every_line():
    tid, line = _ticket()
    W.record_round(line, 1, 50)
    with pytest.raises(ValueError, match="Set the price per kg"):
        W.submit_ticket(tid, "summary")
    t = _submit(tid, line, receipt="summary")
    assert t["status"] == "submitted" and t["receipt_type"] == "summary"
    with pytest.raises(ValueError, match="already submitted"):
        W.submit_ticket(tid)


def test_weigher_can_edit_and_remove_own_ticket_until_the_cashier_finalizes_it():
    tid, line = _ticket()
    rnd = W.record_round(line, 1, 50)
    _submit(tid, line)
    # awaiting the cashier: still the weigher's to correct
    W.update_round(rnd["id"], weight_kg=60)
    extra = W.record_round(line, 2, 80)
    W.remove_round(extra["id"])
    W.set_line_price(line, 470)
    t = W.get_ticket(tid)
    assert t["status"] == "submitted" and t["total_kg"] == 60 and t["total_amount"] == 60 * 470
    with get_session() as s:
        actions = {a.action for a in s.query(AuditLog).all()}
        assert {"weigh.round_edited", "weigh.round_added", "weigh.round_removed", "weigh.price"} <= actions
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        W.update_round(rnd["id"], weight_kg=1)                # never another weigher's
    with pytest.raises(PermissionDenied):
        W.cancel_ticket(tid)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid, {line: 470})
    as_role("weigher", WEIGHER)
    with pytest.raises(ValueError):
        W.update_round(rnd["id"], weight_kg=1)                # finalized: frozen
    with pytest.raises(ValueError):
        W.cancel_ticket(tid)


def test_weigher_can_remove_a_submitted_ticket_before_it_is_finalized():
    tid, line = _ticket()
    W.record_round(line, 1, 50)
    _submit(tid, line)
    W.cancel_ticket(tid, "weighed the wrong delivery")
    t = W.get_ticket(tid)
    assert t["status"] == "cancelled" and t["cancel_reason"] == "weighed the wrong delivery"
    assert W.count_awaiting() == 0


def test_cannot_submit_without_rounds_and_empty_lines_are_dropped():
    tid, line = _ticket()
    with pytest.raises(ValueError):
        W.submit_ticket(tid)
    W.record_round(line, 1, 50)
    W.add_line(tid, CROP_ID, area="Other")         # never weighed
    t = _submit(tid, line)
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
    _submit(tid, line, price=400)                           # the weigher's price ...
    before = stock(CROP_ID)

    as_role("cashier", CASHIER)
    W.update_round(r1["id"], weight_kg=250)                 # verify & edit: scale misread
    extra = W.record_round(line, 1, 50)                     # a missed round
    t = W.get_ticket(tid)
    assert t["total_kg"] == 400 and t["total_bags"] == 6
    assert W.get_ticket(tid)["lines"][0]["unit_price"] == 400
    res = W.finalize_ticket(tid, {line: 450})                # ... which the cashier confirms (here: changes)
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
    _submit(tid, line, 100)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid, {line: 100})
    with get_session() as s:
        assert not s.get(Party, CUSTOMER_ID).is_supplier       # a customer selling to us stays a customer
    tid, line = _ticket(SUPPLIER_ID, "supplier")
    W.record_round(line, 1, 100)
    _submit(tid, line, 100)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid, {line: 100})
    with get_session() as s:
        assert s.get(Party, SUPPLIER_ID).is_supplier


def test_awaiting_count_and_status_filters():
    tid, line = _ticket()
    W.record_round(line, 1, 10)
    assert W.count_awaiting() == 0
    _submit(tid, line, 100)
    assert W.count_awaiting() == 1
    as_role("cashier", CASHIER)
    assert [t["id"] for t in W.list_tickets(status="submitted")] == [tid]
    assert W.list_tickets(status=["open"]) == []
    assert W.list_tickets(status="submitted")[0]["total_amount"] == 1000


def test_weigher_can_correct_own_round_and_the_round_limits_still_apply():
    tid, line = _ticket()
    rnd = W.record_round(line, 2, 100)
    W.update_round(rnd["id"], weight_kg=105)
    assert W.get_ticket(tid)["total_kg"] == 105
    with pytest.raises(ValueError):
        W.update_round(rnd["id"], bags=4)                       # still the 3-bag limit
    _submit(tid, line)
    W.update_round(rnd["id"], weight_kg=106)                    # awaiting the cashier: still correctable
    assert W.get_ticket(tid)["total_kg"] == 106
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        W.update_round(rnd["id"], weight_kg=1)                  # and never another weigher's


def test_walk_in_ticket_needs_a_name_and_finalizes_under_their_own_profile():
    from database.models.purchase import Purchase
    from shared.config.constants import WALKIN_PARTY_ID
    as_role("weigher", WEIGHER)
    with pytest.raises(ValueError):
        W.create_ticket(WALKIN_PARTY_ID, "customer")
    tid = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Alhaji Sule")
    line = W.add_line(tid, CROP_ID)
    W.record_round(line, 2, 100)
    _submit(tid, line, price=450)
    t = W.get_ticket(tid)
    assert t["party_name"] == "Alhaji Sule" and t["party_code"] == "WT-WKN-00001" and t["party_id"] != WALKIN_PARTY_ID
    as_role("cashier", CASHIER)
    res = W.finalize_ticket(tid)
    with get_session() as s:
        p = s.get(Purchase, res["purchase_id"])
        assert p.supplier_party_id == t["party_id"] and p.walkin_name is None
    assert all(p["kind"] != "supplier" for p in W.search_parties("", customers_only=True))


def test_two_walk_ins_with_the_same_name_are_two_people_with_their_own_ids():
    from shared.config.constants import WALKIN_PARTY_ID
    as_role("weigher", WEIGHER)
    a = W.get_ticket(W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Musa"))
    b = W.get_ticket(W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Musa"))
    assert a["party_id"] != b["party_id"] and (a["party_code"], b["party_code"]) == ("WT-WKN-00001", "WT-WKN-00002")
    c = W.new_walkin("Musa", "0803")                                     # also by hand, phone optional
    assert c["code"] == "WT-WKN-00003" and c["id"] not in (a["party_id"], b["party_id"])
    assert {w["code"] for w in W.search_walkins("musa")} == {"WT-WKN-00001", "WT-WKN-00002", "WT-WKN-00003"}


def test_a_walk_in_can_be_a_single_letter_and_found_by_name_phone_code_id_or_weighing_number():
    from shared.config.constants import WALKIN_PARTY_ID
    as_role("weigher", WEIGHER)
    ta = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="A")
    assert W.get_ticket(ta)["party_name"] == "A"
    tb = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Aminu")
    W.new_walkin("Bala", "0701")
    names = lambda q: [w["name"] for w in W.search_walkins(q)]               # noqa: E731
    assert names("a") == ["A", "Aminu", "Bala"] and names("min") == ["Aminu"] and names("0701") == ["Bala"]
    assert names("") == [] and names("zz") == []
    assert names("WT-WKN-00002") == ["Aminu"] and names("wt-wkn-00001") == ["A"]          # by ID code
    assert names(str(tb)) == ["Aminu"] and names(f"WT-{tb:05d}") == ["Aminu"]            # by a weighing's number
    pid = W.get_ticket(ta)["party_id"]
    assert names(str(pid)) == ["A"]                                                       # by ID number


def test_a_weighing_walk_in_does_not_need_the_shared_walk_in_account():
    from shared.config.constants import WALKIN_PARTY_ID
    with get_session() as s:
        s.query(Party).filter(Party.id == WALKIN_PARTY_ID).delete()
        s.commit()
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Musa")
    assert W.get_ticket(tid)["party_name"] == "Musa"


def test_a_walk_in_not_paid_yet_can_still_be_found_and_seen_in_his_activity():
    from database.services.party_ledger_service import PartyLedgerService
    from database.services.walkin_service import WalkinService
    from shared.config.constants import WALKIN_PARTY_ID
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name="Musa Kano")
    pid = W.get_ticket(tid)["party_id"]
    line = W.add_line(tid, CROP_ID)
    W.record_round(line, 2, 100)
    _submit(tid, line, price=450)                      # sent to the cashier, nobody pays yet
    assert [(d["name"], d["code"]) for d in WalkinService.search("mus")] == [("Musa Kano", "WT-WKN-00001")]
    assert WalkinService.weighings_for([pid])[pid] == [(f"WT-{tid:05d}", "submitted")]
    acts = PartyLedgerService.list_activity_details(pid)
    assert [(a["type"], a["status"], a["reference"]) for a in acts] == [("weighing", "pending", f"WT-{tid:05d}")]
    as_role("cashier", CASHIER)
    W.finalize_ticket(tid)                             # finalized but still unpaid: now it is a purchase
    acts = PartyLedgerService.list_activity_details(pid)
    assert [(a["type"], a["status"]) for a in acts] == [("purchase", "unpaid")]
    assert f"WT-{tid:05d}" in acts[0]["summary"]       # says which weighing it came from
    assert WalkinService.search("zzz") == []


def _walkin_purchase(name, kg=100, price=450):
    from shared.config.constants import WALKIN_PARTY_ID
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(WALKIN_PARTY_ID, "customer", walkin_name=name)
    line = W.add_line(tid, CROP_ID)
    W.record_round(line, 2, kg)
    _submit(tid, line, price=price)
    as_role("cashier", CASHIER)
    return W.finalize_ticket(tid)


def test_dashboard_lists_each_walk_in_with_their_own_id_not_as_one_lump():
    from database.services.dashboard_service import get_purchase_payment_snapshot
    _walkin_purchase("Musa Kano")
    _walkin_purchase("Ladi Bello")
    _walkin_purchase("Musa Kano")                                   # a different man with the same name
    as_role("admin", ADMIN_ID)
    rows = get_purchase_payment_snapshot(role="admin", user_id=None, scope="all")["rows"]
    assert sorted(r["supplier"] for r in rows) == [
        "Ladi Bello (WT-WKN-00002)", "Musa Kano (WT-WKN-00001)", "Musa Kano (WT-WKN-00003)"]


def test_payment_receipt_is_small_and_names_the_product_and_the_balance():
    from database.services.payment_service import PaymentService
    from apps.product_management_app.features.receipts_v2.payment_slip import build_payment_slip, render_payment_slip
    from apps.product_management_app.features.receipts_v2.shop import get_shop
    res = _walkin_purchase("Musa Kano", kg=100, price=450)         # 45,000 owed
    pid = PaymentService.record_purchase_payment(
        purchase_id=res["purchase_id"], party_id=res["party_id"], amount=20000,
        created_by_user_id=CASHIER, method="cash", notes="")
    text = render_payment_slip(build_payment_slip(pid), get_shop())
    assert "PAYMENT RECEIPT" in text and "Musa Kano (WT-WKN-00001)" in text
    assert "PAID NOW" in text and "20,000.00" in text and "25,000.00" in text      # balance left
    assert "2 bags 100.00 kg" in text
    assert len(text.splitlines()) < 30


def test_walk_ins_show_in_the_drawer_with_their_ids():
    from database.services.drawer import DrawerService
    from database.services.payment_service import PaymentService
    res = _walkin_purchase("Musa Kano", kg=100, price=450)
    _walkin_purchase("Musa Kano", kg=100, price=450)
    PaymentService.record_purchase_payment(
        purchase_id=res["purchase_id"], party_id=res["party_id"], amount=20000,
        created_by_user_id=CASHIER, method="manual", notes="")
    parties = {e["party"] for e in DrawerService.compute(CASHIER)["cash_events"] if e.get("party")}
    assert "Musa Kano (WT-WKN-00001)" in parties and "Walk-in" not in parties
    assert not any("WT-WKN-00002" in p for p in parties)            # the other Musa has no cash event


def test_a_weighing_walk_in_can_return_a_product_like_any_other_walk_in():
    from database.models.purchase_item import PurchaseItem
    from database.models.purchase_return import PurchaseReturn
    from database.services.return_service import ReturnService
    res = _walkin_purchase("Musa Kano", kg=100, price=450)
    as_role("admin", ADMIN_ID)
    with get_session() as s:
        item = s.query(PurchaseItem).filter(PurchaseItem.purchase_id == res["purchase_id"]).first()
        item_id = int(item.id)
    rid = ReturnService.create_return(document_type="purchase", document_id=res["purchase_id"], item_id=item_id,
                                      qty_kg=40, qty_bags=1, reason="bad quality", created_by_user_id=ADMIN_ID)
    with get_session() as s:
        assert s.get(PurchaseReturn, rid).party_id == res["party_id"]     # on his own profile, not the shared one


def test_walk_ins_met_at_the_till_get_their_own_WKN_id_and_are_found_by_it():
    from apps.product_management_app.app_state import state
    from database.services.walkin_service import WalkinService
    a = state.get_or_lazy_create_walkin("Ali", "")
    b = state.get_or_lazy_create_walkin("Ali", "")                  # same name, no phone -> a different person
    c = state.get_or_lazy_create_walkin("Ali", "0803")
    d = state.get_or_lazy_create_walkin("Someone Else", "0803")     # same phone -> the same person as c
    assert len({a, b, c}) == 3 and d == c
    assert [w["code"] for w in WalkinService.search("ali")] == ["WKN-00001", "WKN-00002", "WKN-00003"]
    assert [w["name"] for w in WalkinService.search("wkn-00002")] == ["Ali"]
    assert W.new_walkin("Musa")["code"] == "WT-WKN-00001"           # the weighing series is separate
