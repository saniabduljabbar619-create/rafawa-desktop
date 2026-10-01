from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment
from database.models.purchase_price_adjustment import PurchasePriceAdjustment
from database.models.sale_discount import SaleDiscount
from database.services.document_balance import document_balances, payment_status
from database.services.purchase_service import PurchaseService
from database.services.return_service import ReturnService
from database.services.sales_service import SaleService
from database.session import get_session
from tests.conftest import ADMIN_ID, CASHIER_A, CASHIER_B, CUSTOMER_ID, SUPPLIER_ID, make_sale, pay


def _bal(kind, doc_id, **kw):
    with get_session() as s:
        return document_balances(s, kind, [doc_id], **kw)[doc_id]


def test_unpaid_partial_paid():
    res = make_sale()                                          # 20000
    assert _bal("sale", res["sale_id"]).status == "unpaid"
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 5000)
    b = _bal("sale", res["sale_id"])
    assert (b.net_total, b.cash_paid, b.balance, b.status) == (20000, 5000, 15000, "partial")
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 15000)
    assert _bal("sale", res["sale_id"]).status == "paid"


def test_returns_discounts_and_advance_allocations_reduce_what_is_owed():
    res = make_sale()
    ReturnService.create_return(document_type="sale", document_id=res["sale_id"],
                                item_id=res["sale_item_ids"][0], qty_kg=100, qty_bags=1,
                                reason="x", created_by_user_id=CASHIER_A)        # -10000
    with get_session() as s:
        s.add(SaleDiscount(sale_id=res["sale_id"], amount=1000, created_by_user_id=ADMIN_ID))
        s.add(AdvanceAllocation(advance_id=1, entity_type="sale", entity_id=res["sale_id"],
                                amount=2000, created_by_user_id=ADMIN_ID))
    b = _bal("sale", res["sale_id"])
    assert (b.returned, b.discount, b.net_total, b.advance_applied, b.balance) == (10000, 1000, 9000, 2000, 7000)


def test_refunds_and_advance_method_payments_are_not_counted_as_paid():
    res = make_sale()
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 4000)
    pay("sale", res["sale_id"], CUSTOMER_ID, "out", 999, method="refund")          # wrong direction
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 777, method="advance")          # advance-funded
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 555, notes="Advance settlement")
    assert _bal("sale", res["sale_id"]).cash_paid == 4000


def test_payment_with_no_method_or_notes_still_counts():
    res = make_sale()
    with get_session() as s:
        s.add(Payment(entity_type="sale", entity_id=res["sale_id"], party_id=CUSTOMER_ID, direction="in",
                      amount=1234, method=None, notes=None, created_by_user_id=CASHIER_A))
    assert _bal("sale", res["sale_id"]).cash_paid == 1234


def test_voided_payments_do_not_count_and_voided_docs_owe_nothing():
    res = make_sale()
    pid = pay("sale", res["sale_id"], CUSTOMER_ID, "in", 3000)
    from database.services.payment_service import PaymentService
    PaymentService.void_payment(pid, ADMIN_ID)
    assert _bal("sale", res["sale_id"]).cash_paid == 0
    other = make_sale()                                        # a sale never paid against can be voided
    SaleService.void_sale(other["sale_id"], ADMIN_ID)
    b = _bal("sale", other["sale_id"])
    assert (b.status, b.net_total, b.balance) == ("voided", 0, 0)


def test_purchase_adjustments_add_to_what_is_owed_and_cashier_filter_works():
    res = PurchaseService.record({"supplier_party_id": SUPPLIER_ID, "created_by_user_id": CASHIER_A,
                                  "items": [{"sku": "S", "product_name": "Maize", "product_type": "crops",
                                             "qty_bags": 1, "total_weight_kg": 100, "unit_cost": 100}]})
    with get_session() as s:
        s.add(PurchasePriceAdjustment(purchase_id=res["purchase_id"], amount=500, created_by_user_id=ADMIN_ID))
    pay("purchase", res["purchase_id"], SUPPLIER_ID, "out", 4000, user=CASHIER_A)
    pay("purchase", res["purchase_id"], SUPPLIER_ID, "out", 1000, user=CASHIER_B)
    b = _bal("purchase", res["purchase_id"])
    assert (b.net_total, b.cash_paid, b.balance) == (10500, 5000, 5500)
    assert _bal("purchase", res["purchase_id"], payments_by_user_id=CASHIER_B).cash_paid == 1000


def test_status_tolerances_and_empty_input():
    assert payment_status(100, 99.995) == "paid"
    assert payment_status(100, 50) == "partial"
    assert payment_status(100, 0.001) == "unpaid"
    assert payment_status(0, 0) == "paid"
    with get_session() as s:
        assert document_balances(s, "sale", []) == {}


# ---- every screen must agree with the shared definition -------------------
def test_dashboard_supplier_profile_drawer_and_ledger_all_agree():
    from database.services import dashboard_service as dash
    from database.services.drawer_service import DrawerService
    from database.services.party_ledger_service import PartyLedgerService
    from database.services.supplier_profile_service import SupplierProfileService

    sale = make_sale()                                          # 20000
    pay("sale", sale["sale_id"], CUSTOMER_ID, "in", 5000)
    sid = sale["sale_id"]

    snap = next(r for r in dash.get_sale_payment_snapshot("admin", ADMIN_ID)["rows"] if r["sale_id"] == sid)
    detail = SupplierProfileService.sale_detail(sid)
    ledger = next(r for r in PartyLedgerService.list_activity_details(CUSTOMER_ID)
                  if r["type"] == "sale" and r["reference"] == f"SAL-{sid}")
    drawer = next(r for r in DrawerService.cashier_transactions(CASHIER_A, "sale") if r["id"] == sid)

    assert (snap["balance"], detail["balance"], ledger["balance"]) == (15000, 15000, 15000)
    assert snap["status"].lower() == ledger["status"] == drawer["payment_status"] == "partial"

    purchase = PurchaseService.record({"supplier_party_id": SUPPLIER_ID, "created_by_user_id": CASHIER_A,
                                       "items": [{"sku": "S", "product_name": "Maize", "product_type": "crops",
                                                  "qty_bags": 1, "total_weight_kg": 100, "unit_cost": 100}]})
    pid = purchase["purchase_id"]
    pay("purchase", pid, SUPPLIER_ID, "out", 10000)
    psnap = next(r for r in dash.get_purchase_payment_snapshot("admin", ADMIN_ID)["rows"] if r["purchase_id"] == pid)
    pdetail = SupplierProfileService.purchase_detail(pid)
    assert psnap["status"] == "PAID" and psnap["balance"] == 0 and pdetail["balance"] == 0
