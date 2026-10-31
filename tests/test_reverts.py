"""Every revert undoes exactly what it should and leaves the books as they were - including reverts of advance use."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.models.audit_log import AuditLog
from database.models.payment import Payment
from database.services.advance_service import AdvanceService
from database.services.document_balance import document_balances
from database.services.drawer_service import DrawerService
from database.services.party_balance_service import PartyBalanceService
from database.services.party_ledger_service import PartyLedgerService
from database.services.payment_center import PaymentCenter
from database.services.payment_service import PaymentService
from database.services.purchase_service import PurchaseService
from database.services.sales_service import SaleService
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, CUSTOMER_ID, FERT_ID, make_sale, pay, stock

P = CUSTOMER_ID            # one party who is both a customer (sales) and a supplier (purchases)


def _purchase(party=P, cost=50):                                # 1000 kg x 50 = 50,000
    return PurchaseService.record({"supplier_party_id": party, "created_by_user_id": CASHIER_A, "items": [
        {"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
         "qty_bags": 10, "total_weight_kg": 1000, "unit_cost": cost}]})["purchase_id"]


def _sale():                                                    # 2 bags / 200 kg x 100 = 20,000
    return make_sale(buyer=P)["sale_id"]


def snapshot():
    """Everything a revert could disturb, as plain comparable data."""
    with get_session() as s:
        from database.models.purchase import Purchase
        from database.models.sale import Sale
        pids = [int(i) for (i,) in s.query(Purchase.id).all()]
        sids = [int(i) for (i,) in s.query(Sale.id).all()]
        pb = {k: (round(v.net_total, 2), round(v.balance, 2), v.voided) for k, v in document_balances(s, "purchase", pids).items()}
        sb = {k: (round(v.net_total, 2), round(v.balance, 2), v.voided) for k, v in document_balances(s, "sale", sids).items()}
    o = PartyBalanceService.compute(P, ADMIN_ID, "")["overall"]
    return {
        "party": {k: round(float(o[k]), 2) for k in ("payable", "receivable", "advance_in", "advance_out")},
        "purchases": pb, "sales": sb,
        "adv_given": PaymentCenter.available_advance(P, "purchase"), "adv_received": PaymentCenter.available_advance(P, "sale"),
        "stock": (stock(CROP_ID), stock(FERT_ID)),
        "drawer": DrawerService.compute(CASHIER_A)["cards"],
    }


def audit(action):
    with get_session() as s:
        return s.query(AuditLog).filter(AuditLog.action == action).count()


def advance_pay_rows(entity_type, entity_id):
    with get_session() as s:
        return [(float(p.amount), p.status) for p in s.query(Payment).filter(
            Payment.entity_type == entity_type, Payment.entity_id == entity_id, Payment.method == "advance").all()]


def healthy():
    """No screen-feeding query may raise, and no balance may go negative."""
    rows = PartyLedgerService.list_activity_details(P)
    assert all(r.get("type") for r in rows)
    snap = snapshot()
    assert all(v >= -0.005 for v in snap["party"].values()) and snap["adv_given"] >= 0 and snap["adv_received"] >= 0
    return snap


# ------------------------------------------------------------------ the ordinary reverts
def test_revert_a_cash_payment_on_a_sale():
    sid = _sale()
    before = snapshot()
    p = pay("sale", sid, P, "in", 5000)
    assert snapshot() != before
    PaymentService.void_payment(p, ADMIN_ID, "wrong")
    assert healthy() == before
    PaymentService.void_payment(p, ADMIN_ID, "again")                       # idempotent
    assert healthy() == before and audit("payment.void") == 1


def test_revert_a_cash_payment_on_a_purchase():
    pid = _purchase()
    before = snapshot()
    p = PaymentService.record_purchase_payment(pid, P, 12000, CASHIER_A, method="cash")
    PaymentService.void_payment(p, ADMIN_ID, "wrong")
    assert healthy() == before


def test_revert_an_unpaid_sale_and_purchase_puts_stock_back():
    before = snapshot()
    sid, pid = _sale(), _purchase()
    SaleService.void_sale(sid, ADMIN_ID, "mistake")
    PurchaseService.void_purchase(pid, ADMIN_ID, "mistake")
    after = healthy()
    assert after["stock"] == before["stock"] and after["party"] == before["party"]
    assert SaleService.void_sale(sid, ADMIN_ID)["already_voided"] and PurchaseService.void_purchase(pid, ADMIN_ID)["already_voided"]


def test_a_paid_sale_or_purchase_cannot_be_cancelled_until_its_cash_payment_is_reverted():
    sid, pid = _sale(), _purchase()
    ps = pay("sale", sid, P, "in", 1000)
    pp = PaymentService.record_purchase_payment(pid, P, 1000, CASHIER_A, method="cash")
    snap = snapshot()
    for fn, i in ((SaleService.void_sale, sid), (PurchaseService.void_purchase, pid)):
        with pytest.raises(ValueError):
            fn(i, ADMIN_ID, "x")
    assert snapshot() == snap                                              # a refusal changes nothing
    PaymentService.void_payment(ps, ADMIN_ID)
    PaymentService.void_payment(pp, ADMIN_ID)
    with pytest.raises(ValueError):                                         # even a voided payment still blocks (existing rule)
        SaleService.void_sale(sid, ADMIN_ID)


def test_revert_advance_recording_and_advance_settlement_payment():
    before = snapshot()
    aid = AdvanceService.record_advance_out(P, 30000, ADMIN_ID)
    pay_id = AdvanceService.settle_advance_payment(aid, 10000, "cash", ADMIN_ID)
    assert snapshot() != before
    PaymentService.void_payment(pay_id, ADMIN_ID, "wrong")
    AdvanceService.void_advance(aid, ADMIN_ID, "wrong")
    assert healthy() == before and audit("advance.void") == 1
    assert AdvanceService.void_advance(aid, ADMIN_ID) == aid                # idempotent


# ------------------------------------------------------------------ purchase paid with advance
def _purchase_with_advance(advance=20000, apply=20000):
    pid = _purchase()
    aid = AdvanceService.record_advance_out(P, advance, ADMIN_ID)
    PaymentCenter.apply_advance(P, "purchase", [(pid, apply)], ADMIN_ID)
    return pid, aid


def test_taking_the_advance_back_off_a_purchase():
    pid, aid = _purchase_with_advance()
    mid = snapshot()
    assert mid["purchases"][pid][1] == 30000 and mid["adv_given"] == 0
    res = AdvanceService.revert_advance_use("purchase", pid, ADMIN_ID, "wrong purchase")
    assert res == {"released": 20000, "advances": [aid]}
    after = healthy()
    assert after["purchases"][pid][1] == 50000                              # owes in full again
    assert after["adv_given"] == 20000 and after["party"]["advance_out"] == 20000     # the advance is available again
    assert advance_pay_rows("purchase", pid) == [(20000.0, "voided")]       # history kept, marked voided
    assert AdvanceService.revert_advance_use("purchase", pid, ADMIN_ID)["released"] == 0     # idempotent
    assert healthy() == after and audit("advance.revert_use") == 1
    # and the advance can be used again, on this purchase
    PaymentCenter.apply_advance(P, "purchase", [(pid, 20000)], ADMIN_ID)
    assert healthy()["purchases"][pid][1] == 30000


def test_cancelling_a_purchase_paid_with_advance_gives_the_advance_back_and_the_stock_goes():
    before = snapshot()
    pid, aid = _purchase_with_advance()
    PurchaseService.void_purchase(pid, ADMIN_ID, "wrong")
    after = healthy()
    assert after["stock"] == before["stock"]
    assert after["adv_given"] == 20000 and after["party"]["payable"] == 0 and after["party"]["advance_out"] == 20000
    assert advance_pay_rows("purchase", pid) == [(20000.0, "voided")]
    PurchaseService.void_purchase(pid, ADMIN_ID)                            # idempotent
    assert healthy() == after
    # the freed advance now pays a new purchase
    p2 = _purchase()
    PaymentCenter.apply_advance(P, "purchase", [(p2, 20000)], ADMIN_ID)
    assert healthy()["purchases"][p2][1] == 30000


def test_voiding_just_the_paid_from_advance_row_frees_the_advance_too():
    pid, aid = _purchase_with_advance()
    (row,) = [p for p in PaymentService.list_voidable_payments(P, "purchase") if p["method"] == "advance"]
    PaymentService.void_payment(row["id"], ADMIN_ID, "wrong")
    after = healthy()
    assert after["purchases"][pid][1] == 50000 and after["adv_given"] == 20000    # used to change nothing at all


def test_one_payment_row_backed_by_two_advances_frees_both():
    pid = _purchase()
    AdvanceService.record_advance_out(P, 10000, ADMIN_ID)
    AdvanceService.record_advance_out(P, 15000, ADMIN_ID)
    PaymentCenter.apply_advance(P, "purchase", [(pid, 25000)], ADMIN_ID)
    assert healthy()["adv_given"] == 0
    (row,) = [p for p in PaymentService.list_voidable_payments(P, "purchase") if p["method"] == "advance"]
    PaymentService.void_payment(row["id"], ADMIN_ID)
    after = healthy()
    assert after["adv_given"] == 25000 and after["purchases"][pid][1] == 50000


def test_only_the_chosen_purchase_is_released():
    a, b = _purchase(), _purchase()
    AdvanceService.record_advance_out(P, 40000, ADMIN_ID)
    PaymentCenter.apply_advance(P, "purchase", [(a, 15000), (b, 25000)], ADMIN_ID)
    AdvanceService.revert_advance_use("purchase", a, ADMIN_ID)
    snap = healthy()
    assert snap["purchases"][a][1] == 50000 and snap["purchases"][b][1] == 25000 and snap["adv_given"] == 15000
    assert [d["reference"] for d in AdvanceService.list_documents_with_advance(P, "purchase")] == [f"PUR-{b}"]


def test_purchase_part_cash_part_advance():
    pid, aid = _purchase_with_advance(apply=20000)
    cash = PaymentService.record_purchase_payment(pid, P, 10000, CASHIER_A, method="cash")
    with pytest.raises(ValueError):
        PurchaseService.void_purchase(pid, ADMIN_ID)                       # cash payment blocks the cancel
    assert healthy()["purchases"][pid][1] == 20000
    AdvanceService.revert_advance_use("purchase", pid, ADMIN_ID)           # taking the advance back is still fine
    assert healthy()["purchases"][pid][1] == 40000
    PaymentService.void_payment(cash, ADMIN_ID)
    assert healthy()["purchases"][pid][1] == 50000


def test_voiding_the_advance_itself_restates_the_purchase_rows():
    pid, aid = _purchase_with_advance(advance=30000, apply=20000)          # partly used
    AdvanceService.void_advance(aid, ADMIN_ID, "wrong", cascade=True)
    after = healthy()
    assert after["purchases"][pid][1] == 50000 and after["adv_given"] == 0 and after["party"]["advance_out"] == 0
    assert advance_pay_rows("purchase", pid) == [(20000.0, "voided")]       # no stale 'paid from advance' row left


def test_a_fully_used_advance_can_be_voided_once_its_use_is_reverted():
    pid, aid = _purchase_with_advance(advance=20000, apply=20000)
    with pytest.raises(ValueError):
        AdvanceService.void_advance(aid, ADMIN_ID, "x")                    # fully used: refused
    AdvanceService.revert_advance_use("purchase", pid, ADMIN_ID)
    AdvanceService.void_advance(aid, ADMIN_ID, "x")
    assert healthy()["adv_given"] == 0


# ------------------------------------------------------------------ sale paid with advance (customer deposit)
def _sale_with_advance(deposit=15000, apply=15000):
    sid = _sale()
    aid = AdvanceService.record_advance_in(P, deposit, ADMIN_ID)
    PaymentCenter.apply_advance(P, "sale", [(sid, apply)], ADMIN_ID)
    return sid, aid


def test_taking_the_deposit_back_off_a_sale():
    sid, aid = _sale_with_advance()
    assert snapshot()["sales"][sid][1] == 5000
    res = AdvanceService.revert_advance_use("sale", sid, ADMIN_ID, "wrong sale")
    assert res == {"released": 15000, "advances": [aid]}
    after = healthy()
    assert after["sales"][sid][1] == 20000 and after["adv_received"] == 15000 and after["party"]["advance_in"] == 15000
    assert advance_pay_rows("sale", sid) == [(15000.0, "voided")]
    assert AdvanceService.revert_advance_use("sale", sid, ADMIN_ID)["released"] == 0


def test_cancelling_a_sale_paid_with_a_deposit_gives_the_deposit_back_and_the_stock_returns():
    before = snapshot()
    sid, aid = _sale_with_advance()
    SaleService.void_sale(sid, ADMIN_ID, "wrong")
    after = healthy()
    assert after["stock"] == before["stock"] and after["adv_received"] == 15000 and after["party"]["receivable"] == 0
    s2 = _sale()
    PaymentCenter.apply_advance(P, "sale", [(s2, 15000)], ADMIN_ID)
    assert healthy()["sales"][s2][1] == 5000


def test_voiding_the_paid_from_deposit_row_frees_the_deposit():
    sid, aid = _sale_with_advance()
    (row,) = [p for p in PaymentService.list_voidable_payments(P, "sale") if p["method"] == "advance"]
    PaymentService.void_payment(row["id"], ADMIN_ID)
    after = healthy()
    assert after["sales"][sid][1] == 20000 and after["adv_received"] == 15000


# ------------------------------------------------------------------ permissions and the screens
def test_only_an_admin_can_take_an_advance_back():
    pid, _aid = _purchase_with_advance()
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    with pytest.raises(PermissionDenied):
        AdvanceService.revert_advance_use("purchase", pid, CASHIER_A)
    set_session(UserSession(user_id=ADMIN_ID, username="admin", role="admin"))
    assert healthy()["purchases"][pid][1] == 30000


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    from PySide2.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("test reason", True)))
    return shown


def _profile(qapp):
    from apps.product_management_app.app_state import state
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView
    state._load_customers_from_db()
    customer = next(c for c in state.customers if c.id == P)
    v = CustomerProfileView(customer=customer, mode="admin")
    v.resize(1200, 700)
    v.show()
    qapp.processEvents()
    return v


def _pick(v, kind):
    v.revert_kind_combo.setCurrentIndex(v.revert_kind_combo.findData(kind))
    v.load_revert_table()
    return v.revert_table


def test_revert_tab_lists_purchases_and_sales_paid_with_advance_and_takes_the_advance_back(qapp, boxes, monkeypatch):
    pid, _a = _purchase_with_advance()
    sid, _b = _sale_with_advance()
    v = _profile(qapp)
    kinds = [v.revert_kind_combo.itemData(i) for i in range(v.revert_kind_combo.count())]
    assert "purchase_adv" in kinds and "sale_adv" in kinds
    t = _pick(v, "purchase_adv")
    assert t.rowCount() == 1 and t.item(0, 1).text() == f"PUR-{pid}" and t.item(0, 3).text() == "20,000.00"
    assert "ADV-" in t.item(0, 4).text() and t.item(0, 6).text() == "30,000.00"
    monkeypatch.setattr(type(v), "_ask_advance_revert", lambda self, *a: "release")
    t.setCurrentCell(0, 0)
    v.btn_revert_selected.click()
    assert snapshot()["purchases"][pid][1] == 50000 and snapshot()["adv_given"] == 20000
    assert _pick(v, "purchase_adv").rowCount() == 0                          # nothing left to revert there
    t = _pick(v, "sale_adv")
    assert t.rowCount() == 1 and t.item(0, 1).text() == f"SAL-{sid}"
    t.setCurrentCell(0, 0)
    v.btn_revert_selected.click()
    assert snapshot()["sales"][sid][1] == 20000 and snapshot()["adv_received"] == 15000
    assert not [m for k, m in boxes if k == "critical"]


def test_revert_tab_can_cancel_the_whole_purchase_paid_with_advance(qapp, boxes, monkeypatch):
    before = snapshot()
    pid, _a = _purchase_with_advance()
    v = _profile(qapp)
    monkeypatch.setattr(type(v), "_ask_advance_revert", lambda self, *a: "cancel")
    t = _pick(v, "purchase_adv")
    t.setCurrentCell(0, 0)
    v.btn_revert_selected.click()
    after = snapshot()
    assert after["stock"] == before["stock"] and after["adv_given"] == 20000 and after["purchases"][pid][2] is True
    assert not [m for k, m in boxes if k == "critical"]


def test_revert_tab_reports_a_refusal_instead_of_failing_silently(qapp, boxes, monkeypatch):
    pid, _a = _purchase_with_advance()
    PaymentService.record_purchase_payment(pid, P, 5000, CASHIER_A, method="cash")
    v = _profile(qapp)
    monkeypatch.setattr(type(v), "_ask_advance_revert", lambda self, *a: "cancel")
    t = _pick(v, "purchase_adv")
    t.setCurrentCell(0, 0)
    snap = snapshot()
    v.btn_revert_selected.click()
    assert any(k == "critical" and "payment already exists" in m for k, m in boxes)
    assert snapshot() == snap


def test_every_revert_kind_in_the_tab_loads_without_error(qapp):
    _purchase_with_advance()
    _sale_with_advance()
    v = _profile(qapp)
    for i in range(v.revert_kind_combo.count()):
        v.revert_kind_combo.setCurrentIndex(i)
        v.load_revert_table()
        assert v.revert_table.columnCount() >= 4
