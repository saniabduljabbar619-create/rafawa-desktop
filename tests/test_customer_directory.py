from datetime import datetime, timedelta

from database.models.sale import Sale
from database.services.advance_service import AdvanceService
from database.services.customer_directory_service import CustomerDirectoryService as D
from database.services.sales_service import SaleService
from database.session import get_session
from tests.conftest import ADMIN_ID, CUSTOMER_ID, make_sale, pay


def test_receivable_counts_only_unpaid_non_voided_sales():
    a = make_sale()                                        # 20000
    b = make_sale()
    pay("sale", b["sale_id"], CUSTOMER_ID, "in", 20000)    # fully paid
    c = make_sale()
    SaleService.void_sale(c["sale_id"], ADMIN_ID)
    pay("sale", a["sale_id"], CUSTOMER_ID, "in", 5000)
    row = D.summaries()[CUSTOMER_ID]
    assert row["receivable"] == 15000 and row["sales_count"] == 2


def test_deposit_is_unused_received_advances():
    AdvanceService.record_advance_in(CUSTOMER_ID, 3000, ADMIN_ID)
    AdvanceService.record_advance_in(CUSTOMER_ID, 1000, ADMIN_ID)
    assert D.summaries()[CUSTOMER_ID]["deposit"] == 4000
    adv = AdvanceService.list_all_party_advances(CUSTOMER_ID)[0]
    AdvanceService.void_advance(adv["id"], ADMIN_ID)
    assert D.summaries()[CUSTOMER_ID]["deposit"] in (1000, 3000)


def test_last_sale_and_filtering_and_empty():
    old = make_sale()
    with get_session() as s:
        s.get(Sale, old["sale_id"]).created_at = datetime.now() - timedelta(days=12)
    make_sale()
    row = D.summaries([CUSTOMER_ID])[CUSTOMER_ID]
    assert D.days_since(row["last_sale_at"]) == 0
    assert D.summaries([999]) == {} and D.summaries([]) == {}
    assert D.days_since(None) is None and D.empty()["receivable"] == 0
