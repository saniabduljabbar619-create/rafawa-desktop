"""Debt Center: the standalone creditor/debtor book, and Expenses vs Expenditures."""
from datetime import date, timedelta

import pytest

from database.models.party import Party
from database.models.payment import Payment
from database.models.purchase import Purchase
from database.models.sale import Sale
from database.services.debt_book_service import DebtBookService as B
from database.services.expense_service import ExpenditureService as XD, ExpenseService as E
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import CASHIER_A, make_sale


def _person(kind="creditor", name="Alhaji Musa"):
    return B.add_person(kind, name, phone="0803")


def _row(pid, details="Fertilizer on credit", owed=0, paid=0, **kw):
    return B.add_entry(pid, {"details": details, "owed": owed, "paid": paid, **kw})


# ----------------------------------------------------------------- standalone book
def test_any_number_of_people_in_each_list():
    for i in range(25):
        _person("creditor", f"Creditor {i}")
        _person("debtor", f"Debtor {i}")
    assert len(B.people("creditor")) == 25 and len(B.people("debtor")) == 25
    assert B.overview()["creditor"]["people"] == 25


def test_the_book_never_touches_customers_sales_or_the_drawer():
    """Recording debts must not create parties, sales, purchases or payments - and ignores existing ones."""
    make_sale()                                                   # a real customer who owes 20,000
    with get_session() as s:
        before = (s.query(Party).count(), s.query(Sale).count(), s.query(Purchase).count(), s.query(Payment).count())
    pid = _person("debtor", "Customer One")                       # same name as a real customer
    _row(pid, owed=5000)
    _row(pid, "part payment", paid=1000)
    with get_session() as s:
        after = (s.query(Party).count(), s.query(Sale).count(), s.query(Purchase).count(), s.query(Payment).count())
    assert before == after
    assert B.sheet(pid)["figures"]["balance"] == 4000             # only what was typed here, not the 20,000 sale
    # and the real customer's figures are untouched by anything done here
    from database.services.customer_directory_service import CustomerDirectoryService
    assert CustomerDirectoryService.summaries([1])[1]["receivable"] == 20000


def test_sheet_has_running_balance_and_totals():
    pid = _person()
    a = _row(pid, owed=50000, date="01/06/2026")
    _row(pid, "paid part", paid=20000, date="10/06/2026", method="transfer")
    _row(pid, "more goods", owed=5000, date="15/06/2026")
    sh = B.sheet(pid)
    assert [r["running"] for r in sh["rows"]] == [50000, 30000, 35000]
    assert sh["figures"] == {"owed": 55000, "paid": 20000, "balance": 35000}
    assert sh["person"]["balance"] == 35000 and sh["person"]["entries"] == 3
    assert B.people("creditor")[0]["balance"] == 35000 and a


def test_rows_are_ordered_by_date_then_order_written():
    pid = _person()
    _row(pid, "later", owed=1, date="20/06/2026")
    _row(pid, "earlier", owed=1, date="01/06/2026")
    assert [r["details"] for r in B.sheet(pid)["rows"]] == ["earlier", "later"]


def test_row_validation():
    pid = _person()
    for bad in ({"details": "", "owed": 5}, {"details": "x"}, {"details": "x", "owed": -1},
                {"details": "x", "owed": "abc"}, {"details": "x", "owed": 5, "date": "not a date"}):
        with pytest.raises(ValueError):
            B.add_entry(pid, bad)
    assert B.sheet(pid)["rows"] == []
    assert _row(pid, owed="1,500.50") and B.sheet(pid)["figures"]["owed"] == 1500.5      # commas are fine


def test_edit_a_cell_is_audited_and_recalculates():
    from database.models.audit_log import AuditLog
    pid = _person()
    e = _row(pid, owed=10000)
    B.edit_entry(e, {"owed": "12000"})
    assert B.sheet(pid)["figures"]["owed"] == 12000
    with get_session() as s:
        log = s.query(AuditLog).filter(AuditLog.action == "debt.entry.edit").one()
        assert "10000" in log.details and "12000" in log.details
    with pytest.raises(ValueError):
        B.edit_entry(e, {"owed": 0, "paid": 0})                   # would leave an empty row
    with pytest.raises(ValueError):
        B.edit_entry(e, {"bogus": 1})


def test_remove_crosses_out_stops_counting_and_can_come_back():
    pid = _person()
    a, b = _row(pid, owed=100), _row(pid, "x", owed=50)
    B.remove_entry(a, "typed twice")
    sh = B.sheet(pid)
    assert sh["figures"]["balance"] == 50 and sh["rows"][0]["removed"] and sh["rows"][0]["removed_reason"] == "typed twice"
    assert [r["running"] for r in sh["rows"]] == [0, 50]
    with pytest.raises(ValueError):
        B.edit_entry(a, {"owed": 1})
    B.restore_entry(a)
    assert B.sheet(pid)["figures"]["balance"] == 150 and b


def test_overpaid_shows_as_negative_balance_and_overview_counts_only_what_is_owing():
    c1, c2 = _person("creditor", "A"), _person("creditor", "B")
    _row(c1, owed=1000)
    _row(c2, owed=100, paid=300)                                  # overpaid
    d = _person("debtor", "D")
    _row(d, owed=700)
    ov = B.overview()
    assert ov["creditor"]["balance"] == 1000 and ov["creditor"]["owing"] == 1
    assert ov["debtor"]["balance"] == 700 and ov["net"] == -300
    assert [r["name"] for r in B.people("creditor")] == ["A", "B"]          # biggest balance first


def test_people_search_archive_edit_and_duplicates():
    pid = _person("creditor", "Alhaji Musa")
    with pytest.raises(ValueError):
        B.add_person("creditor", "alhaji musa")
    B.add_person("debtor", "Alhaji Musa")                          # same name in the other list is fine
    assert [r["name"] for r in B.people("creditor", search="0803")] == ["Alhaji Musa"]
    assert B.people("creditor", search="zzz") == []
    B.update_person(pid, "Alhaji Musa Kano", phone="0900")
    assert B.people("creditor")[0]["name"] == "Alhaji Musa Kano"
    B.set_archived(pid, True)
    assert B.people("creditor") == [] and len(B.people("creditor", include_archived=True)) == 1
    with pytest.raises(ValueError):
        B.add_person("neighbour", "x")
    with pytest.raises(ValueError):
        B.add_person("debtor", "  ")


def test_only_admins_may_use_the_book_and_outflow_sheets():
    pid = _person()
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    for call in (lambda: B.people("creditor"), lambda: B.add_entry(pid, {"details": "x", "owed": 1}),
                 lambda: E.add({"amount": 5, "description": "x"}), lambda: XD.list_expenses()):
        with pytest.raises(PermissionDenied):
            call()


# ----------------------------------------------------------------- expenses vs expenditures
def _exp(svc=E, **kw):
    d = {"amount": 15000, "category": "Rent", "description": "Shop rent", "date": date.today()}
    d.update(kw)
    return svc.add(d)


def test_expenses_and_expenditures_are_separate_records():
    e = _exp(description="Diesel", amount=2000)
    x = _exp(XD, description="Delivery truck", amount=4_500_000, category="Vehicles")
    assert [r["description"] for r in E.list_expenses()] == ["Diesel"]
    assert [r["description"] for r in XD.list_expenses()] == ["Delivery truck"]
    assert E.list_expenses()[0]["expense_no"] == f"EXP-{e:05d}"
    assert XD.list_expenses()[0]["expense_no"] == f"EXD-{x:05d}"
    assert E.summary()["total"] == 2000 and XD.summary()["total"] == 4_500_000
    assert "Vehicles" in XD.categories() and "Vehicles" not in E.categories()
    assert "Rent" in E.categories() and "Rent" not in XD.categories()


def test_outflow_validation_and_defaults():
    row = _exp(category="")
    assert E.list_expenses()[0]["category"] == "General" and row
    for bad in ({"amount": 0}, {"description": " "}, {"date": date.today() + timedelta(days=30)}):
        with pytest.raises(ValueError):
            _exp(**bad)


def test_void_restore_edit_and_audit_for_both_books():
    from database.models.audit_log import AuditLog
    for svc, tag in ((E, "expense"), (XD, "expenditure")):
        eid = _exp(svc)
        with pytest.raises(ValueError):
            svc.void(eid, "")
        svc.void(eid, "entered twice")
        assert svc.list_expenses() == [] and len(svc.list_expenses(include_voided=True)) == 1
        with pytest.raises(ValueError):
            svc.update(eid, {"amount": 1, "description": "x"})
        svc.restore(eid)
        svc.update(eid, {"amount": 16000, "category": "Rent", "description": "Shop rent"})
        assert svc.list_expenses()[0]["amount"] == 16000
        with get_session() as s:
            actions = {a.action for a in s.query(AuditLog).all()}
        assert {f"{tag}.void", f"{tag}.restore", f"{tag}.edit"} <= actions


def test_summary_splits_by_category_and_filters_by_period():
    today = date.today()
    _exp(amount=30000, category="Rent")
    _exp(amount=10000, category="Salaries & Wages", description="Wages")
    _exp(amount=999, category="Rent", description="old", date=today - timedelta(days=400))
    s = E.summary(start=today - timedelta(days=30), end=today)
    assert s["total"] == 40000 and s["count"] == 2
    assert [(c["category"], round(c["share"], 2)) for c in s["by_category"]] == [("Rent", 0.75), ("Salaries & Wages", 0.25)]
    assert s["today"] == 40000 and len(s["trend"]) == 6
    assert E.summary(category="Rent")["total"] == 30000 + 999
    assert len(E.list_expenses(search="Wages")) == 1
