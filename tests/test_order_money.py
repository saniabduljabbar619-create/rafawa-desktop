"""Orders: money given / received, per-order expenses with repayments, and the partner statement."""
import pytest

from database.models.order_payment import OrderPayment
from database.services.company_service import CompanyService
from database.services.order_money_service import OrderMoneyService as M
from database.services.order_sheet_service import OrderSheetService as O
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID


def _lines(price=110, kg=500):
    return [{"product_id": CROP_ID, "name": "Maize", "type": "crops", "kg": kg, "bags": 5, "price": price}]


def _both():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    return c, p, O.create("sales", c, _lines()), O.create("purchase", p, _lines(100, 400))


def _cashier():
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))


def test_money_given_and_received_is_recorded_on_the_account_or_an_order():
    c, p, so, po = _both()
    a = M.add_money("sales", c, "in", 200000, note="part of the next load")           # on the account
    b = M.add_money("sales", c, "out", 50000, order_id=so, method="cash", note="lent for fuel")
    rows = M.list_money("sales", partner_id=c)
    assert [(r["direction"], r["amount"], r["order_no"]) for r in rows] == [("in", 200000, ""), ("out", 50000, f"SO-{so}")]
    assert [r["id"] for r in M.list_money("sales", order_id=so)] == [b]
    assert M.list_money("purchase", partner_id=p) == []                           # the other side is separate
    with pytest.raises(ValueError):
        M.add_money("sales", c, "in", 0)
    with pytest.raises(ValueError):
        M.add_money("sales", c, "sideways", 10)
    with pytest.raises(ValueError):
        M.add_money("sales", p, "in", 10, order_id=so)                              # that order is not this partner's
    assert a


def test_applying_money_to_an_order_makes_a_real_payment_and_locks_the_entry():
    c, p, so, po = _both()
    got = M.add_money("sales", c, "in", 30000)
    gave = M.add_money("sales", c, "out", 5000)
    with pytest.raises(ValueError):
        M.apply_to_order(gave, so)                                                  # a Sales Order is paid by money they gave us
    pay = M.apply_to_order(got, so)
    with get_session() as s:
        assert float(s.get(OrderPayment, pay).amount) == 30000
    assert [r["status"] for r in M.list_money("sales", partner_id=c)] == ["applied", "completed"]
    with pytest.raises(ValueError):
        M.void_money(got, "oops")                                                   # void the order payment first
    out = M.add_money("purchase", p, "out", 12000)
    M.apply_to_order(out, po)                                                       # a Purchase Order: money we gave them
    with pytest.raises(ValueError):
        M.apply_to_order(M.add_money("purchase", p, "in", 1), po)


def test_expenses_are_recoverable_by_default_on_purchase_orders_and_our_cost_on_sales():
    c, p, so, po = _both()
    e1 = M.add_expense("purchase", po, "Driver", 20000, "driver paid for the supplier")
    e2 = M.add_expense("purchase", po, "Fuel", 15000, "diesel", paid_to="Total")
    e3 = M.add_expense("sales", so, "Food", 4000)
    e4 = M.add_expense("sales", so, "Fuel", 9000, recoverable=True)
    rows = {r["id"]: r for r in M.list_expenses("purchase", order_id=po)}
    assert rows[e1]["recoverable"] and rows[e1]["state"] == "unrecovered" and rows[e1]["no"].startswith("OEX-")
    sales = {r["id"]: r for r in M.list_expenses("sales", order_id=so)}
    assert not sales[e3]["recoverable"] and sales[e3]["state"] == "our cost" and sales[e4]["recoverable"]
    assert M.list_expenses("purchase", partner_id=p)[1]["id"] == e2
    with pytest.raises(ValueError):
        M.add_expense("purchase", po, "", 10)
    with pytest.raises(ValueError):
        M.add_expense("purchase", po, "Fuel", -5)


def test_a_repayment_is_spread_oldest_first_and_never_exceeds_what_is_owed():
    c, p, so, po = _both()
    e1 = M.add_expense("purchase", po, "Driver", 20000)
    e2 = M.add_expense("purchase", po, "Fuel", 15000)
    M.record_recovery(25000, kind="purchase", order_id=po, method="cash")
    rows = {r["id"]: r for r in M.list_expenses("purchase", order_id=po)}
    assert (rows[e1]["recovered"], rows[e1]["state"]) == (20000, "recovered")
    assert (rows[e2]["recovered"], rows[e2]["left"], rows[e2]["state"]) == (5000, 10000, "part recovered")
    with pytest.raises(ValueError):
        M.record_recovery(10001, kind="purchase", order_id=po)                      # only 10,000 is still owed
    M.record_recovery(10000, expense_id=e2)
    assert all(r["state"] == "recovered" for r in M.list_expenses("purchase", order_id=po))
    assert len(M.list_recoveries("purchase", order_id=po)) == 3


def test_void_rules_and_permissions():
    c, p, so, po = _both()
    m = M.add_money("purchase", p, "out", 1000)
    e = M.add_expense("purchase", po, "Driver", 20000)
    M.record_recovery(5000, expense_id=e)
    with pytest.raises(ValueError):
        M.void_expense(e, "wrong")                                                  # part of it was paid back
    rid = M.list_recoveries("purchase", order_id=po)[0]["id"]
    M.void_recovery(rid, "typo")
    M.void_expense(e, "wrong driver")
    assert M.list_expenses("purchase", order_id=po)[0]["state"] == "voided"
    M.void_money(m, "entered twice")
    assert M.list_money("purchase", partner_id=p)[0]["status"] == "voided"
    _cashier()
    assert M.add_money("purchase", p, "in", 500) and M.add_expense("purchase", po, "Food", 300)   # cashiers add
    with pytest.raises(PermissionDenied):
        M.void_money(m)                                                             # only an admin voids
    with pytest.raises(PermissionDenied):
        M.void_expense(e)


def test_the_partner_statement_puts_the_four_figures_side_by_side():
    c, p, so, po = _both()
    st = M.statement("purchase", p)
    assert st == {"orders": 0, "orders_owed": 0, "money_in": 0, "money_out": 0, "money_net": 0,
                  "to_recover": 0, "our_cost": 0, "net": 0}                         # nothing confirmed or priced yet
    M.add_money("purchase", p, "in", 50000)                                         # they handed us money
    M.add_money("purchase", p, "out", 10000)                                        # we gave them money
    M.add_expense("purchase", po, "Driver", 20000)                                  # to recover from them
    M.add_expense("purchase", po, "Food", 3000, recoverable=False)                  # our own cost
    M.record_recovery(5000, kind="purchase", order_id=po)
    st = M.statement("purchase", p)
    assert (st["money_in"], st["money_out"], st["money_net"]) == (50000, 10000, 40000)
    assert (st["to_recover"], st["our_cost"]) == (15000, 3000)
    assert st["net"] == -50000 + 10000 + 15000                                      # negative: we owe them overall
