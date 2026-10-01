from datetime import date

from database.models.cashier_settlement import CashierSettlement
from database.models.stock_movement import StockMovement
from database.services import dashboard_service as dash
from database.services.drawer_service import DrawerService
from database.services.party_ledger_service import PartyLedgerService
from database.services.sales_service import SaleService
from database.session import get_session
from tests.conftest import (ADMIN_ID, CASHIER_A, CASHIER_B, CROP_ID, CUSTOMER_ID, make_sale,
                            pay, stock)


def _row(rows, reference):
    """The document row (payment rows share the parent's reference)."""
    return next(r for r in rows if r.get("reference") == reference and r["type"] in ("sale", "purchase"))


# ---------------------------------------------------------------- ledger
def test_ledger_balance_and_status_follow_payments():
    res = make_sale()                                         # 20000
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 5000)
    row = _row(PartyLedgerService.list_activity_details(CUSTOMER_ID), f"SAL-{res['sale_id']}")
    assert (row["total"], row["paid"], row["balance"], row["status"]) == (20000, 5000, 15000, "partial")


def test_voided_sale_owes_nothing_but_stays_listed():
    res = make_sale()
    SaleService.void_sale(res["sale_id"], ADMIN_ID)
    row = _row(PartyLedgerService.list_activity_details(CUSTOMER_ID), f"SAL-{res['sale_id']}")
    assert row["status"] == "voided" and row["total"] == 0 and row["balance"] == 0


def test_ledger_sorts_even_when_a_date_is_missing():
    make_sale()
    with get_session() as s:
        from database.models.sale import Sale
        s.query(Sale).update({Sale.completed_at: None})
    PartyLedgerService.list_activity_details(CUSTOMER_ID)   # must not raise TypeError


def test_dashboard_snapshot_ignores_voided_sales():
    keep = make_sale()
    gone = make_sale()
    SaleService.void_sale(gone["sale_id"], ADMIN_ID)
    snap = dash.get_sale_payment_snapshot("admin", ADMIN_ID)
    assert [r["sale_id"] for r in snap["rows"]] == [keep["sale_id"]]


# ---------------------------------------------------------------- hard delete
def test_delete_sale_restores_stock_and_clears_settlements():
    res = make_sale(user=CASHIER_A)
    pid = pay("sale", res["sale_id"], CUSTOMER_ID, "in", 1000, user=CASHIER_B)  # cross-cashier
    DrawerService.compute(CASHIER_A)                         # mirrors the settlement row
    with get_session() as s:
        assert s.query(CashierSettlement).filter_by(source_payment_id=pid).count() == 1

    dash.delete_sale(res["sale_id"], ADMIN_ID, "test")

    assert stock(CROP_ID) == (20, 2000)
    with get_session() as s:
        assert s.query(CashierSettlement).count() == 0
        assert s.query(StockMovement).filter(StockMovement.reference_type == "sale_item").count() == 0


# ---------------------------------------------------------------- drawer
def test_cross_cashier_payment_creates_a_debt_between_them():
    res = make_sale(user=CASHIER_A)
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 4000, user=CASHIER_B)   # B collected A's sale
    a = DrawerService.compute(CASHIER_A)
    b = DrawerService.compute(CASHIER_B)
    # B holds money that belongs to A's sale: B owes A
    assert a["cards"]["owed_to_me_open"] == 4000 and a["cards"]["i_owe_open"] == 0
    assert b["cards"]["i_owe_open"] == 4000
    assert b["human"]["money_collected"] == 4000


def test_settling_clears_the_open_debt():
    res = make_sale(user=CASHIER_A)
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 4000, user=CASHIER_B)
    DrawerService.compute(CASHIER_A)
    DrawerService.settle_with(CASHIER_A, CASHIER_B, ADMIN_ID)
    assert DrawerService.compute(CASHIER_B)["cards"]["i_owe_open"] == 0


def test_drawer_totals_skip_voided_documents():
    keep = make_sale(user=CASHIER_A)
    gone = make_sale(user=CASHIER_A)
    SaleService.void_sale(gone["sale_id"], ADMIN_ID)
    totals = DrawerService.party_totals(CASHIER_A, "mine")
    assert totals["grand_sales_total"] == 20000
    rows = DrawerService.cashier_transactions(CASHIER_A, "sale")
    status = {r["id"]: r["payment_status"] for r in rows}
    assert status[gone["sale_id"]] == "voided" and status[keep["sale_id"]] == "unpaid"


def test_date_range_accepts_a_single_day():
    make_sale(user=CASHIER_A)
    today = date.today()
    out = DrawerService.compute(CASHIER_A, today, today)
    assert out["cards"]["sales_net"] == 20000
