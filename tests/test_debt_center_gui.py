"""Debt Center screens: hub -> list -> worksheet -> payment, and the expenses page."""
from datetime import date

import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMessageBox

from database.services.debt_center_service import DebtCenterService as D
from database.services.expense_service import ExpenseService as E
from tests.conftest import CUSTOMER_ID, SUPPLIER_ID, make_sale
from tests.test_debt_center import _bal, _purchase


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


def _hub():
    from apps.product_management_app.features.debt_center.views.debt_center import DebtCenterView
    return DebtCenterView()


def test_workspace_menu_has_the_debt_center(qapp):
    from apps.product_management_app.workspaces import build_workspaces
    pm = build_workspaces()["pm"]
    assert "admin.debt_center" in pm.routes and "admin.debt_center" in pm.title_map
    assert pm.routes["admin.debt_center"]() is not None


def test_hub_shows_totals_and_double_click_opens_the_lists(qapp):
    _purchase()
    make_sale()
    v = _hub()
    assert v.tile_cred.value.text() == "50,000.00" and v.tile_debt.value.text() == "20,000.00"
    v.tile_cred.mouseDoubleClickEvent(__import__("PySide2.QtGui", fromlist=["QMouseEvent"]).QMouseEvent(
        __import__("PySide2.QtCore", fromlist=["QEvent"]).QEvent.MouseButtonDblClick, QPoint(5, 5),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert v.stack.currentWidget() is v.creditors and v.creditors.table.rowCount() == 1
    v.show_hub()
    v.tile_debt.opened.emit()
    assert v.stack.currentWidget() is v.debtors and v.debtors.table.item(0, 0).text() == "Customer One"
    v.tile_exp.opened.emit()
    assert v.stack.currentWidget() is v.expenses


def test_double_click_a_creditor_opens_worksheet_and_payment_works(qapp, boxes):
    p = _purchase()
    v = _hub()
    v.open_list("creditors")
    v.creditors.table.cellDoubleClicked.emit(0, 0)
    ws = v.worksheet
    assert v.stack.currentWidget() is ws and ws.side == "creditors"
    assert ws.kpi_we.value.text() == "50,000.00" and ws.docs.rowCount() == 1
    ws.amount.setValue(20000)
    ws.btn_pay.click()
    assert _bal("purchase", p) == 30000
    assert ws.kpi_we.value.text() == "30,000.00" and ws.hist.rowCount() == 1
    assert ws.amount.value() == 30000                                   # panel resets to the new balance
    ws.btn_back.click()
    assert v.stack.currentWidget() is v.creditors
    assert v.creditors.table.item(0, 4).text() == "30,000.00"


def test_ticking_a_document_limits_payment_to_it(qapp, boxes):
    a, b = _purchase(), _purchase()
    from apps.product_management_app.features.debt_center.views.worksheet import DebtWorksheetPage
    ws = DebtWorksheetPage(SUPPLIER_ID, "creditors")
    ws.docs.item(1, 0).setCheckState(Qt.Checked)
    assert ws.amount.value() == 50000 and "1 document" in ws.lbl_selected.text()
    ws.btn_pay.click()
    assert _bal("purchase", a) == 50000 and _bal("purchase", b) == 0


def test_receiving_from_a_debtor_via_the_worksheet(qapp, boxes):
    sale = make_sale()["sale_id"]
    from apps.product_management_app.features.debt_center.views.worksheet import DebtWorksheetPage
    ws = DebtWorksheetPage(CUSTOMER_ID, "debtors")
    assert ws.btn_pay.text() == "Record receipt"
    ws.amount.setValue(20000)
    ws.btn_pay.click()
    assert _bal("sale", sale) == 0 and not any(k == "warning" for k, _m in boxes)


def test_worksheet_side_switch_for_a_two_way_party(qapp, boxes):
    _purchase()
    make_sale(buyer=SUPPLIER_ID)
    from apps.product_management_app.features.debt_center.views.worksheet import DebtWorksheetPage
    ws = DebtWorksheetPage(SUPPLIER_ID, "creditors")
    assert ws.docs.rowCount() == 1 and ws.docs.item(0, 1).text().startswith("PUR-")
    ws.seg.set_key("debtors")
    ws._on_side("debtors")
    assert ws.docs.item(0, 1).text().startswith("SAL-")


def test_list_filters_search_and_overdue(qapp):
    _purchase()
    from apps.product_management_app.features.debt_center.views.party_list import DebtPartyListPage
    page = DebtPartyListPage("creditors")
    page.reload()
    assert page.table.rowCount() == 1
    page.search.setText("zzz")
    assert page.table.rowCount() == 0
    page.search.setText("supplier")
    page.seg.set_key("overdue")
    page._render()
    assert page.table.rowCount() == 0                                   # nothing is 30 days old


def test_expenses_page_add_void_and_filters(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.debt_center.views import expenses_page as ep
    E.add({"amount": 12000, "category": "Rent", "description": "Shop rent", "date": date.today()})
    E.add({"amount": 3000, "category": "Transport & Fuel", "description": "Diesel", "date": date.today()})
    page = ep.ExpensesPage()
    page.reload()
    assert page.table.rowCount() == 2 and page.kpi_filtered.value.text() == "15,000.00"
    page.category.setCurrentIndex(page.category.findData("Rent"))
    assert page.table.rowCount() == 1
    page.category.setCurrentIndex(0)
    page.table.selectRow(0)
    monkeypatch.setattr(ep.QInputDialog, "getText", staticmethod(lambda *a, **k: ("typo", True)))
    page.btn_void.click()
    assert page.table.rowCount() == 1 and page.kpi_filtered.value.text() in ("12,000.00", "3,000.00")
    page.chk_void.setChecked(True)
    assert page.table.rowCount() == 2


def test_expense_dialog_saves_and_validates(qapp, boxes):
    from apps.product_management_app.features.debt_center.dialogs.expense_dialog import ExpenseDialog
    dlg = ExpenseDialog()
    dlg.description.setText("Generator fuel")
    dlg._save()                                                          # amount 0 -> refused
    assert boxes and boxes[-1][0] == "warning" and E.list_expenses() == []
    dlg.amount.setValue(8000)
    dlg.category.setCurrentText("Transport & Fuel")
    dlg._save()
    assert [r["description"] for r in E.list_expenses()] == ["Generator fuel"]
