"""The Activity tab says everything about an advance and about each payment, in words."""
from database.services.advance_service import AdvanceService
from database.services.party_ledger_service import PartyLedgerService
from database.services.payment_center import PaymentCenter
from database.services.payment_service import PaymentService
from tests.conftest import ADMIN_ID, CUSTOMER_ID, SUPPLIER_ID
from tests.test_advance_settlement import _purchase


def _rows(kind, party_id=SUPPLIER_ID):
    return [r for r in PartyLedgerService.list_activity_details(party_id) if r["type"] == kind]


def test_advance_row_tells_the_whole_story():
    pid = _purchase()                                                    # 50,000
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID, ledger="crops", notes="for the next lorry")
    adv = _rows("advance")[0]
    assert "Advance OUT" in adv["detail"] and "Not used yet" in adv["detail"] and "for the next lorry" in adv["detail"]
    assert adv["paid"] == 0 and adv["balance"] == 20000
    PaymentCenter.apply_advance(SUPPLIER_ID, "purchase", [(pid, 12000)], ADMIN_ID)
    adv = _rows("advance")[0]
    assert f"₦12,000.00 set against PUR-{pid}" in adv["detail"] and "Still unused: ₦8,000.00" in adv["detail"]
    assert "Recorded by admin on" in adv["detail"] and "entered by hand in the Advance Pay Center" in adv["detail"]
    assert " by admin" in adv["detail"].split("set against")[1]               # who applied it, and when
    assert "Cash drawer: not counted" in adv["detail"] and "Ledger: crops" in adv["detail"]
    assert adv["paid"] == 12000 and adv["balance"] == 8000
    assert "12,000.00 used" in adv["summary"] and "8,000.00 unused" in adv["summary"]


def test_manual_advance_and_partial_payment_show_detail_rows_and_remaining():
    # Record a 100,000 manual Advance OUT and pay 50,000 of it
    adv_out_id = AdvanceService.record_advance_out(
        SUPPLIER_ID, 100000, ADMIN_ID, ledger="crops", notes="100k advance out"
    )
    PaymentService.record_advance_payment(
        adv_out_id, SUPPLIER_ID, "in", 50000, ADMIN_ID, method="cash", notes="half settled"
    )

    all_rows = PartyLedgerService.list_activity_details(SUPPLIER_ID)
    assert len(all_rows) == 2
    adv_row, pay_row = all_rows[0], all_rows[1]

    # Parent Advance row comes first, followed immediately by its child payment row
    assert adv_row["type"] == "advance" and adv_row["reference"] == f"ADV-{adv_out_id}"
    assert abs(adv_row["total"]) == 100000 and adv_row["paid"] == 50000 and adv_row["balance"] == 50000
    assert "50,000.00" in adv_row["detail"] and "Remaining: ₦50,000.00" in adv_row["detail"]

    assert pay_row["type"] == "payment" and pay_row["reference"] == f"ADV-{adv_out_id}"
    assert pay_row["entity_type"] == "advance" and pay_row["entity_id"] == adv_out_id
    assert pay_row["total"] == 100000 and pay_row["paid"] == 50000 and pay_row["balance"] == 50000
    assert pay_row["balance_before"] == 100000 and pay_row["balance_after"] == 50000
    assert "remaining ₦100,000.00 -> ₦50,000.00" in pay_row["summary"]
    assert "Remaining: ₦50,000.00" in pay_row["detail"]

    # Record a 100,000 manual Advance IN for customer and pay 50,000 of it
    adv_in_id = AdvanceService.record_advance_in(
        CUSTOMER_ID, 100000, ADMIN_ID, ledger="fertilizer", notes="100k advance in"
    )
    PaymentService.record_advance_payment(
        adv_in_id, CUSTOMER_ID, "out", 50000, ADMIN_ID, method="transfer", notes="refunded half"
    )
    cust_rows = PartyLedgerService.list_activity_details(CUSTOMER_ID)
    assert len(cust_rows) == 2
    c_adv, c_pay = cust_rows[0], cust_rows[1]
    assert c_adv["type"] == "advance" and c_adv["reference"] == f"ADV-{adv_in_id}"
    assert c_adv["total"] == 100000 and c_adv["paid"] == 50000 and c_adv["balance"] == 50000
    assert c_pay["type"] == "payment" and c_pay["reference"] == f"ADV-{adv_in_id}"
    assert c_pay["total"] == 100000 and c_pay["paid"] == 50000 and c_pay["balance"] == 50000
    assert c_pay["balance_before"] == 100000 and c_pay["balance_after"] == 50000


def test_payment_row_says_who_paid_whom_for_what_and_what_it_left():
    pid = _purchase()
    PaymentService.record_purchase_payment(pid, SUPPLIER_ID, 10000, ADMIN_ID, method="cash", notes="part payment")
    pay = _rows("payment")[0]
    assert f"we paid the supplier ₦10,000.00 for PUR-{pid}" in pay["detail"]
    assert "Maize" in pay["detail"] and "₦50,000.00" in pay["detail"]
    assert "Owed before this payment: ₦50,000.00" in pay["detail"] and "after: ₦40,000.00" in pay["detail"]
    assert "part payment" in pay["detail"] and "Recorded by" in pay["detail"]
    assert "owed ₦50,000.00 -> ₦40,000.00" in pay["summary"]


def test_payment_from_an_advance_names_the_advance():
    pid = _purchase()
    AdvanceService.record_advance_out(SUPPLIER_ID, 20000, ADMIN_ID)
    PaymentCenter.apply_advance(SUPPLIER_ID, "purchase", [(pid, 15000)], ADMIN_ID)
    pay = [r for r in _rows("payment")][0]
    assert "From advance" in pay["detail"] and "ADV-" in pay["detail"] and "₦15,000.00" in pay["detail"]
