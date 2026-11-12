# tests/test_orders_new_ux.py
from datetime import date
import pytest

from database.models.company import Company
from database.models.purchase_order_supplier import PurchaseOrderSupplier
from database.models.order_money import OrderMoney
from database.models.order_payment import OrderPayment
from database.services.company_service import CompanyService
from database.services.order_money_service import OrderMoneyService
from database.services.order_profile_service import OrderProfileService
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from database.session import get_session
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CROP_ID, FERT_ID


@pytest.fixture(autouse=True)
def admin_session():
    set_session(UserSession(user_id=ADMIN_ID, username="admin_tester", role="admin"))


def test_order_profile_advances_and_new_activities():
    # 1. Create a company partner
    comp_id = CompanyService.create("Dangote Flour Mills", ADMIN_ID, phone="08011223344", address="Kano")
    assert comp_id > 0

    partner = OrderProfileService.resolve_partner(comp_id, kind="sales")
    assert partner["name"] == "Dangote Flour Mills"
    assert partner["company_id"] == comp_id

    # 2. Record Advance IN for Sale Orders
    adv_id = OrderProfileService.record_advance(
        kind="sales",
        partner_id=comp_id,
        direction="in",
        amount=500000.0,
        method="transfer",
        note="Contract deposit for white maize",
        entry_date=date.today(),
    )
    assert adv_id > 0

    # Verify advance is recorded but NOT used (stays 'completed' / available)
    bals = OrderProfileService.get_advance_balances(partner["company_id"], partner["supplier_id"])
    assert bals["advance_in"] == 500000.0
    assert bals["advance_out"] == 0.0

    with get_session() as s:
        adv_row = s.get(OrderMoney, adv_id)
        assert adv_row.status == "completed"
        assert adv_row.order_id is None

    # 3. Create a Sale Order Activity (single-row activity with Quality, Bags, Weight, Price)
    order_id = OrderProfileService.create_activity(
        kind="sales",
        partner_id=comp_id,
        product_id=CROP_ID,
        quality="Grade 1 Premium Clean",
        bags=10,
        weight_kg=500.0,
        price=850.0,
        notes="Truck delivery to Kano Mill",
        dispatch_now=True,
    )
    assert order_id > 0

    # 4. Verify order list shows single-row tracking data
    orders = OrderProfileService.list_partner_orders(partner["company_id"], partner["supplier_id"])
    assert len(orders) >= 1
    target_so = next(o for o in orders if o["id"] == order_id and o["kind"] == "sales")
    assert target_so["quality"] == "Grade 1 Premium Clean"
    assert target_so["initial_qty_bags"] == 10
    assert target_so["initial_weight_kg"] == 500.0
    assert target_so["initial_price"] == 850.0
    assert target_so["status"] == "in_transit"
    assert target_so["final_total_amount"] is None  # not finalized yet

    # Verify that advance was NOT touched yet (requirement #3)
    bals_after_create = OrderProfileService.get_advance_balances(partner["company_id"], partner["supplier_id"])
    assert bals_after_create["advance_in"] == 500000.0

    # 5. Finalize Order on a single row with Company Price, Company Weight, Accepted/Rejected breakdown
    # And test requirement #9: Use Advance during finalization!
    # Company verified: 480 kg @ ₦840/kg = ₦403,200.00
    fin_res = OrderProfileService.finalize_order(
        kind="sales",
        order_id=order_id,
        company_price=840.0,
        company_weight_kg=480.0,
        company_qty_bags=10.0,
        accepted_qty=480.0,
        rejected_qty=0.0,
        use_advance=True,
        advance_amount=403200.0,
    )
    assert fin_res["ok"] is True
    assert fin_res["final_total_amount"] == 403200.0
    assert fin_res["applied_payment_id"] is not None

    # Check that the advance was used to pay ₦403,200 against this order,
    # and the remaining ₦96,800 remains active as available advance in the profile!
    bals_after_fin = OrderProfileService.get_advance_balances(partner["company_id"], partner["supplier_id"])
    assert bals_after_fin["advance_in"] == 500000.0 - 403200.0

    with get_session() as s:
        adv_row = s.get(OrderMoney, adv_id)
        assert adv_row.status == "applied"
        assert float(adv_row.amount) == 403200.0
        assert adv_row.order_id == order_id

    # Verify updated order reflects finalization and full settlement
    orders_updated = OrderProfileService.list_partner_orders(partner["company_id"], partner["supplier_id"])
    finalized_so = next(o for o in orders_updated if o["id"] == order_id)
    assert finalized_so["status"] == "confirmed"
    assert finalized_so["final_total_amount"] == 403200.0
    assert finalized_so["paid_amount"] == 403200.0
    assert finalized_so["balance"] == 0.0


def test_advance_split_and_purchase_order_flow():
    # 1. Create a supplier partner
    supp_id = PurchaseOrderSupplierService.create("Golden Agro Supplies", ADMIN_ID, phone="08099887766")
    partner = OrderProfileService.resolve_partner(supp_id, kind="purchase")

    # 2. Record an advance OUT (we gave supplier ₦1,000,000)
    adv_id = OrderProfileService.record_advance(
        kind="purchase",
        partner_id=supp_id,
        direction="out",
        amount=1000000.0,
        method="transfer",
        note="Urea advance deposit",
    )
    assert adv_id > 0

    # 3. Create a Purchase Order for Fertilizer: 20 bags @ ₦35,000 = ₦700,000
    po_id = OrderProfileService.create_activity(
        kind="purchase",
        partner_id=supp_id,
        product_id=FERT_ID,
        quality="NPK 15-15-15 50kg",
        bags=20,
        weight_kg=0.0,
        price=35000.0,
        dispatch_now=True,
    )
    assert po_id > 0

    # 4. Finalize Purchase Order: Received 20 bags @ ₦35,000 = ₦700,000
    # Apply only ₦700,000 of the ₦1,000,000 advance. The remaining ₦300,000 should stay available!
    fin_res = OrderProfileService.finalize_order(
        kind="purchase",
        order_id=po_id,
        company_price=35000.0,
        company_qty_bags=20,
        accepted_qty=20,
        rejected_qty=0,
        use_advance=True,
        advance_amount=700000.0,
    )
    assert fin_res["ok"] is True
    assert fin_res["final_total_amount"] == 700000.0

    # Verify advance splitting:
    # ₦700,000 applied to PO, and ₦300,000 remainder exists as active 'completed' advance
    bals = OrderProfileService.get_advance_balances(partner["company_id"], partner["supplier_id"])
    assert bals["advance_out"] == 300000.0

    with get_session() as s:
        old_adv = s.get(OrderMoney, adv_id)
        assert old_adv.status == "applied"
        assert float(old_adv.amount) == 700000.0
        assert old_adv.order_id == po_id

        # Remainder row
        rem_adv = s.query(OrderMoney).filter(
            OrderMoney.partner_id == supp_id,
            OrderMoney.direction == "out",
            OrderMoney.status == "completed",
        ).first()
        assert rem_adv is not None
        assert float(rem_adv.amount) == 300000.0
