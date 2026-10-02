"""Debt Center: creditors, debtors, worksheets, paying down, and expenses."""
from datetime import date, timedelta

import pytest

from database.models.payment import Payment
from database.services.debt_center_service import DebtCenterService as D
from database.services.document_balance import document_balances
from database.services.expense_service import ExpenseService as E
from database.services.purchase_service import PurchaseService
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CUSTOMER_ID, SUPPLIER_ID, make_sale, pay


def _purchase(party=SUPPLIER_ID, kg=1000, cost=50):                      # 50,000
    return PurchaseService.record({"supplier_party_id": party, "created_by_user_id": CASHIER_A,
        "items": [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
                   "qty_bags": 10, "total_weight_kg": kg, "unit_cost": cost}]})["purchase_id"]


def _bal(kind, doc):
    with get_session() as s:
        return document_balances(s, kind, [doc])[doc].balance


def test_creditors_and_debtors_lists_use_real_balances():
    p1 = _purchase()
    pay("purchase", p1, SUPPLIER_ID, "out", 20000)
    sale = make_sale()["sale_id"]                                        # 200 kg x 100 = 20,000
    pay("sale", sale, CUSTOMER_ID, "in", 5000)

    cred = D.parties("creditors")
    assert [(r["party_id"], r["balance"]) for r in cred] == [(SUPPLIER_ID, 30000.0)]
    debt = D.parties("debtors")
    assert [(r["party_id"], r["balance"]) for r in debt] == [(CUSTOMER_ID, 15000.0)]
    o = D.overview()
    assert (o["payable"], o["receivable"], o["net"]) == (30000.0, 15000.0, -15000.0)


def test_settled_and_voided_documents_are_not_debts():
    p = _purchase()
    pay("purchase", p, SUPPLIER_ID, "out", 50000)
    assert D.parties("creditors") == []
    s = make_sale()["sale_id"]
    from database.services.sales_service import SaleService
    SaleService.void_sale(s, ADMIN_ID, "mistake")
    assert D.parties("debtors") == []


def test_pay_spreads_over_oldest_documents_first():
    a, b = _purchase(), _purchase()
    r = D.pay(SUPPLIER_ID, "creditors", 70000, method="transfer", note="June part")
    assert [(x["doc_id"], x["amount"]) for x in r["allocations"]] == [(a, 50000.0), (b, 20000.0)]
    assert r["still_owed"] == 30000.0
    assert _bal("purchase", a) == 0 and _bal("purchase", b) == 30000
    with get_session() as s:
        rows = s.query(Payment).all()
        assert all(p.direction == "out" and p.method == "transfer" for p in rows)
        assert all("June part" in p.notes for p in rows)


def test_pay_only_the_ticked_documents():
    a, b = _purchase(), _purchase()
    r = D.pay(SUPPLIER_ID, "creditors", 10000, doc_ids=[b])
    assert [x["doc_id"] for x in r["allocations"]] == [b]
    assert _bal("purchase", a) == 50000 and _bal("purchase", b) == 40000


def test_receiving_from_a_debtor_records_money_in():
    sale = make_sale()["sale_id"]
    D.pay(CUSTOMER_ID, "debtors", 20000)
    assert _bal("sale", sale) == 0
    with get_session() as s:
        assert s.query(Payment).one().direction == "in"


def test_overpaying_or_paying_nothing_is_refused_and_writes_nothing():
    _purchase()
    for amt in (0, -5, 50000.01 + 1):
        with pytest.raises(ValueError):
            D.pay(SUPPLIER_ID, "creditors", amt)
    with pytest.raises(ValueError):
        D.pay(CUSTOMER_ID, "debtors", 10)                                # nothing owed by him
    with get_session() as s:
        assert s.query(Payment).count() == 0


def test_worksheet_shows_both_sides_and_the_trail():
    p = _purchase()
    sale = make_sale(buyer=SUPPLIER_ID)["sale_id"]                       # supplier also buys from us
    D.pay(SUPPLIER_ID, "creditors", 1000)
    w = D.worksheet(SUPPLIER_ID)
    assert w["we_owe"] == 49000 and w["they_owe"] == 20000 and w["net"] == -29000
    assert [d["doc_id"] for d in w["payables"]] == [p] and [d["doc_id"] for d in w["receivables"]] == [sale]
    assert len(w["payments"]) == 1 and w["payments"][0]["amount"] == 1000
    assert [r["party_id"] for r in D.parties("creditors")] == [SUPPLIER_ID]
    assert D.parties("creditors")[0]["opposite"] == 20000


def test_age_and_overdue_are_reported():
    _purchase()
    from database.models.purchase import Purchase
    with get_session() as s:
        s.query(Purchase).update({"completed_at": __import__("datetime").datetime.now() - timedelta(days=45)})
    row = D.parties("creditors")[0]
    assert row["oldest_days"] == 45 and row["overdue"] == 50000


def test_only_admins_may_use_the_debt_center():
    _purchase()
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    for call in (lambda: D.parties("creditors"), lambda: D.pay(SUPPLIER_ID, "creditors", 1),
                 lambda: E.add({"amount": 5, "category": "Rent", "description": "x"})):
        with pytest.raises(PermissionDenied):
            call()


# --------------------------------------------------------------------- expenses
def _exp(**kw):
    d = {"amount": 15000, "category": "Rent", "description": "Shop rent", "date": date.today()}
    d.update(kw)
    return E.add(d)


def test_expense_add_validates_and_numbers():
    eid = _exp(paid_to="Landlord", method="transfer", reference="TRX1")
    row = E.list_expenses()[0]
    assert row["expense_no"] == f"EXP-{eid:05d}" and row["amount"] == 15000 and row["paid_to"] == "Landlord"
    for bad in ({"amount": 0}, {"category": ""}, {"description": " "},
                {"date": date.today() + timedelta(days=30)}):
        with pytest.raises(ValueError):
            _exp(**bad)


def test_expense_void_keeps_the_row_but_drops_it_from_totals():
    eid = _exp()
    _exp(amount=5000, category="Transport & Fuel", description="Diesel")
    with pytest.raises(ValueError):
        E.void(eid, "")
    E.void(eid, "entered twice")
    assert [r["amount"] for r in E.list_expenses()] == [5000]
    assert len(E.list_expenses(include_voided=True)) == 2
    assert E.summary()["total"] == 5000
    with pytest.raises(ValueError):
        E.update(eid, {"amount": 1, "category": "Rent", "description": "x"})


def test_expense_edit_is_audited_with_before_and_after():
    from database.models.audit_log import AuditLog
    eid = _exp()
    E.update(eid, {"amount": 16000, "category": "Rent", "description": "Shop rent"})
    with get_session() as s:
        log = s.query(AuditLog).filter(AuditLog.action == "expense.edit").one()
        assert "16000" in log.details and "15000" in log.details


def test_expense_summary_splits_by_category_and_filters_by_period():
    today = date.today()
    _exp(amount=30000, category="Rent")
    _exp(amount=10000, category="Salaries & Wages", description="Wages")
    _exp(amount=999, category="Rent", description="old", date=today - timedelta(days=400))
    s = E.summary(start=today - timedelta(days=30), end=today)
    assert s["total"] == 40000 and s["count"] == 2
    assert [(c["category"], round(c["share"], 2)) for c in s["by_category"]] == [
        ("Rent", 0.75), ("Salaries & Wages", 0.25)]
    assert s["today"] == 40000 and len(s["trend"]) == 6
    assert E.summary(category="Rent")["total"] == 30000 + 999
    assert len(E.list_expenses(search="Wages")) == 1
    assert "Rent" in E.categories() and "Miscellaneous" in E.categories()
