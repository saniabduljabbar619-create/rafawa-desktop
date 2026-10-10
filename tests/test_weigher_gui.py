"""The weigher's screens end to end, plus the cashier/admin side that takes over."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.models.purchase import Purchase
from database.services.user_admin_service import UserAdminService
from database.services.weigh_service import WeighService as W
from database.session import get_session
from shared.auth.permissions import PermissionDenied, workspace_allowed
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, SUPPLIER_ID, stock
from tests.test_weigh_service import CASHIER, as_role


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


@pytest.fixture
def printed(monkeypatch):
    from apps.product_management_app.features.receipts_v2 import receipt_print
    out = []
    monkeypatch.setattr(receipt_print, "print_text", lambda parent, text, **k: out.append(text))
    return out


def _station():
    from apps.product_management_app.features.weigher.views.weigher_station import WeigherStationView
    as_role("weigher", CASHIER_A)
    return WeigherStationView()


def _type_round(v, bags, kg):
    """Type one round into the blank row at the bottom of the rounds table (like the purchase intake)."""
    from PySide2.QtWidgets import QApplication
    t = v.rounds_table
    r = t.rowCount() - 1
    t.item(r, 1).setText(str(bags))
    t.item(r, 2).setText(str(kg))
    QApplication.processEvents()
    QApplication.processEvents()


def _pick(v, name):
    for i in range(v.party_list.count()):
        if name in v.party_list.item(i).text():
            v.party_list.setCurrentRow(i)
            return
    raise AssertionError(name)


def test_station_full_flow_from_party_to_signed_slip(qapp, boxes, printed):
    v = _station()
    assert not v.btn_start.isEnabled()
    _pick(v, "Supplier One")
    v.kind_seg.set_key("supplier")
    v.start_ticket()
    assert v.stack.currentIndex() == 1 and v.ticket_id

    # the product starts on the placeholder, so nothing is picked by accident
    assert v.product_cb.currentText() == "Select Product"
    v.add_line()
    assert any("Select a product" in m for _k, m in boxes)
    v.product_cb.setCurrentIndex(1)
    v.area_cb.setCurrentText("Gwarzo")
    v.grade_cb.setCurrentText("A")
    v.add_line()
    assert v.lines_list.count() == 1

    _type_round(v, "3bags", "245.5")
    _type_round(v, "2", "160")
    _type_round(v, "0.5", "21.25")                                # a part-bag
    assert v.lbl_total_bags.text() == "5.5" and v.lbl_total_kg.text() == "426.75 kg"
    assert v.rounds_table.rowCount() == 4                          # 3 rounds + the next blank row
    assert v.lbl_round_totals.text() == "Total: 5.5 bags  -  426.75 kg"

    _type_round(v, "1", "0")                                      # zero weight is refused
    assert any("Weight must be more than zero" in m for _k, m in boxes)
    assert W.get_ticket(v.ticket_id)["total_kg"] == 426.75

    v.rounds_table.setCurrentCell(2, 1)                          # a slip of the finger: remove round 3
    v.remove_round()
    assert v.lbl_total_kg.text() == "405.50 kg"

    v.btn_finish.click()                                           # weighing done -> the PRICE step
    assert v.stack.currentIndex() == 2 and v.price_table.rowCount() == 1
    assert v.price_table.item(0, 0).text() == "Maize" and v.price_table.item(0, 3).text() == "405.50"
    spin = next(iter(v._price_spins.values()))
    assert spin.value() == 80 and "Usual price" in spin.toolTip()       # Maize's usual price is filled in
    spin.setValue(0)
    v.btn_price_next.click()                                       # price cleared: refused, stays on the step
    assert v.stack.currentIndex() == 2 and any("price per kg for: Maize" in m for _k, m in boxes)
    spin.setValue(450)
    assert v.lbl_price_amount.text() == f"₦{405.5 * 450:,.2f}" and v.price_table.item(0, 5).text() == f"₦{405.5 * 450:,.2f}"
    v.btn_price_next.click()                                       # price saved on the ticket -> the receipt step
    assert v.stack.currentIndex() == 3 and W.get_ticket(v.ticket_id)["total_amount"] == 405.5 * 450
    # the two weigher receipts: Option 1 (weights only) is the default, Option 2 adds price, amount and payment
    assert v.receipt_seg.key() == "weighing" and "WEIGHING RECEIPT (OPTION 1)" in v.preview.toPlainText()
    assert "GRAND TOTAL BAGS" in v.preview.toPlainText() and "Price/KG" not in v.preview.toPlainText()
    v.receipt_seg.set_key("value"); v._render_preview()
    text = v.preview.toPlainText()
    assert "FULL VALUE RECEIPT (OPTION 2)" in text and "Price/KG" in text and "Payment Status: Unpaid" in text
    assert f"{405.5 * 450:,.0f}" in text

    v.btn_submit.click()
    t = W.get_ticket(v.ticket_id)
    assert t["status"] == "submitted" and t["receipt_type"] == "value"
    assert printed == []                                           # submitting never prints by itself
    assert v.btn_submit.isHidden() and not v.btn_new.isHidden() and not v.btn_print.isHidden()
    v.btn_print.click()                                            # only the button prints
    assert len(printed) == 1 and "FULL VALUE RECEIPT (OPTION 2)" in printed[0] and "Customer Signature" in printed[0]
    v.receipt_seg.set_key("weighing")
    v.btn_print.click()
    assert len(printed) == 2 and "WEIGHING RECEIPT (OPTION 1)" in printed[1] and "Weigher Signature" in printed[1]
    v.btn_new.click()
    assert v.stack.currentIndex() == 0 and v.ticket_id == 0


def test_price_step_leaves_a_product_without_a_usual_price_empty_and_amber(qapp, boxes):
    from database.models.product import Product
    with get_session() as sess:
        sess.get(Product, CROP_ID).default_cost_rate = None
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    _type_round(v, "1", "100")
    v.btn_finish.click()
    spin = next(iter(v._price_spins.values()))
    assert spin.value() == 0 and "No usual price" in spin.toolTip() and "fff7ed" in spin.styleSheet()
    spin.setValue(300)
    assert spin.styleSheet() == "" and v.lbl_price_amount.text() == "₦30,000.00"


def test_price_step_fills_the_usual_price_and_back_returns_to_weighing(qapp, boxes):
    from database.models.product import Product
    with get_session() as sess:
        sess.get(Product, CROP_ID).default_cost_rate = 420
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    _type_round(v, "2", "100")
    v.btn_finish.click()
    spin = next(iter(v._price_spins.values()))
    assert spin.value() == 420 and v.lbl_price_amount.text() == "₦42,000.00"      # filled, and editable
    v.btn_price_back.click()
    assert v.stack.currentIndex() == 1
    v.btn_finish.click()                                                          # a saved price is kept
    next(iter(v._price_spins.values())).setValue(430)
    v.btn_price_next.click()
    v.btn_back.click()                                                            # receipt -> back to the price step
    assert v.stack.currentIndex() == 2 and next(iter(v._price_spins.values())).value() == 430


def test_station_resume_and_cancel(qapp, boxes):
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    tid = v.ticket_id
    v.product_cb.setCurrentIndex(1); v.add_line()
    _type_round(v, "1", "50")
    v.new_weighing()
    assert v.open_list.count() == 1
    v.open_list.setCurrentRow(0)
    v.btn_resume.click()
    assert v.ticket_id == tid and v.rounds_table.rowCount() == 2 and v.stack.currentIndex() == 1
    v.btn_cancel_ticket.click()
    assert W.get_ticket(tid)["status"] == "cancelled" and v.stack.currentIndex() == 0


def test_finish_needs_a_round_and_oversized_rounds_are_refused(qapp, boxes):
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    v.btn_finish.click()
    assert v.stack.currentIndex() == 1 and any("at least one round" in m for _k, m in boxes)
    _type_round(v, "4", "300")                                    # more than 3 bags in one round
    assert any("at most 3 bags" in m for _k, m in boxes)
    assert W.get_ticket(v.ticket_id)["total_kg"] == 0


def test_register_new_supplier_from_the_station(qapp, boxes):
    from apps.product_management_app.features.weigher.dialogs.register_party_dialog import RegisterPartyDialog
    v = _station()
    d = RegisterPartyDialog(v, kind="customer")
    d.name.setText("Musa Farms"); d.phone.setText("08031234567"); d.address.setText("Kano")
    d.kind.set_key("supplier")
    d._save()
    assert d.party["kind"] == "supplier" and d.result() == d.Accepted
    v._load_party_list(select_id=d.party["id"])
    assert v.party_list.currentItem().text().startswith("Musa Farms")
    d2 = RegisterPartyDialog(v)
    d2.name.setText("Dup"); d2.phone.setText("08031234567")
    d2._save()
    assert d2.party is None and any("already registered" in m for _k, m in boxes)


def _priced_ticket(kg=100, price=450, bags=2):
    tid = W.create_ticket(SUPPLIER_ID, "supplier"); line = W.add_line(tid, CROP_ID, "Gwarzo", "A")
    W.record_round(line, bags, kg)
    W.set_line_price(line, price)
    return tid, line


def test_finalize_list_shows_every_weighing_with_amounts_and_the_admin_queue_does_too(qapp, boxes, printed):
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    as_role("weigher", CASHIER_A)
    tid, _line = _priced_ticket()
    W.submit_ticket(tid)
    open_tid = W.create_ticket(SUPPLIER_ID, "supplier")                          # a second one, still weighing

    mine = WeigherTicketsView()
    headers = [mine.table.horizontalHeaderItem(i).text() for i in range(mine.table.columnCount())]
    assert "Amount" in headers and mine.table.rowCount() == 2 and mine.kpi_wait.value.text() == "1"
    assert mine.kpi_open.value.text() == "1"
    mine.table.selectRow(1)                                                      # the submitted one (oldest last)
    assert mine.btn_edit.isEnabled() and mine.btn_remove.isEnabled() and mine.btn_print.isEnabled()
    assert printed == []
    mine.btn_print.click()                                                       # Option 1
    mine.btn_print_value.click()                                                 # Option 2
    assert len(printed) == 2 and "WEIGHING RECEIPT (OPTION 1)" in printed[0] and "TOTAL AMOUNT" not in printed[0]
    assert "FULL VALUE RECEIPT (OPTION 2)" in printed[1] and "TOTAL AMOUNT" in printed[1]
    mine.table.selectRow(0)                                                      # the open one: nothing to print yet
    assert not mine.btn_print.isEnabled() and not mine.btn_print_value.isEnabled() and open_tid

    as_role("cashier", CASHIER)
    q = WeighTicketsAdminView(mode="cashier")
    headers = [q.table.horizontalHeaderItem(i).text() for i in range(q.table.columnCount())]
    assert "Amount" in headers and "Weigher" in headers
    assert q.table.rowCount() == 1 and q.kpi_wait.value.text() == "1"
    q.filter_seg.set_key("finalized"); q.refresh()
    assert q.table.rowCount() == 0


def test_finalize_list_remove_keeps_the_ticket_in_the_removed_filter(qapp, boxes):
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    as_role("weigher", CASHIER_A)
    tid, _line = _priced_ticket()
    W.submit_ticket(tid)
    v = WeigherTicketsView()
    v.table.selectRow(0)
    v.remove_selected()                                                          # the stub answers Yes
    assert W.get_ticket(tid)["status"] == "cancelled"
    v.filter_seg.set_key("cancelled"); v.refresh()
    assert v.table.rowCount() == 1
    v.table.selectRow(0)
    assert not v.btn_edit.isEnabled() and not v.btn_remove.isEnabled()          # a removed ticket is closed


def test_finalize_list_edit_opens_the_weigher_dialog_and_saves_rounds_and_prices(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    from PySide2.QtWidgets import QApplication
    as_role("weigher", CASHIER_A)
    tid, line = _priced_ticket(kg=100, price=400, bags=2)
    W.submit_ticket(tid)

    opened = []
    monkeypatch.setattr(WeighTicketReviewDialog, "exec_", lambda self: opened.append(self) or 1)
    v = WeigherTicketsView()
    v.table.selectRow(0)
    v.edit_selected()
    d = opened[0]
    assert d.mode == "weigher" and d.windowTitle() == "Edit Weighing" and d.editable
    assert d.btn_finalize.text() == "Save Changes" and d.btn_reject.text() == "Remove Ticket"
    assert d._price_spins[line].value() == 400

    d.rounds_table.item(0, 2).setText("120")                                      # correct a weight
    QApplication.processEvents()
    d.add_bags.setText("1"); d.add_kg.setText("50"); d._add_round()
    d._price_spins[line].setValue(410)
    d.btn_finalize.click()                                                        # Save Changes
    t = W.get_ticket(tid)
    assert t["status"] == "submitted" and t["total_kg"] == 170 and t["total_amount"] == 170 * 410
    assert d.result() == d.Accepted

    d._price_spins[line].setValue(0)
    d.save_changes()
    assert any("price per kg" in m for _k, m in boxes)                            # a price is always required


def test_weigher_dialog_removes_a_ticket_and_is_read_only_once_finalized(qapp, boxes):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    as_role("weigher", CASHIER_A)
    tid, _l = _priced_ticket()
    W.submit_ticket(tid)
    d = WeighTicketReviewDialog(tid, mode="weigher")
    d._cancel_ticket()
    assert W.get_ticket(tid)["status"] == "cancelled" and "Removed by the weigher" in W.get_ticket(tid)["cancel_reason"]

    tid2, line2 = _priced_ticket()
    W.submit_ticket(tid2)
    as_role("admin", ADMIN_ID)
    W.finalize_ticket(tid2, {line2: 450})
    as_role("weigher", CASHIER_A)
    d2 = WeighTicketReviewDialog(tid2, mode="weigher")
    assert not d2.editable and not d2.btn_finalize.isVisible() and not d2.btn_reject.isEnabled()


def test_cashier_opens_intake_corrects_prices_finalizes_and_payment_flow_runs(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    from PySide2.QtWidgets import QApplication
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier"); line = W.add_line(tid, CROP_ID, "Gwarzo", "A")
    W.record_round(line, 3, 245); W.record_round(line, 2, 100)
    W.set_line_price(line, 400)                                   # the weigher's price
    W.submit_ticket(tid)
    before = stock(CROP_ID)

    as_role("cashier", CASHIER)
    paid = []
    monkeypatch.setattr(WeighTicketReviewDialog, "_process_payment", lambda self, res: paid.append(res))
    d = WeighTicketReviewDialog(tid)
    assert d.editable and d.lines_table.rowCount() == 1 and d.rounds_table.rowCount() == 2

    assert d._price_spins[line].value() == 400                   # the weigher's price arrives pre-filled
    d._price_spins[line].setValue(0)
    d.finalize()                                                  # price cleared -> refused
    assert any("Confirm the price" in m for _k, m in boxes) and W.get_ticket(tid)["status"] == "submitted"

    d.rounds_table.item(0, 2).setText("250")                      # verify & edit a weight
    QApplication.processEvents()
    assert W.get_ticket(tid)["total_kg"] == 350
    d.add_bags.setText("1"); d.add_kg.setText("50"); d._add_round()   # a missed round
    assert W.get_ticket(tid)["total_kg"] == 400 and d.rounds_table.rowCount() == 3

    d._price_spins[line].setValue(450)
    assert d.lbl_amount.text() == "₦180,000.00"
    d.finalize()
    t = W.get_ticket(tid)
    assert t["status"] == "finalized" and d.result_purchase_id == t["purchase_id"]
    assert paid and paid[0]["total_amount"] == 180000                # the payment step was handed over
    assert stock(CROP_ID)[1] == before[1] + 400
    with get_session() as s:
        assert float(s.get(Purchase, t["purchase_id"]).total_amount) == 180000

    d2 = WeighTicketReviewDialog(tid)                              # finalized: read-only
    assert not d2.editable and not d2.btn_finalize.isVisible() and not d2.btn_add_round.isEnabled()


def test_reject_at_intake_cancels_the_ticket(qapp, boxes):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier"); line = W.add_line(tid, CROP_ID)
    W.record_round(line, 1, 10); W.set_line_price(line, 100); W.submit_ticket(tid)
    as_role("cashier", CASHIER)
    d = WeighTicketReviewDialog(tid)
    d._cancel_ticket()
    assert W.get_ticket(tid)["status"] == "cancelled"


# ---------------------------------------------------------------- roles & launcher
def test_workspace_permissions_by_role():
    assert workspace_allowed("weigher", "weigher") and not workspace_allowed("weigher", "pm")
    assert not workspace_allowed("weigher", "cashier")
    assert workspace_allowed("cashier", "cashier") and not workspace_allowed("cashier", "weigher")
    assert all(workspace_allowed("admin", k) for k in ("pm", "cashier", "weigher"))


def test_a_weigher_user_can_be_created_and_has_no_admin_powers():
    as_role("admin", ADMIN_ID)
    uid = UserAdminService.create_user(username="scale_bala", password="secret1", role_name="weigher")
    assert uid
    assert ("scale_bala" in [u[1] for u in UserAdminService.list_users(role_name="weigher")])
    as_role("weigher", uid)
    with pytest.raises(PermissionDenied):
        UserAdminService.create_user(username="sneaky", password="secret1", role_name="admin")
    with pytest.raises(PermissionDenied):
        W.finalize_ticket(1, {})


def test_launcher_routes_a_weigher_straight_to_their_workspace(qapp):
    from apps.product_management_app.launcher import LauncherView
    from database.services.auth_service import AuthService
    from shared.auth.session import set_session
    as_role("admin", ADMIN_ID)
    UserAdminService.create_user(username="scale_bala", password="secret1", role_name="weigher")
    set_session(None)
    seen = []
    lv = LauncherView()
    lv.workspace_selected.connect(seen.append)
    set_session(AuthService.authenticate(username="scale_bala", password="secret1"))
    lv._refresh_ui()
    assert seen == ["weigher"]
    assert not lv.card_pm.isVisibleTo(lv) and not lv.card_cashier.isVisibleTo(lv)
    as_role("admin", ADMIN_ID)
    lv._refresh_ui()
    assert all(c.isVisibleTo(lv) for c in (lv.card_pm, lv.card_cashier, lv.card_weigher))


def test_main_window_refuses_a_workspace_the_role_may_not_open(qapp, boxes):
    from apps.product_management_app.main import ProductManagementMainWindow
    win = ProductManagementMainWindow()
    as_role("weigher", CASHIER_A)
    win.open_workspace("pm")
    assert win.active_workspace_key is None and any("cannot open" in m for _k, m in boxes)
    win.open_workspace("weigher")
    assert win.active_workspace_key == "weigher"
    as_role("cashier", CASHIER)
    win.open_workspace("weigher")
    assert win.active_workspace_key == "weigher"                   # unchanged: refused
    win.open_workspace("cashier")
    assert win.active_workspace_key == "cashier"


# ---------------------------------------------------------------- the typed rounds table
def test_parse_bags_and_kg():
    from apps.product_management_app.features.weigher.views.weigher_station import parse_bags, parse_kg
    assert [parse_bags(x) for x in ("3", "3bags", "1 bag", " 2 Bags ", "0.5", "0,5")] == [3, 3, 1, 2, 0.5, 0.5]
    assert parse_bags("abc") is None and parse_bags("") is None and parse_bags("3 sacks") is None
    assert parse_kg("245.5") == 245.5 and parse_kg("1,250.5kg") == 1250.5 and parse_kg("x") is None


def test_a_half_typed_row_records_nothing_until_both_cells_are_filled(qapp, boxes):
    from PySide2.QtWidgets import QApplication
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    t = v.rounds_table
    t.item(0, 1).setText("3")
    QApplication.processEvents()
    assert W.get_ticket(v.ticket_id)["total_kg"] == 0 and t.rowCount() == 1
    t.item(0, 2).setText("245.5")
    QApplication.processEvents(); QApplication.processEvents()
    assert W.get_ticket(v.ticket_id)["total_kg"] == 245.5 and t.rowCount() == 2
    t.item(1, 1).setText("two"); t.item(1, 2).setText("10")      # not a number of bags
    QApplication.processEvents()
    assert any("number" in m for _k, m in boxes) and W.get_ticket(v.ticket_id)["total_kg"] == 245.5


def test_a_recorded_round_can_be_corrected_in_the_table(qapp, boxes):
    from PySide2.QtWidgets import QApplication
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    _type_round(v, "3", "245")
    v.rounds_table.item(0, 2).setText("250")                      # misread the scale
    QApplication.processEvents(); QApplication.processEvents()
    t = W.get_ticket(v.ticket_id)
    assert t["total_kg"] == 250 and v.lbl_total_kg.text() == "250.00 kg"
    v.rounds_table.item(0, 1).setText("4")                        # too many bags: refused, value restored
    QApplication.processEvents(); QApplication.processEvents()
    assert W.get_ticket(v.ticket_id)["total_bags"] == 3
    assert v.rounds_table.item(0, 1).text() == "3 bags"
