"""The daily reminder: who still owes us (Advance OUT left, unpaid / part-paid sales), and when it is due."""
from datetime import date, datetime, timedelta

from apps.product_management_app.features.reminders.reminder_state import ReminderState
from database.services.advance_service import AdvanceService
from database.services.payment_service import PaymentService
from database.services.reminder_service import ReminderService as R
from database.services.sales_service import SaleService
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, CUSTOMER_ID, FERT_ID, SUPPLIER_ID, make_sale, pay


def _sale(price=100, bags=2, kg=200, buyer=CUSTOMER_ID, **kw):
    return make_sale([{"product_id": CROP_ID, "qty_bags": bags, "total_weight_kg": kg, "unit_price": price}],
                     buyer=buyer, **kw)["sale_id"]


def _group(data, party_id=CUSTOMER_ID):
    return next(g for g in data["groups"] if g["party_id"] == party_id)


def test_unpaid_part_paid_sales_and_advance_out_are_listed_with_when_and_status():
    unpaid = _sale()                                                   # 20,000, nothing paid
    part = _sale()                                                     # 20,000, 5,000 paid
    pay("sale", part, CUSTOMER_ID, "in", 5000)
    adv = AdvanceService.record_advance_out(CUSTOMER_ID, 100000, ADMIN_ID)
    PaymentService.record_advance_payment(adv, CUSTOMER_ID, "in", 50000, ADMIN_ID, method="cash")

    data = R.collect(today=date.today() + timedelta(days=12))
    g = _group(data)
    items = {i["ref"]: i for i in g["items"]}
    assert set(items) == {f"SAL-{unpaid}", f"SAL-{part}", f"ADV-{adv}"}
    assert (items[f"SAL-{unpaid}"]["status"], items[f"SAL-{unpaid}"]["balance"]) == ("unpaid", 20000)
    assert (items[f"SAL-{part}"]["status"], items[f"SAL-{part}"]["paid"], items[f"SAL-{part}"]["balance"]) == ("partial", 5000, 15000)
    a = items[f"ADV-{adv}"]
    assert (a["type"], a["total"], a["paid"], a["balance"], a["status"]) == ("advance_out", 100000, 50000, 50000, "partial")
    assert all(i["days"] == 12 and i["given_at"] is not None for i in g["items"])
    assert g["sales_balance"] == 35000 and g["advance_out_balance"] == 50000 and g["balance"] == 85000
    assert data["parties"] == 1 and data["total"] == 85000
    assert data["sales_total"] == 35000 and data["advance_out_total"] == 50000


def test_settled_voided_and_advance_in_are_not_listed():
    paid = _sale()
    pay("sale", paid, CUSTOMER_ID, "in", 20000)                        # fully paid
    voided = _sale()
    SaleService.void_sale(voided, ADMIN_ID, reason="mistake")
    adv = AdvanceService.record_advance_out(CUSTOMER_ID, 10000, ADMIN_ID)
    PaymentService.record_advance_payment(adv, CUSTOMER_ID, "in", 10000, ADMIN_ID, method="cash")   # paid back
    AdvanceService.record_advance_in(CUSTOMER_ID, 70000, ADMIN_ID)     # THEY gave US money: not a debt of theirs
    assert R.collect()["groups"] == []


def test_supplier_advance_out_counts_too_and_purchases_do_not():
    from tests.test_purchase_price_edit import _purchase
    _purchase()                                                        # we owe the supplier: not our reminder
    AdvanceService.record_advance_out(SUPPLIER_ID, 25000, ADMIN_ID)
    data = R.collect()
    assert [g["party_id"] for g in data["groups"]] == [SUPPLIER_ID]
    assert data["groups"][0]["items"][0]["type"] == "advance_out" and data["total"] == 25000


def test_longest_waiting_first_then_biggest():
    _sale(buyer=CUSTOMER_ID)                                           # 20,000 for customer one
    AdvanceService.record_advance_out(SUPPLIER_ID, 90000, ADMIN_ID)    # 90,000 for supplier, same age
    data = R.collect()
    assert [g["party_id"] for g in data["groups"]] == [SUPPLIER_ID, CUSTOMER_ID]        # same days -> bigger first


def test_the_balance_follows_a_price_edit():
    from database.services.sale_adjustment_service import SaleAdjustmentService as SA
    sid = _sale()
    item = SA.line_prices(sid)[0]["item_id"]
    SA.edit_prices(sid, {item: 110}, ADMIN_ID)                         # 200 kg x 10 more
    assert _group(R.collect())["balance"] == 22000


# --------------------------------------------------------------------------- when it is due
def test_reminder_is_due_once_a_day_after_the_hour(tmp_path, monkeypatch):
    monkeypatch.setenv("REMINDER_HOUR", "8")
    st = ReminderState(tmp_path / "state.json")
    day = datetime(2026, 11, 10)
    assert st.due(1, day.replace(hour=7, minute=59)) is False          # too early
    assert st.due(1, day.replace(hour=8)) is True
    st.mark_done(1, day.date())
    assert st.due(1, day.replace(hour=15)) is False                    # already shown today
    assert st.due(2, day.replace(hour=15)) is True                     # another user has their own
    assert st.due(1, (day + timedelta(days=1)).replace(hour=9)) is True


def test_remind_me_later_waits_two_hours_but_not_past_midnight(tmp_path, monkeypatch):
    monkeypatch.setenv("REMINDER_HOUR", "8")
    st = ReminderState(tmp_path / "state.json")
    now = datetime(2026, 11, 10, 9, 0)
    st.snooze(1, now)
    assert st.due(1, now + timedelta(hours=1, minutes=59)) is False
    assert st.due(1, now + timedelta(hours=2)) is True
    assert st.due(1, datetime(2026, 11, 11, 8, 0)) is True


def test_a_broken_state_file_never_blocks_the_reminder(tmp_path, monkeypatch):
    monkeypatch.setenv("REMINDER_HOUR", "0")
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    assert ReminderState(path).due(1, datetime(2026, 11, 10, 10, 0)) is True
