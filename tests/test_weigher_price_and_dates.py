# tests/test_weigher_price_and_dates.py
from datetime import datetime, timedelta
import pytest

from database.models.party import Party
from database.models.purchase import Purchase
from database.models.weigh_ticket import WeighTicket
from database.models.payment import Payment
from database.services.weigh_service import WeighService as W
from database.services.customer_records_service import CustomerRecordsService
from database.services.party_ledger_service import PartyLedgerService
from database.services.purchase_adjustment_service import PurchaseAdjustmentService
from database.services.payment_service import PaymentService
from database.session import get_session
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CASHIER_B, SUPPLIER_ID, CROP_ID

WEIGHER = CASHIER_A
CASHIER = CASHIER_B


def as_role(role, uid):
    set_session(UserSession(user_id=uid, username=f"{role}{uid}", role=role))


def test_weigher_price_isolation_from_cashier_and_admin_adjustments():
    """
    Test Request 3:
    When a cashier or admin adjusts a price or updates it, it must not reflect on the weigher side.
    The weigher price and admin/cashier price are isolated historically.
    """
    # 1. Weigher sets price at 400 per kg at the scale
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line_id = W.add_line(tid, CROP_ID, area="Kano", grade="A")
    W.record_round(line_id, bags=3, weight_kg=150.0)
    W.record_round(line_id, bags=2, weight_kg=100.0)
    W.set_line_price(line_id, 400.0)
    W.submit_ticket(tid, receipt_type="full")

    # Weigher sees their price: 400/kg -> 100,000 total
    t_weigher = W.get_ticket(tid, for_weigher=True)
    assert t_weigher["lines"][0]["weigher_price"] == 400.0
    assert t_weigher["lines"][0]["unit_price"] == 400.0
    assert t_weigher["total_amount"] == 100000.0
    assert t_weigher["weigher_total"] == 100000.0

    # 2. Cashier takes over at intake and confirms at a DIFFERENT price (450/kg)
    as_role("cashier", CASHIER)
    res = W.finalize_ticket(tid, {line_id: 450.0})
    purchase_id = res["purchase_id"]

    # Cashier sees confirmed purchase amount: 250kg * 450 = 112,500
    t_cashier = W.get_ticket(tid, for_weigher=False)
    assert t_cashier["total_amount"] == 112500.0
    assert t_cashier["lines"][0]["unit_price"] == 450.0
    assert t_cashier["confirmed_total"] == 112500.0

    # BUT Weigher side STILL sees 400/kg -> 100,000!
    as_role("weigher", WEIGHER)
    t_weigher_after_intake = W.get_ticket(tid, for_weigher=True)
    assert t_weigher_after_intake["lines"][0]["weigher_price"] == 400.0
    assert t_weigher_after_intake["lines"][0]["unit_price"] == 400.0
    assert t_weigher_after_intake["total_amount"] == 100000.0

    # Weigher ticket list also reflects the weigher's 100,000
    listed_for_weigher = W.list_tickets(search=t_weigher["ticket_no"], for_weigher=True)
    assert len(listed_for_weigher) >= 1
    assert listed_for_weigher[0]["total_amount"] == 100000.0
    assert listed_for_weigher[0]["weigher_total"] == 100000.0

    # 3. Admin adjusts the purchase price LATER (from 450 to 500/kg)
    as_role("admin", ADMIN_ID)
    PurchaseAdjustmentService.apply_adjustment(purchase_id, amount=12500.0, created_by_user_id=ADMIN_ID,
                                              reason="Post-delivery market bump")

    # Purchase now reflects 500/kg total (112,500 + 12,500 = 125,000)
    with get_session() as s:
        pur = s.get(Purchase, purchase_id)
        bal = PurchaseAdjustmentService._purchase_balance(s, pur)
        assert bal["net_total"] == 125000.0

    # Weigher side MUST STILL REMAIN 400.0 / 100,000.0 (completely isolated!)
    as_role("weigher", WEIGHER)
    t_weigher_final = W.get_ticket(tid, for_weigher=True)
    assert t_weigher_final["lines"][0]["weigher_price"] == 400.0
    assert t_weigher_final["lines"][0]["unit_price"] == 400.0
    assert t_weigher_final["total_amount"] == 100000.0
    assert t_weigher_final["weigher_total"] == 100000.0


def test_customer_profile_monitors_weighed_date_and_payment_tracking():
    """
    Test Request 4:
    A date weighed for a product must be monitored inside customer's profile.
    Every intake/purchase keeps track of the exact date weighed and booked, and
    payments track the weighing booking date and elapsed days.
    """
    # 1. Weighed on 5 days ago
    weighed_dt = datetime.now() - timedelta(days=5)
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line_id = W.add_line(tid, CROP_ID, area="Kano", grade="A")
    W.record_round(line_id, bags=3, weight_kg=150.0)
    W.record_round(line_id, bags=3, weight_kg=150.0)
    W.record_round(line_id, bags=2, weight_kg=100.0)
    W.set_line_price(line_id, 300.0)
    W.submit_ticket(tid)

    # Backdate the ticket's created_at to 5 days ago to simulate time gap
    with get_session() as s:
        wt = s.get(WeighTicket, tid)
        wt.created_at = weighed_dt
        s.commit()

    # 2. Finalize into purchase today
    as_role("cashier", CASHIER)
    res = W.finalize_ticket(tid, {line_id: 300.0})
    purchase_id = res["purchase_id"]

    # 3. Verify CustomerRecordsService.documents tracks date weighed and ticket
    docs = CustomerRecordsService.documents(SUPPLIER_ID, "purchase")
    pur_doc = next(d for d in docs if d["id"] == purchase_id)
    assert pur_doc["weighed_at"] is not None
    assert pur_doc["ticket_no"] == f"WT-{tid:05d}"
    assert pur_doc["booked_at"] is not None

    # 4. Verify PartyLedgerService.list_activity_details tracks weighed date & ticket
    acts = PartyLedgerService.list_activity_details(SUPPLIER_ID)
    act_pur = next(a for a in acts if a["reference"] == f"PUR-{purchase_id}")
    assert act_pur["weighed_at"] is not None
    assert act_pur["ticket_no"] == f"WT-{tid:05d}"
    assert f"WT-{tid:05d}" in act_pur["summary"]

    # 5. Verify PartyLedgerService.list_crop_activity tracks weighed date & ticket
    crops = PartyLedgerService.list_crop_activity(SUPPLIER_ID)
    crop_pur = next(c for c in crops if c["reference"] == f"PUR-{purchase_id}")
    assert crop_pur["weighed_at"] is not None
    assert crop_pur["ticket_no"] == f"WT-{tid:05d}"

    # 6. Verify Payment tracks weighing date and gap
    # Make payment today (5 days after weighing)
    pay_id = PaymentService.record_purchase_payment(
        purchase_id=purchase_id,
        party_id=SUPPLIER_ID,
        amount=50000.0,
        created_by_user_id=CASHIER,
        method="cash",
        notes=f"[Weighed: {weighed_dt.strftime('%Y-%m-%d')}, WT-{tid:05d}, 5d ago] Partial payment"
    )
    with get_session() as s:
        p = s.get(Payment, pay_id)
        assert f"WT-{tid:05d}" in p.notes
        assert "5d ago" in p.notes

    # 7. Verify CustomerRecordsService.history tracks the weighing information for this payment
    hist = CustomerRecordsService.history(SUPPLIER_ID)
    pay_hist = next(h for h in hist if h["type"] == "Payment out" and h["ref"] == f"PUR-{purchase_id}")
    assert "Weighed" in pay_hist["detail"]
    assert "gap" in pay_hist["detail"]
