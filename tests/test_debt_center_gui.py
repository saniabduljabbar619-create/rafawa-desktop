"""Debt Center screens: hub -> people list -> person sheet; Expenses and Expenditures sheets."""
from datetime import date

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication, QMessageBox

from database.models.party import Party
from database.services.debt_book_service import DebtBookService as B
from database.services.expense_service import ExpenditureService as XD, ExpenseService as E
from database.session import get_session
from tests.conftest import make_sale

RAW = Qt.UserRole


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    return shown


def _settle():
    for _ in range(3):
        QApplication.processEvents()


def type_cell(grid, key, raw, row=None):
    """Type into a cell the way the editor does: the raw value, then its text."""
    j = next(j for j, c in enumerate(grid.columns, start=1) if c["key"] == key)
    r = len(grid.rows) if row is None else row
    it = grid.table.item(r, j)
    it.setData(RAW, raw)
    it.setText(str(raw))
    _settle()


def _hub():
    from apps.product_management_app.features.debt_center.views.debt_center import DebtCenterView
    return DebtCenterView()


def test_workspace_menu_has_the_debt_center(qapp):
    from apps.product_management_app.workspaces import build_workspaces
    pm = build_workspaces()["pm"]
    assert "admin.debt_center" in pm.routes and pm.routes["admin.debt_center"]() is not None


def test_hub_totals_and_double_click_tiles(qapp):
    c = B.add_person("creditor", "Alhaji Musa")
    B.add_entry(c, {"details": "fertilizer", "owed": 50000})
    d = B.add_person("debtor", "Mama Ngozi")
    B.add_entry(d, {"details": "goods", "owed": 20000})
    B.pay_entry(B.sheet(d)["rows"][0]["id"], {"amount": 5000})
    E.add({"amount": 3000, "description": "diesel", "date": date.today()})
    XD.add({"amount": 100000, "description": "generator", "category": "Equipment & Machinery", "date": date.today()})
    v = _hub()
    assert v.tile_cred.value.text() == "50,000.00" and v.tile_debt.value.text() == "15,000.00"
    assert v.tile_exp.value.text() == "103,000.00" and v.k_net.val.text() == "35,000.00"
    v.tile_cred.opened.emit()
    assert v.stack.currentWidget() is v.creditors and v.creditors.table.item(0, 1).text() == "Alhaji Musa"
    v.show_hub()
    v.tile_debt.opened.emit()
    assert v.stack.currentWidget() is v.debtors
    v.show_hub()
    v.tile_exp.opened.emit()
    assert v.stack.currentWidget() is v.outflows


def test_add_person_from_the_list_opens_their_sheet(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.debt_center.dialogs import person_dialog as pd
    v = _hub()
    v.open_list("debtor")
    
    def fill_and_save(self):
        self.name.setText("Baba Audu")
        self._save()
        return 1
    monkeypatch.setattr(pd.PersonDialog, "exec_", fill_and_save)
    v.debtors.btn_add.click()
    assert [r["name"] for r in B.people("debtor")] == ["Baba Audu"]
    assert v.sheet is not None and v.stack.currentWidget() is v.sheet and v.sheet.kind == "debtor"


def test_any_number_of_people_and_search(qapp):
    for i in range(30):
        B.add_person("creditor", f"Supplier {i}", phone=f"080{i:03d}")
    v = _hub()
    v.open_list("creditor")
    assert v.creditors.table.rowCount() == 30 and v.creditors.k_people.val.text() == "30"
    v.creditors.search.setText("Supplier 7")
    assert v.creditors.table.rowCount() == 1


def test_double_click_a_person_opens_the_sheet_and_typing_in_the_last_row_saves(qapp, boxes):
    c = B.add_person("creditor", "Alhaji Musa")
    v = _hub()
    v.open_list("creditor")
    v.creditors.table.cellDoubleClicked.emit(0, 1)
    sh = v.sheet
    assert v.stack.currentWidget() is sh and sh.grid.table.rowCount() == 1       # just the empty typing row
    type_cell(sh.grid, "details", "Fertilizer on credit")
    assert B.sheet(c)["rows"] == [] and "amount" in sh.grid.msg.text()             # waits for an amount
    type_cell(sh.grid, "owed", "50000")
    rows = B.sheet(c)["rows"]
    assert len(rows) == 1 and rows[0]["owed"] == 50000 and rows[0]["details"] == "Fertilizer on credit"
    assert sh.grid.table.rowCount() == 2 and sh.k_balance.val.text() == "50,000.00"
    B.pay_entry(rows[0]["id"], {"amount": 20000})
    sh.reload()
    assert B.sheet(c)["figures"]["balance"] == 30000
    assert sh.k_balance.val.text() == "30,000.00" and sh.k_balance.cap.text() == "We still owe him"
    run_col = len(sh.grid.columns) + 3
    assert sh.grid.table.item(0, run_col).text() == "30,000.00"
    assert sh.grid.foot.item(0, 3).text() == "50,000.00" and sh.grid.foot.item(0, 5).text() == "20,000.00"


def test_debtor_sheet_uses_debtor_words_and_edit_remove_restore(qapp, boxes, monkeypatch):
    d = B.add_person("debtor", "Mama Ngozi")
    from apps.product_management_app.features.debt_center.views import sheet_grid as sg
    from apps.product_management_app.features.debt_center.views.person_sheet import PersonSheetPage
    sh = PersonSheetPage(d, "debtor")
    assert sh.k_balance.cap.text() == "Settled" and sh.words["bal"] == "He still owes us"
    type_cell(sh.grid, "details", "Maize on credit")
    type_cell(sh.grid, "owed", "10000")
    assert sh.k_balance.cap.text() == "He still owes us"
    type_cell(sh.grid, "owed", "12000", row=0)                                     # change an existing cell
    assert B.sheet(d)["figures"]["owed"] == 12000
    sh.grid.table.setCurrentCell(0, 1)
    monkeypatch.setattr(sg.QInputDialog, "getText", staticmethod(lambda *a, **k: ("typed twice", True)))
    sh.btn_remove.click()
    _settle()
    assert B.sheet(d)["rows"][0]["removed"] and sh.k_balance.val.text() == "0.00"
    assert sh.grid.table.item(0, 2).font().strikeOut()
    sh.grid.table.setCurrentCell(0, 1)
    sh.btn_restore.click()
    _settle()
    assert not B.sheet(d)["rows"][0]["removed"] and sh.k_balance.val.text() == "12,000.00"


def test_a_bad_entry_shows_a_message_and_saves_nothing(qapp, boxes):
    c = B.add_person("creditor", "X")
    from apps.product_management_app.features.debt_center.views.person_sheet import PersonSheetPage
    sh = PersonSheetPage(c, "creditor")
    type_cell(sh.grid, "details", "thing")
    type_cell(sh.grid, "date", "2026-13-45")
    type_cell(sh.grid, "owed", "100")                                              # invalid date reaches the service
    assert B.sheet(c)["rows"] == [] and "date" in sh.grid.msg.text().lower()


def test_the_book_creates_no_customers_and_ignores_real_ones(qapp):
    make_sale()
    with get_session() as s:
        n = s.query(Party).count()
    v = _hub()
    p = B.add_person("debtor", "Customer One")
    B.add_entry(p, {"details": "x", "owed": 1})
    v.open_list("debtor")
    v.open_sheet(p, "debtor")
    with get_session() as s:
        assert s.query(Party).count() == n
    assert v.tile_debt is not None and B.overview()["debtor"]["balance"] == 1      # not the 20,000 sale


# ------------------------------------------------------------------ expenses / expenditures
def _outflows():
    from apps.product_management_app.features.debt_center.views.outflow_sheets import OutflowSheetsPage
    return OutflowSheetsPage()


def test_two_separate_sheets_as_tabs(qapp, boxes):
    pg = _outflows()
    assert [pg.tabs.tabText(i) for i in range(pg.tabs.count())] == ["Expenses", "Expenditures"]
    type_cell(pg.grid, "description", "Diesel for generator")
    type_cell(pg.grid, "amount", "4500")
    assert [r["description"] for r in E.list_expenses()] == ["Diesel for generator"] and XD.list_expenses() == []
    assert pg.k_period.val.text() == "4,500.00"
    pg.tabs.setCurrentIndex(1)
    assert pg.grid.rows == [] and pg.k_period.val.text() == "0.00"
    type_cell(pg.grid, "description", "Delivery truck")
    type_cell(pg.grid, "category", "Vehicles")
    type_cell(pg.grid, "amount", "4500000")
    assert [r["description"] for r in XD.list_expenses()] == ["Delivery truck"]
    assert XD.list_expenses()[0]["category"] == "Vehicles" and XD.list_expenses()[0]["expense_no"].startswith("EXD-")
    pg.tabs.setCurrentIndex(0)
    assert len(pg.grid.rows) == 1 and pg.k_period.val.text() == "4,500.00"                # unaffected


def test_expense_row_edit_remove_restore_and_running_total(qapp, boxes, monkeypatch):
    E.add({"amount": 1000, "description": "a", "date": date.today()})
    E.add({"amount": 2000, "description": "b", "date": date.today()})
    pg = _outflows()
    run_col = len(pg.grid.columns) + 1
    assert [pg.grid.table.item(r, run_col).text() for r in range(2)] == ["1,000.00", "3,000.00"]
    type_cell(pg.grid, "amount", "1500", row=0)
    assert sorted(r["amount"] for r in E.list_expenses()) == [1500, 2000]
    from apps.product_management_app.features.debt_center.views import sheet_grid as sg
    pg.grid.table.setCurrentCell(1, 1)
    monkeypatch.setattr(sg.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))     # reason is optional
    pg.btn_remove.click()
    _settle()
    assert len(E.list_expenses()) == 1 and pg.k_period.val.text() == "1,500.00" and pg.grid.rows[1]["removed"]
    pg.grid.table.setCurrentCell(1, 1)
    pg.btn_restore.click()
    _settle()
    assert len(E.list_expenses()) == 2 and pg.k_period.val.text() == "3,500.00"


def test_period_filter_and_validation_message(qapp, boxes):
    from datetime import timedelta
    E.add({"amount": 10, "description": "recent", "date": date.today()})
    E.add({"amount": 99, "description": "old", "date": date.today() - timedelta(days=400)})
    pg = _outflows()
    assert [r["description"] for r in pg._rows] == ["recent"]
    pg.period.set_key("all")
    pg.reload()
    assert len(pg._rows) == 2 and pg.k_period.val.text() == "109.00"
    type_cell(pg.grid, "description", "no amount yet")
    assert "amount" in pg.grid.msg.text().lower() and len(E.list_expenses()) == 2


def test_record_payment_dialog_saves_each_payment_on_the_selected_row(qapp, boxes):
    from apps.product_management_app.features.debt_center.dialogs.payment_dialog import PaymentDialog
    from apps.product_management_app.features.debt_center.views.person_sheet import PersonSheetPage
    c = B.add_person("creditor", "Alhaji Musa")
    r = B.add_entry(c, {"details": "Crops purchasing", "owed": 50_000_000})
    sh = PersonSheetPage(c, "creditor")
    sh.grid.say  # page is built
    dlg = PaymentDialog(r, c, "We paid him")
    dlg.amount.setText("30000000")
    dlg.save()
    dlg.amount.setText("10000000")
    dlg.save()
    assert dlg.changed and dlg.table.rowCount() == 2 and "paid so far 40,000,000.00" in dlg.sub.text()
    dlg.amount.setText("20000000")
    dlg.save()                                                                    # more than the 10m left
    assert "Only 10,000,000.00" in dlg.msg.text() and dlg.table.rowCount() == 2
    sh.reload()
    assert sh.k_balance.val.text() == "10,000,000.00" and sh.k_paid.val.text() == "40,000,000.00"
    assert sh.grid.table.item(0, 5).text() == "40,000,000.00" and sh.grid.table.item(0, 6).text() == "10,000,000.00"
