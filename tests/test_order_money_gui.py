"""The Money and Expenses tabs of the order sheet, and the partner profile's money statement."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.services.company_service import CompanyService
from database.services.order_money_service import OrderMoneyService as M
from database.services.order_sheet_service import OrderSheetService as O
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from tests.conftest import ADMIN_ID, CROP_ID
from tests.test_debt_center_gui import type_cell
from tests.test_orders_gui import _hub, _lines, _sheet


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


def test_the_order_sheet_has_money_and_expenses_tabs(qapp, boxes):
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    so = O.create("sales", c, _lines())
    v = _hub()
    v.open_order("sales", c, so)
    pg = _sheet(v)
    assert [pg.tabs.tabText(i) for i in range(pg.tabs.count())] == ["Order lines", "Payments", "Money", "Expenses",
                                                                      "History"]
    # money: typed into the last row; "They gave us" is the default way, a cash drawer is not involved
    type_cell(pg.money_grid, "note", "for the next load")                             # a row saves when the amount is in
    type_cell(pg.money_grid, "amount", "30000")
    type_cell(pg.money_grid, "direction", "We gave them")
    type_cell(pg.money_grid, "amount", "5000")
    rows = M.list_money("sales", order_id=so)
    assert [(r["direction"], r["amount"], r["note"]) for r in rows] == [("in", 30000, "for the next load"), ("out", 5000, "")]
    # apply the first row to the order's payments
    pg.money_grid.table.setCurrentCell(0, 2)
    pg.btn_apply_money.click()
    assert [r["status"] for r in M.list_money("sales", order_id=so)] == ["applied", "completed"]
    assert O.detail("sales", so)["paid"] == 30000
    pg.money_grid.table.setCurrentCell(1, 2)
    pg.btn_apply_money.click()                                                        # money we gave is not a Sales Order payment
    assert "they gave us" in pg.msg.text().lower()


def test_expenses_tab_charge_default_follows_the_kind_and_repayments_are_recorded(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.orders.dialogs import repayment_dialog as rd
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    po = O.create("purchase", p, _lines())
    v = _hub()
    v.open_order("purchase", p, po)
    pg = _sheet(v)
    type_cell(pg.exp_grid, "amount", "20000")                                         # category defaults to Driver
    type_cell(pg.exp_grid, "category", "Fuel")
    type_cell(pg.exp_grid, "charge", "Our cost")
    type_cell(pg.exp_grid, "amount", "15000")
    type_cell(pg.exp_grid, "category", "Food")
    type_cell(pg.exp_grid, "charge", "Our cost")
    type_cell(pg.exp_grid, "amount", "3000")
    ex = M.list_expenses("purchase", order_id=po)
    assert [(e["category"], e["amount"], e["recoverable"]) for e in ex] == [
        ("Driver", 20000, True), ("Fuel", 15000, False), ("Food", 3000, False)]
    # a repayment of 25,000 is taken off the oldest recoverable expenses
    M.add_expense("purchase", po, "Fuel", 7000)                                       # a second recoverable one
    monkeypatch.setattr(rd.RepaymentDialog, "exec_",
                        lambda self: setattr(self, "values", {"amount": 25000, "method": "cash", "note": "part"}) or self.Accepted)
    pg.btn_repay.click()
    got = {e["category"]: e["recovered"] for e in M.list_expenses("purchase", order_id=po)}
    assert got["Driver"] == 20000 and got["Fuel"] == 5000                              # oldest first, only recoverable ones
    # the same default on a Sales Order is "Our cost"
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    so = O.create("sales", c, _lines())
    v2 = _hub()
    v2.open_order("sales", c, so)
    pg2 = _sheet(v2)
    type_cell(pg2.exp_grid, "amount", "4000")
    assert [e["recoverable"] for e in M.list_expenses("sales", order_id=so)] == [False]


def test_partner_profile_shows_the_statement_money_and_expense_sheets(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.orders.dialogs import repayment_dialog as rd
    from apps.product_management_app.features.orders.views import partner_profile_page as pp
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    po = O.create("purchase", p, _lines())
    prof = pp.PartnerProfilePage("purchase", p)
    assert [prof.tabs.tabText(i) for i in range(3)] == ["Orders", "Money", "Expenses"]
    # money on account (they gave us 50,000), typed on the profile
    type_cell(prof.money_grid, "amount", "50000")
    M.add_expense("purchase", po, "Driver", 20000)
    prof.reload()
    assert prof.k_money.val.text() == "50,000.00" and prof.k_recover.val.text() == "20,000.00"
    assert prof.k_net.cap.text().endswith("WE owe them") or prof.k_net.cap.text().endswith("THEY owe us")
    assert prof.k_net.val.text() == "30,000.00"                                      # 20,000 to get back - 50,000 held
    # apply the on-account money to the order (it is a Purchase Order: only money WE gave them applies)
    prof.money_grid.table.setCurrentCell(0, 2)
    monkeypatch.setattr(pp.QInputDialog, "getItem", staticmethod(lambda *a, **k: (f"PO-{po}", True)))
    prof.btn_apply.click()
    assert "we gave them" in prof.msg.text().lower()
    # a repayment spread over the partner's expenses
    monkeypatch.setattr(rd.RepaymentDialog, "exec_",
                        lambda self: setattr(self, "values", {"amount": 12000, "method": "cash", "note": ""}) or self.Accepted)
    prof.btn_repay.click()
    assert prof.k_recover.val.text() == "8,000.00"
    assert M.list_expenses("purchase", partner_id=p)[0]["recovered"] == 12000
