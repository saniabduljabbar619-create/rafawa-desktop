"""Giving an advance to a party we owe (or taking a deposit from one who owes us) and applying it."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.services.advance_service import AdvanceService
from database.services.party_balance_service import PartyBalanceService
from database.services.payment_center import PaymentCenter
from database.services.payment_service import PaymentService
from database.services.purchase_service import PurchaseService
from database.session import get_session
from tests.conftest import ADMIN_ID, CASHIER_A, CUSTOMER_ID, SUPPLIER_ID, make_sale, pay


def _purchase(party=SUPPLIER_ID, cost=50):                 # 1000 kg x 50 = 50,000
    return PurchaseService.record({"supplier_party_id": party, "created_by_user_id": CASHIER_A,
        "items": [{"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops",
                   "qty_bags": 10, "total_weight_kg": 1000, "unit_cost": cost}]})["purchase_id"]


def _bal(party=SUPPLIER_ID):
    o = PartyBalanceService.compute(party, ADMIN_ID, "")["overall"]
    return o["payable"], o["advance_out"], o["receivable"], o["advance_in"]


def _counts():
    with get_session() as s:
        return s.query(Advance).count(), s.query(AdvanceAllocation).count()


def test_advance_out_reduces_payables_and_leaves_no_advance_balance():
    _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
    assert _bal()[:2] == (50000, 20000)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (30000, 0)
    assert _counts() == (1, 1)                              # nothing duplicated


def test_partly_paid_purchase():
    pid = _purchase()
    PaymentService.record_purchase_payment(pid, SUPPLIER_ID, 10000, ADMIN_ID, method="cash")
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (20000, 0)


def test_second_advance_is_applied_in_full():
    """Used to leave 10,000 behind as Advance OUT: the first settlement was subtracted twice."""
    _purchase()
    for _ in range(2):
        AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
        PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (10000, 0)
    assert _counts() == (2, 2)


def test_advance_larger_than_debt_keeps_only_the_excess():
    _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 70000, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (0, 20000)


def test_settlement_twice_does_not_apply_twice():
    _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (30000, 0) and _counts() == (1, 1)


def test_voided_purchase_does_not_swallow_the_advance():
    old = _purchase(cost=10)
    PurchaseService.void_purchase(old, ADMIN_ID)
    _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    assert _bal()[:2] == (30000, 0)


def test_customer_deposit_settles_sales_and_second_deposit_too():
    make_sale()                                              # 20,000
    pay("sale", 1, CUSTOMER_ID, "in", 5000)
    AdvanceService.record_advance_in(CUSTOMER_ID, 6000, ADMIN_ID)
    PaymentCenter.handle_customer_advance_settlement(CUSTOMER_ID, ADMIN_ID)
    assert _bal(CUSTOMER_ID)[2:] == (9000, 0)
    AdvanceService.record_advance_in(CUSTOMER_ID, 6000, ADMIN_ID)
    PaymentCenter.handle_customer_advance_settlement(CUSTOMER_ID, ADMIN_ID)
    assert _bal(CUSTOMER_ID)[2:] == (3000, 0)


def test_advances_list_shows_applied_and_remaining():
    _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 70000, ADMIN_ID)
    PaymentCenter.handle_supplier_advance_settlement(SUPPLIER_ID, ADMIN_ID)
    row = AdvanceService.list_all_party_advances(SUPPLIER_ID)[0]
    assert (row["amount"], row["applied"], row["remaining"]) == (70000, 50000, 20000)


# ---------------------------------------------------------------- the dialog
@pytest.fixture
def dialog(qapp, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs.advance_pay_dialog import AdvancePayDialog
    from database.models.party import Party
    shown = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    with get_session() as s:
        party = s.get(Party, SUPPLIER_ID)
    dlg = AdvancePayDialog(party=party, user_id=ADMIN_ID)
    dlg.direction.setCurrentIndex(1)
    dlg.amount.setText("20000")
    return dlg, shown


def test_dialog_yes_applies_advance_and_locks_the_form(dialog):
    dlg, shown = dialog
    _purchase()
    dlg.btn_record.click()
    assert any("outstanding" in m for _k, m in shown)
    assert _bal()[:2] == (30000, 0) and _counts() == (1, 1)
    assert not dlg.btn_record.isEnabled() and not dlg.amount.isEnabled()


def test_a_failure_while_applying_cannot_lead_to_a_duplicate_advance(dialog, monkeypatch):
    dlg, shown = dialog
    _purchase()
    monkeypatch.setattr(dlg, "_offer_settlement", lambda _i: (_ for _ in ()).throw(RuntimeError("boom")))
    dlg.btn_record.click()
    assert any(k == "warning" and "NOT recorded twice" in m for k, m in shown)
    assert not dlg.btn_record.isEnabled()                     # cannot be submitted again
    dlg.btn_record.click()                                    # (a disabled button ignores clicks)
    assert _counts()[0] == 1
    assert _bal()[1] == 20000                                 # advance kept, still offered later
