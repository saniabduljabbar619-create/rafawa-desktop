# tests/test_customer_closings.py
from datetime import datetime, timedelta
import pytest

from database.models.advance import Advance
from database.models.customer_closing import CustomerClosing
from database.models.party import Party
from database.models.payment import Payment
from database.models.product import Product
from database.models.purchase import Purchase
from database.models.purchase_item import PurchaseItem
from database.models.sale import Sale
from database.models.sale_item import SaleItem
from database.services.closing_service import ClosingService
from database.session import get_session
from apps.product_management_app.features.receipts_v2.closing_slip import render_closing_slip
from apps.product_management_app.features.receipts_v2.receipt_models import ShopInfo


def test_customer_closing_computation_and_record(db):
    """Test computing and recording a closing period for a customer."""
    with get_session() as s:
        # Create a party
        p = Party(
            display_name="Alhaji Danladi",
            phone="08031234567",
            party_type="customer",
            is_customer=True,
            is_active=True,
            created_at=datetime.now() - timedelta(days=10),
        )
        s.add(p)
        s.flush()
        pid = p.id

        prod = Product(
            name="White Maize",
            product_type="crops",
            category_id=1,
            unit_label="bag",
            default_cost_rate=20000,
            default_sale_rate=25000,
            is_active=True,
        )
        s.add(prod)
        s.flush()
        prid = prod.id

        # 1. Purchase from customer: 10 bags @ 20,000 = 200,000
        purch = Purchase(
            supplier_party_id=pid,
            status="completed",
            total_amount=200000.0,
            subtotal=200000.0,
            created_by_user_id=1,
            completed_at=datetime.now() - timedelta(days=5),
            created_at=datetime.now() - timedelta(days=5),
        )
        s.add(purch)
        s.flush()
        p_item = PurchaseItem(
            purchase_id=purch.id,
            product_id=prid,
            product_type_snapshot="crops",
            qty_bags=10,
            unit_cost_rate=20000.0,
            line_total=200000.0,
        )
        s.add(p_item)

        # 2. Sale to customer: 5 bags @ 25,000 = 125,000
        sale = Sale(
            buyer_party_id=pid,
            status="completed",
            total_amount=125000.0,
            subtotal=125000.0,
            created_by_user_id=1,
            completed_at=datetime.now() - timedelta(days=4),
            created_at=datetime.now() - timedelta(days=4),
        )
        s.add(sale)
        s.flush()
        s_item = SaleItem(
            sale_id=sale.id,
            product_id=prid,
            product_type_snapshot="crops",
            qty_bags=5,
            unit_sale_rate=25000.0,
            line_total=125000.0,
        )
        s.add(s_item)

        # 3. Advance OUT given to customer: 30,000
        adv_out = Advance(
            party_id=pid,
            direction="given",
            advance_type="money",
            amount=30000.0,
            status="completed",
            created_by_user_id=1,
            created_at=datetime.now() - timedelta(days=3),
        )
        s.add(adv_out)

        # 4. Advance IN received from customer: 10,000
        adv_in = Advance(
            party_id=pid,
            direction="received",
            advance_type="money",
            amount=10000.0,
            status="completed",
            created_by_user_id=1,
            created_at=datetime.now() - timedelta(days=3),
        )
        s.add(adv_in)

        # 5. Payment IN received from customer: 50,000
        pay_in = Payment(
            party_id=pid,
            entity_type="sale",
            entity_id=sale.id,
            direction="in",
            amount=50000.0,
            status="completed",
            created_by_user_id=1,
            created_at=datetime.now() - timedelta(days=2),
        )
        s.add(pay_in)

        # 6. Payment OUT paid to customer: 40,000
        pay_out = Payment(
            party_id=pid,
            entity_type="purchase",
            entity_id=purch.id,
            direction="out",
            amount=40000.0,
            status="completed",
            created_by_user_id=1,
            created_at=datetime.now() - timedelta(days=1),
        )
        s.add(pay_out)
        s.commit()

    # Compute closing
    calc = ClosingService.compute_closing(pid)
    assert calc["opening_balance"] == 0.0
    assert calc["total_purchases"] == 200000.0
    assert calc["total_sales"] == 125000.0
    assert calc["total_advance_out"] == 30000.0
    assert calc["total_advance_in"] == 10000.0
    assert calc["total_payments_in"] == 50000.0
    assert calc["total_payments_out"] == 40000.0

    # Debits = Sales(125k) + AdvOut(30k) + PayOut(40k) = 195,000
    # Credits = Purchases(200k) + AdvIn(10k) + PayIn(50k) = 260,000
    # Net period change = 195,000 - 260,000 = -65,000 (We owe customer 65,000)
    assert calc["debits"] == 195000.0
    assert calc["credits"] == 260000.0
    assert calc["net_period_change"] == -65000.0
    assert calc["closing_balance"] == -65000.0

    # Record closing
    rec = ClosingService.record_closing(pid, user_id=1, notes="End of week closing")
    assert rec["closing_no"].startswith("CLS-")
    assert rec["opening_balance"] == 0.0
    assert rec["closing_balance"] == -65000.0
    assert rec["notes"] == "End of week closing"

    # Multi-period chained closing test
    with get_session() as s:
        # Customer buys more goods: Sale 100,000
        sale2 = Sale(
            buyer_party_id=pid,
            status="completed",
            total_amount=100000.0,
            subtotal=100000.0,
            created_by_user_id=1,
            completed_at=datetime.now(),
            created_at=datetime.now(),
        )
        s.add(sale2)
        s.commit()

    calc2 = ClosingService.compute_closing(pid)
    # Opening balance of Period 2 must match closing balance of Period 1 (-65,000)
    assert calc2["opening_balance"] == -65000.0
    assert calc2["total_sales"] == 100000.0
    assert calc2["total_purchases"] == 0.0
    # Net period change = +100,000
    # New closing balance = -65,000 + 100,000 = +35,000 (Customer now owes us 35,000)
    assert calc2["closing_balance"] == 35000.0

    rec2 = ClosingService.record_closing(pid, user_id=1, notes="Period 2 closing")
    assert rec2["opening_balance"] == -65000.0
    assert rec2["closing_balance"] == 35000.0

    # Test list_closings
    closings = ClosingService.list_closings(party_id=pid)
    assert len(closings) == 2
    assert closings[0]["id"] == rec2["id"]
    assert closings[1]["id"] == rec["id"]


def test_thermal_closing_slip_rendering():
    """Verify Hausa and English 80mm thermal slip rendering."""
    shop = ShopInfo(name="Rafawa Enterprises", address="Kano, Nigeria", phone="08012345678")
    data = {
        "closing_no": "CLS-00042",
        "party_name": "Alhaji Danladi",
        "party_phone": "08031234567",
        "period_start": datetime(2026, 11, 1),
        "period_end": datetime(2026, 11, 11),
        "opening_balance": 10000.0,
        "total_purchases": 200000.0,
        "total_sales": 150000.0,
        "total_advance_out": 20000.0,
        "total_advance_in": 5000.0,
        "total_payments_in": 60000.0,
        "total_payments_out": 30000.0,
        "total_returns": 0.0,
        "net_period_change": -65000.0,
        "closing_balance": -55000.0,
        "details": {
            "purchases": [{"ref": "PUR-01", "date": "2026-11-05", "total_amount": 200000.0}],
            "sales": [{"ref": "SAL-01", "date": "2026-11-06", "total_amount": 150000.0}],
            "advance_out": [{"ref": "ADV-01", "direction": "given", "amount": 20000.0}],
            "advance_in": [],
            "payments_in": [{"ref": "PAY-01", "direction": "in", "amount": 60000.0}],
            "payments_out": [{"ref": "PAY-02", "direction": "out", "amount": 30000.0}],
        },
    }

    # Hausa slip
    ha_slip = render_closing_slip(data, shop=shop, lang="ha", include_items=True)
    assert "TAKARDAR RUFE LISSAFIN ABOKIN CINIKI" in ha_slip
    assert "CLS-00042" in ha_slip
    assert "Alhaji Danladi" in ha_slip
    assert "Bude Balance" in ha_slip
    assert "Siyayya" in ha_slip
    assert "Sayarwa" in ha_slip
    assert "RUFE BALANCE" in ha_slip
    assert "MUNA BIN SHI BASHI" in ha_slip  # negative balance = we owe customer
    assert "Mun gode da kyakkyawar hulda" in ha_slip

    # English slip
    en_slip = render_closing_slip(data, shop=shop, lang="en", include_items=True)
    assert "CUSTOMER CLOSING STATEMENT" in en_slip
    assert "CLS-00042" in en_slip
    assert "Opening Balance" in en_slip
    assert "Purchases (Supplied)" in en_slip
    assert "Sales (Bought)" in en_slip
    assert "CLOSING BALANCE" in en_slip
    assert "WE OWE CUSTOMER" in en_slip
    assert "Thank you for your partnership" in en_slip


def test_auto_close_batch_and_get_details(db):
    """Test auto_close_active_customers_for_today and get_closing_by_id."""
    with get_session() as s:
        p1 = Party(display_name="Trader X", party_type="customer", is_customer=True, is_active=True)
        s.add(p1)
        s.flush()
        pid1 = p1.id

        adv = Advance(party_id=pid1, direction="given", advance_type="money", amount=15000.0,
                      status="completed", created_by_user_id=1, created_at=datetime.now())
        s.add(adv)
        s.commit()

    # Batch auto-close
    closed = ClosingService.auto_close_active_customers_for_today(user_id=1)
    assert len(closed) >= 1
    found = next((c for c in closed if c["party_id"] == pid1), None)
    assert found is not None
    assert found["closing_balance"] == 15000.0
    assert found["total_advance_out"] == 15000.0

    # Retrieve by ID and check details_json
    c_details = ClosingService.get_closing_by_id(found["id"])
    assert c_details is not None
    assert c_details["closing_no"] == found["closing_no"]
    assert len(c_details["details"]["advance_out"]) == 1
    assert c_details["details"]["advance_out"][0]["amount"] == 15000.0

