"""Intake keeps the weigher's price; the lists search and filter by date; walk-ins are apart and payable together."""
from datetime import datetime, timedelta

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox

from database.models.audit_log import AuditLog
from database.services.document_balance import document_balances
from database.services.weigh_service import WeighService as W
from database.session import get_session
from tests.conftest import ADMIN_ID, CROP_ID, SUPPLIER_ID
from tests.test_weigh_service import CASHIER, WEIGHER, as_role


def _ticket(party=SUPPLIER_ID, kg=100, price=450, submit=True, walkin_name=""):
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(party, "supplier", walkin_name=walkin_name)
    line = W.add_line(tid, CROP_ID, area="Gwarzo", grade="A")
    W.record_round(line, 3, kg)
    W.set_line_price(line, price)
    if submit:
        W.submit_ticket(tid, "value")
    return tid, line


def _walkin_ticket(name="Musa", **kw):
    as_role("weigher", WEIGHER)
    w = W.new_walkin(name, "08011112222")
    return _ticket(party=w["id"], **kw) + (w,)


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


# ------------------------------------------------------------------ 1. the weigher's own price
def test_admin_changing_the_price_leaves_the_weighers_price_alone():
    tid, line = _ticket(price=450)                                            # 100 kg x 450 = 45,000
    as_role("admin", ADMIN_ID)
    W.set_line_price(line, 500)                                               # intake confirms 500
    t = W.get_ticket(tid)
    assert t["lines"][0]["unit_price"] == 500 and t["total_amount"] == 50000   # what the cashier works with
    assert t["lines"][0]["weigher_price"] == 450 and t["weigher_total"] == 45000
    as_role("weigher", WEIGHER)
    mine = W.get_ticket(tid)
    assert mine["lines"][0]["unit_price"] == 450 and mine["total_amount"] == 45000     # the weigher still sees theirs
    assert [r["total_amount"] for r in W.list_tickets()] == [45000]
    as_role("admin", ADMIN_ID)
    assert [r["total_amount"] for r in W.list_tickets()] == [50000]


def test_finalizing_at_a_new_price_keeps_the_weighers_price_and_books_the_confirmed_one():
    tid, line = _ticket(price=450)
    as_role("cashier", CASHIER)
    res = W.finalize_ticket(tid, {line: 480})
    assert res["total_amount"] == 48000
    t = W.get_ticket(tid)
    assert t["lines"][0]["weigher_price"] == 450 and t["lines"][0]["unit_price"] == 480
    as_role("weigher", WEIGHER)
    mine = W.get_ticket(tid)
    assert mine["lines"][0]["unit_price"] == 450 and mine["total_amount"] == 45000
    assert mine["payment"]["total"] == 48000                                  # the purchase itself is at the confirmed price


def test_the_weigher_can_still_reprice_until_intake_and_that_becomes_their_price():
    tid, line = _ticket(price=450)
    W.set_line_price(line, 455)
    t = W.get_ticket(tid)
    assert t["lines"][0]["weigher_price"] == 455 and t["lines"][0]["unit_price"] == 455


def test_the_weighers_receipts_use_their_price_and_the_payment_receipt_the_confirmed_one():
    from apps.product_management_app.features.receipts_v2 import get_shop, render_weigh_slip
    tid, line = _ticket(price=450)
    as_role("cashier", CASHIER)
    W.finalize_ticket(tid, {line: 500})
    t = W.get_ticket(tid)                                                     # as the cashier sees it
    value = render_weigh_slip(t, get_shop(), "value")
    plain = value.replace("\x01", "")
    assert " 450" in plain and "45,000" in plain and " 500" not in plain
    assert "Confirmed total" in plain and "50,000" in plain                    # the difference is shown, not hidden
    pay = render_weigh_slip(t, get_shop(), "payment").replace("\x01", "")
    assert "50,000" in pay and "45,000" not in pay


def test_review_dialog_shows_the_weighers_price_beside_the_confirmed_one(qapp):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    tid, line = _ticket(price=450)
    as_role("admin", ADMIN_ID)
    d = WeighTicketReviewDialog(tid)
    assert d.lines_table.item(0, 6).text() == "₦450.00" and not d.lines_table.isColumnHidden(6)
    d._price_spins[line].setValue(520)
    assert d.lines_table.item(0, 5).text() == "₦52,000.00" and d.lines_table.item(0, 6).text() == "₦450.00"


# ------------------------------------------------------------------ 2. search and date / time range
def _backdate(tid, days):
    from database.models.weigh_ticket import WeighTicket
    with get_session() as s:
        t = s.get(WeighTicket, tid)
        t.created_at = datetime.now() - timedelta(days=days)
        if t.submitted_at:
            t.submitted_at = t.created_at
        s.commit()


def test_service_searches_by_ticket_name_phone_walkin_id_and_product():
    a, _ = _ticket()
    b, _l, w = _walkin_ticket("Hajiya Aisha")
    as_role("admin", ADMIN_ID)
    ids = lambda **k: {r["id"] for r in W.list_tickets(**k)}          # noqa: E731
    assert ids(search="supplier one") == {a}
    assert ids(search="aisha") == {b}
    assert ids(search="08011112222") == {b}
    assert ids(search=w["code"]) == {b}
    assert ids(search=f"WT-{a:05d}") == {a}
    assert ids(search="maize") == {a, b}
    assert ids(search="no such person") == set()


def test_service_filters_by_date_and_time_range():
    old, _ = _ticket()
    new, _ = _ticket()
    _backdate(old, 10)
    as_role("admin", ADMIN_ID)
    now = datetime.now()
    assert {r["id"] for r in W.list_tickets(date_from=now - timedelta(days=2))} == {new}
    assert {r["id"] for r in W.list_tickets(date_to=now - timedelta(days=2))} == {old}
    assert {r["id"] for r in W.list_tickets(date_from=now - timedelta(days=11), date_to=now - timedelta(days=9))} == {old}
    assert {r["id"] for r in W.list_tickets(search="maize", date_from=now - timedelta(days=2))} == {new}


def test_weigher_list_searches_and_ranges_across_days(qapp):
    from PySide2.QtCore import QDateTime
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    old, _ = _ticket()
    new, _ = _ticket()
    _backdate(old, 10)
    as_role("weigher", WEIGHER)
    v = WeigherTicketsView()
    assert v.table.rowCount() == 1                                            # today only, as before
    v.filters.search.setText("maize")                                         # a search looks across days
    v.filters.changed.emit()
    assert v.table.rowCount() == 2
    v.filters.search.clear()
    v.filters.chk_range.setChecked(True)
    v.filters.d_from.setDateTime(QDateTime.currentDateTime().addDays(-11))
    v.filters.d_to.setDateTime(QDateTime.currentDateTime().addDays(-9))
    assert v.table.rowCount() == 1 and v.table.item(0, 0).text() == f"WT-{old:05d}"


# ------------------------------------------------------------------ 3. walk-ins apart, paid together
def _admin_view():
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    as_role("admin", ADMIN_ID)
    v = WeighTicketsAdminView("admin")
    v.filter_seg.set_key("all")
    v.refresh()
    return v


def test_queue_keeps_customers_and_walkins_in_separate_lists(qapp):
    a, _ = _ticket()
    b, _l, _w = _walkin_ticket("Musa")
    c, _l2, _w2 = _walkin_ticket("Audu")
    v = _admin_view()
    assert v.table.rowCount() == 1 and v.walkin_table.rowCount() == 2
    assert v.table.item(0, 0).text() == f"WT-{a:05d}"
    assert {v.walkin_table.item(r, 1).text() for r in range(2)} == {f"WT-{b:05d}", f"WT-{c:05d}"}
    assert "(1)" in v.who_seg._buttons[0].text() and "Walk-ins (2)" in v.who_seg._buttons[1].text()
    v.who_seg.set_key("walkins")
    v.who_seg.changed.emit("walkins")
    assert not v.walkin_table.isHidden() and v.table.isHidden() and not v.btn_pay_walkins.isHidden()


def test_tick_select_all_and_pay_the_walkins_together(qapp, boxes):
    a, _ = _ticket()
    t1, _l1, _w1 = _walkin_ticket("Musa", kg=100, price=450)                  # 45,000
    t2, _l2, _w2 = _walkin_ticket("Audu", kg=200, price=400)                  # 80,000
    v = _admin_view()
    v.who_seg.set_key("walkins")
    v.who_seg.changed.emit("walkins")
    assert not v.btn_pay_walkins.isEnabled()
    v.walkin_table.item(0, 0).setCheckState(Qt.Checked)
    assert len(v.checked_ids()) == 1 and "1 selected" in v.lbl_picked.text()
    v.chk_all.setChecked(True)                                                # select all
    assert set(v.checked_ids()) == {t1, t2} and "₦125,000.00" in v.lbl_picked.text()
    v.cmb_method.setCurrentText("transfer")
    v.btn_pay_walkins.click()
    assert any("Paid 2 walk-in" in m and "125,000.00" in m for _k, m in boxes)
    from database.models.weigh_ticket import WeighTicket
    from database.models.payment import Payment
    with get_session() as s:
        done = s.query(WeighTicket).filter(WeighTicket.id.in_([t1, t2])).all()
        assert {t.status for t in done} == {"finalized"}
        for t in done:
            b = document_balances(s, "purchase", [t.purchase_id])[t.purchase_id]
            assert b.balance == 0
        pays = s.query(Payment).filter(Payment.entity_type == "purchase").all()
        assert sorted(float(p.amount) for p in pays) == [45000, 80000] and {p.method for p in pays} == {"transfer"}
        assert s.query(AuditLog).filter(AuditLog.action == "weigh.pay_walkins").count() == 1
        assert s.get(WeighTicket, a).status == "submitted"                    # the customer's ticket was not touched


def test_only_awaiting_intake_walkins_can_be_ticked_and_failures_are_reported(qapp, boxes):
    t1, _l1, _w1 = _walkin_ticket("Musa")
    t2, _l2, _w2 = _walkin_ticket("Audu", submit=False)                       # still being weighed
    v = _admin_view()
    v.who_seg.set_key("walkins")
    v.who_seg.changed.emit("walkins")
    rows = {int(v.walkin_table.item(r, 0).data(Qt.UserRole)): r for r in range(v.walkin_table.rowCount())}
    assert v.walkin_table.item(rows[t2], 0).flags() & Qt.ItemIsUserCheckable == 0
    v.chk_all.setChecked(True)
    assert v.checked_ids() == [t1]
    as_role("admin", ADMIN_ID)
    res = W.pay_walkins([t1, t2, 999999], "cash")                             # service refuses the rest, one by one
    assert [p["amount"] for p in res["paid"]] == [45000] and len(res["failed"]) == 2


def test_customers_are_never_paid_through_the_walkin_route():
    a, _ = _ticket()
    as_role("admin", ADMIN_ID)
    res = W.pay_walkins([a], "cash")
    assert res["paid"] == [] and "Not a walk-in" in res["failed"][0]["error"]
