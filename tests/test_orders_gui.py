"""The unified Orders menu and the sheet-based order workspace (Sales and Purchase Orders)."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.services.company_service import CompanyService
from database.services.document_balance import document_balances  # noqa: F401  (keeps import order stable)
from database.services.order_sheet_service import OrderSheetService as O
from database.services.purchase_order_supplier_service import PurchaseOrderSupplierService
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, FERT_ID, stock
from tests.test_debt_center_gui import _settle, type_cell

MAIZE = "Maize  (CROP-MAIZE)"
NPK = "NPK  (FERT-NPK)"


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    from apps.product_management_app.features.sales_orders.dialogs import driver_picker_dialog as dp
    monkeypatch.setattr(dp.DriverPickerDialog, "exec_", lambda self: self.Accepted)
    return shown


def _lines():
    return [{"product_id": CROP_ID, "name": "Maize", "type": "crops", "kg": 500, "bags": 5, "price": 110},
            {"product_id": FERT_ID, "name": "NPK", "type": "fertilizer", "kg": 0, "bags": 10, "price": 9000}]


def _hub(mode="admin"):
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    return OrdersHubView(mode=mode)


def _seed():
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    so = O.create("sales", c, _lines())
    po = O.create("purchase", p, _lines())
    O.dispatch("sales", so)
    return c, p, so, po


def _sheet(v):
    return v.stack.currentWidget()


# ----------------------------------------------------------------------------- navigation
def test_sidebar_has_a_single_orders_entry_in_both_workspaces(qapp):
    from apps.product_management_app.workspaces import build_workspaces
    ws = build_workspaces()
    for key, route in (("pm", "admin.orders"), ("cashier", "cashier.orders")):
        labels = [i.label for i in ws[key].menu]
        assert labels.count("Orders") == 1 and "Sales Orders" not in labels and "Purchase Orders" not in labels
        assert ws[key].routes[route]() is not None


def test_board_shows_both_kinds_and_double_click_opens_the_order_sheet_and_back_returns(qapp):
    so_c, _p, so, po = _seed()
    v = _hub()
    assert v.board.table.rowCount() == 2 and v.kpi_sales.value.text() == "1"
    v.board.kind_seg.set_key("sales")
    v.board.reload()
    v.board.table.selectRow(0)
    v.board.table.cellDoubleClicked.emit(0, 0)
    from apps.product_management_app.features.orders.views.order_sheet_page import OrderSheetPage
    page = _sheet(v)
    assert isinstance(page, OrderSheetPage) and page.order_id == so and page.stage == "in_transit"
    assert v.tabs_row.isHidden()
    page.btn_back.click()
    assert _sheet(v) is v.board and not v.tabs_row.isHidden()


def test_partner_lists_profile_and_new_order_navigation(qapp):
    c, _p, so, _po = _seed()
    v = _hub()
    v.board.btn_new_sales.click()
    pl = v._pages["sales"]
    assert {pl.table.item(r, 1).text() for r in range(pl.table.rowCount())} == {"Acme Foods", "Farm Co"}   # shared pool
    row = next(r for r in range(pl.table.rowCount()) if pl.table.item(r, 1).text() == "Acme Foods")
    pl.table.cellDoubleClicked.emit(row, 1)
    from apps.product_management_app.features.orders.views.partner_profile_page import PartnerProfilePage
    prof = _sheet(v)
    assert isinstance(prof, PartnerProfilePage) and prof.table.rowCount() == 1
    assert prof.table.item(0, 0).text() == f"SO-{so}" and prof.k_active.val.text() == "1"
    prof.new_order.emit()
    page = _sheet(v)
    assert page.order_id is None and page.stage == "new"
    page.btn_back.click()
    assert _sheet(v) is prof
    prof.table.cellDoubleClicked.emit(0, 0)
    assert _sheet(v).order_id == so
    _sheet(v).btn_back.click()
    prof.btn_back.click()
    assert _sheet(v) is pl and v.tabs.key() == "sales"


def test_add_a_new_partner_from_the_list(qapp, monkeypatch):
    from apps.product_management_app.features.orders.views import partners_page as pp
    v = _hub()
    v.show_tab("purchase")

    def fill(self):
        self.name.setText("Green Valley")
        self.phone.setText("0800")
        self._save()
        return 1
    monkeypatch.setattr(pp.PartnerDialog, "exec_", fill)
    v._pages["purchase"].btn_new.click()
    assert {c["name"] for c in CompanyService.search("")} == {"Green Valley"}          # also pickable for Sales Orders
    assert v.stack.currentWidget().__class__.__name__ == "PartnerProfilePage"


# ----------------------------------------------------------------------------- the sheet, Sales
def test_sales_order_whole_life_on_one_sheet(qapp, boxes):
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    v = _hub()
    v.open_order("sales", c, None)
    pg = _sheet(v)
    assert pg.btn_go.isHidden() and not pg.btn_add.isHidden()
    # draft: type the lines into the last row; each saves itself
    kg0, bags0 = stock(CROP_ID)[1], stock(FERT_ID)[0]
    type_cell(pg.grid, "product", MAIZE)
    assert pg.order_id is None and "bags" in pg.msg.text().lower()
    type_cell(pg.grid, "bags", "5")
    type_cell(pg.grid, "kg", "500")
    assert pg.order_id is None and "price" in pg.msg.text() and "usual" in pg.msg.text()   # price must be typed
    type_cell(pg.grid, "price", "110")
    assert pg.order_id and pg.stage == "draft" and O.detail("sales", pg.order_id)["estimate"] == 55000
    type_cell(pg.grid, "product", NPK)
    type_cell(pg.grid, "bags", "10")
    type_cell(pg.grid, "price", "9000")
    assert len(O.detail("sales", pg.order_id)["lines"]) == 2 and pg.k_value.val.text() == "145,000.00"
    type_cell(pg.grid, "price", "120", row=0)                                   # change an existing cell
    assert O.detail("sales", pg.order_id)["lines"][0]["price"] == 120
    assert (stock(CROP_ID)[1], stock(FERT_ID)[0]) == (kg0, bags0)                # a draft moves no stock
    # dispatch
    pg.btn_go.click()
    _settle()
    assert pg.stage == "in_transit" and stock(CROP_ID)[1] == kg0 - 500 and stock(FERT_ID)[0] == bags0 - 10
    assert pg.grid.allow_add is False and not pg.btn_confirm.isHidden() and pg.btn_go.isHidden()
    # the other side's result: some rejected, a different price
    type_cell(pg.grid, "rcv", "500", row=0)
    type_cell(pg.grid, "rej", "50", row=0)
    type_cell(pg.grid, "rprice", "100", row=0)
    assert pg.lines[0]["acc_qty"] == 450
    run = pg.grid.table
    assert run.item(0, 8).text() == "450" and run.item(0, 9).text() == "45,000.00"      # accepted / amount (auto)
    type_cell(pg.grid, "rej", "999", row=0)
    assert "more than" in pg.msg.text() and pg.lines[0]["rej_qty"] == 50
    pg.btn_confirm.click()
    assert "Still to enter" in pg.msg.text() and pg.stage == "in_transit"          # NPK line not entered yet
    pg.btn_fill.click()                                                             # everything as ordered...
    _settle()
    type_cell(pg.grid, "rej", "50", row=0)
    type_cell(pg.grid, "rprice", "100", row=0)                                      # ...but the crops as agreed above
    pg.btn_confirm.click()
    _settle()
    assert pg.stage == "confirmed" and pg.d["total"] == 450 * 100 + 10 * 9000 and stock(CROP_ID)[1] == kg0 - 450
    assert pg.k_balance.val.text() == "135,000.00" and pg.tabs.currentIndex() == 1
    # payments tab
    type_cell(pg.pay_grid, "amount", "100000")
    assert pg.d["paid"] == 100000 and pg.k_balance.val.text() == "35,000.00" and pg.d["pay_status"] == "partial"
    type_cell(pg.pay_grid, "amount", "40000")
    assert "exceeds" in pg.msg.text() and pg.d["paid"] == 100000
    type_cell(pg.pay_grid, "amount", "35000")
    assert pg.d["pay_status"] == "paid" and all("✓" in pg.step_labels[i].text() for i in range(3))
    # history lists the whole story
    hist = [pg.d["timeline"][i]["event"] for i in range(len(pg.d["timeline"]))]
    assert hist[0].startswith("Created") and any(h.startswith("Dispatched") for h in hist) and hist[-1].startswith("Confirmed")
    texts = [pg.slot_hist.layout().itemAt(0).widget().table.item(r, 2).text() for r in range(
        pg.slot_hist.layout().itemAt(0).widget().table.rowCount())]
    assert any("Payment" in t for t in texts)


def test_void_a_payment_needs_a_reason(qapp, boxes, monkeypatch):
    c, _p, so, _po = _seed()
    O.confirm("sales", so, [{"id": l["id"], "type": l["type"], "rcv_qty": l["kg"] or l["bags"], "rej_qty": 0,
                             "rcv_price": l["price"]} for l in O.detail("sales", so)["lines"]])
    O.pay("sales", so, 1000)
    v = _hub()
    v.open_order("sales", c, so)
    pg = _sheet(v)
    from apps.product_management_app.features.debt_center.views import sheet_grid as sg
    monkeypatch.setattr(sg.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    pg.pay_grid.table.setCurrentCell(0, 1)
    pg.pay_grid.remove()
    _settle()
    assert "reason" in pg.msg.text().lower() and pg.d["paid"] == 1000
    monkeypatch.setattr(sg.QInputDialog, "getText", staticmethod(lambda *a, **k: ("keyed wrongly", True)))
    pg.pay_grid.table.setCurrentCell(0, 1)
    pg.pay_grid.remove()
    _settle()
    assert pg.d["paid"] == 0 and pg.pay_grid.rows[0]["removed"]


def test_draft_rules_last_line_cant_be_removed_and_cancel(qapp, boxes, monkeypatch):
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    oid = O.create("sales", c, _lines()[:1])
    v = _hub()
    v.open_order("sales", c, oid)
    pg = _sheet(v)
    from apps.product_management_app.features.debt_center.views import sheet_grid as sg
    monkeypatch.setattr(sg.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    pg.grid.table.setCurrentCell(0, 1)
    pg.btn_remove.click()
    _settle()
    assert "at least one line" in pg.msg.text() and len(O.detail("sales", oid)["lines"]) == 1
    type_cell(pg.grid, "product", MAIZE)
    type_cell(pg.grid, "bags", "2")
    assert "kg" in pg.msg.text().lower()                                      # crops need weight too
    from apps.product_management_app.features.orders.views import order_sheet_page as osp
    monkeypatch.setattr(osp.QInputDialog, "getText", staticmethod(lambda *a, **k: ("not needed", True)))
    pg.btn_cancel.click()
    _settle()
    assert pg.stage == "cancelled" and pg.btn_go.isHidden() and pg.grid.allow_add is False


def test_no_stock_blocks_dispatch_with_a_clear_message(qapp, boxes):
    c = CompanyService.create("Acme Foods", ADMIN_ID)
    oid = O.create("sales", c, [{"product_id": CROP_ID, "name": "Maize", "type": "crops", "kg": 99999, "bags": 5, "price": 1}])
    v = _hub()
    v.open_order("sales", c, oid)
    pg = _sheet(v)
    pg.btn_go.click()
    assert "Insufficient stock" in pg.msg.text() and pg.stage == "draft"


def test_revert_to_draft_restores_stock_and_editing(qapp, boxes):
    c, _p, so, _po = _seed()
    kg_dispatched = stock(CROP_ID)[1]
    v = _hub()
    v.open_order("sales", c, so)
    pg = _sheet(v)
    pg.btn_revert.click()
    _settle()
    assert pg.stage == "draft" and stock(CROP_ID)[1] == kg_dispatched + 500 and not pg.btn_go.isHidden()


# ----------------------------------------------------------------------------- the sheet, Purchase
def test_purchase_order_flow_stock_comes_in_only_when_received(qapp, boxes):
    p = PurchaseOrderSupplierService.create("Farm Co", ADMIN_ID)
    v = _hub()
    v.open_order("purchase", p, None)
    pg = _sheet(v)
    assert pg.btn_go.text() == "Mark in transit"
    kg0 = stock(CROP_ID)[1]
    type_cell(pg.grid, "product", MAIZE)
    type_cell(pg.grid, "bags", "5")
    type_cell(pg.grid, "kg", "500")
    type_cell(pg.grid, "price", "80")
    assert pg.d["lines"][0]["price"] == 80
    type_cell(pg.pay_grid, "amount", "20000")                                    # advance before the order is confirmed
    assert pg.d["paid"] == 20000 and pg.d["balance"] is None
    pg.btn_go.click()
    _settle()
    assert pg.stage == "in_transit" and stock(CROP_ID)[1] == kg0
    pg.btn_fill.click()
    _settle()
    type_cell(pg.grid, "rej", "20", row=0)
    pg.btn_confirm.click()
    _settle()
    assert pg.stage == "confirmed" and stock(CROP_ID)[1] == kg0 + 480 and pg.d["total"] == 480 * 80
    assert pg.d["balance"] == 480 * 80 - 20000 and pg.k_balance.cap.text() == "Still to pay"


# ----------------------------------------------------------------------------- cashier mode
def test_cashier_can_work_delivery_and_payments_but_not_create_or_dispatch(qapp, boxes):
    c, _p, so, _po = _seed()
    draft = O.create("sales", c, _lines())
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    v = _hub("cashier")
    v.show_tab("sales")
    prof_btn = v._pages["sales"].btn_new
    assert prof_btn.isHidden()
    v.open_profile("sales", c)
    assert _sheet(v).btn_new.isHidden()
    v.open_order("sales", c, so)
    pg = _sheet(v)
    assert pg.btn_revert.isHidden() and pg.btn_go.isHidden() and not pg.btn_confirm.isHidden()
    pg.btn_fill.click()
    _settle()
    pg.btn_confirm.click()
    _settle()
    assert pg.stage == "confirmed"
    type_cell(pg.pay_grid, "amount", "5000")
    assert pg.d["paid"] == 5000
    v.open_order("sales", c, draft)
    d = _sheet(v)
    assert d.btn_add.isHidden() and d.btn_go.isHidden() and d.btn_cancel.isHidden() and d.grid.allow_add is False
