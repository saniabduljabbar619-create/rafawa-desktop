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

    v.bag_buttons[3].setChecked(True)
    v.weight_in.setText("245.5"); v.record_round()
    v.bag_buttons[2].setChecked(True)
    v.weight_in.setText("160"); v.record_round()
    v.btn_partial.setChecked(True); v.partial_spin.setValue(0.5)
    v.weight_in.setText("21.25"); v.record_round()
    assert v.lbl_total_bags.text() == "5.5" and v.lbl_total_kg.text() == "426.75 kg"
    assert v.rounds_table.rowCount() == 3 and v.weight_in.text() == ""

    v.weight_in.setText("0"); v.record_round()                  # zero weight is refused
    assert any("Weight must be more than zero" in m for _k, m in boxes)

    v.rounds_table.selectRow(2)                                  # a slip of the finger: remove round 3
    v.remove_round()
    assert v.lbl_total_kg.text() == "405.50 kg"

    v.btn_finish.click()
    assert v.stack.currentIndex() == 2
    assert "TOTAL WEIGHT" in v.preview.toPlainText() and "245.50" in v.preview.toPlainText()
    v.receipt_seg.set_key("summary"); v._render_preview()
    assert "245.50" not in v.preview.toPlainText()

    v.btn_submit.click()
    t = W.get_ticket(v.ticket_id)
    assert t["status"] == "submitted" and t["receipt_type"] == "summary" and len(printed) == 1
    assert "Weigher: ____" in printed[0]
    assert v.btn_submit.isHidden() and not v.btn_new.isHidden() and not v.btn_print.isHidden()
    v.btn_new.click()
    assert v.stack.currentIndex() == 0 and v.ticket_id == 0


def test_station_resume_and_cancel(qapp, boxes):
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    tid = v.ticket_id
    v.product_cb.setCurrentIndex(1); v.add_line()
    v.weight_in.setText("50"); v.record_round()
    v.new_weighing()
    assert v.open_list.count() == 1
    v.open_list.setCurrentRow(0)
    v.btn_resume.click()
    assert v.ticket_id == tid and v.rounds_table.rowCount() == 1 and v.stack.currentIndex() == 1
    v.btn_cancel_ticket.click()
    assert W.get_ticket(tid)["status"] == "cancelled" and v.stack.currentIndex() == 0


def test_finish_needs_a_round_and_oversized_rounds_are_refused(qapp, boxes):
    v = _station()
    _pick(v, "Supplier One"); v.start_ticket()
    v.product_cb.setCurrentIndex(1); v.add_line()
    v.btn_finish.click()
    assert v.stack.currentIndex() == 1 and any("at least one round" in m for _k, m in boxes)
    v.btn_partial.setChecked(True)
    assert v.partial_spin.maximum() == 3                          # a partial can never reach a 4th bag


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


def test_weigher_history_shows_no_money_and_admin_queue_does(qapp, boxes, printed):
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier"); line = W.add_line(tid, CROP_ID)
    W.record_round(line, 2, 100); W.submit_ticket(tid)

    mine = WeigherTicketsView()
    headers = [mine.table.horizontalHeaderItem(i).text() for i in range(mine.table.columnCount())]
    assert "Amount" not in headers and mine.table.rowCount() == 1 and mine.kpi_wait.value.text() == "1"
    mine.table.selectRow(0); mine.print_selected()
    assert len(printed) == 1 and "WEIGHING SLIP" in printed[0]

    as_role("cashier", CASHIER)
    q = WeighTicketsAdminView(mode="cashier")
    headers = [q.table.horizontalHeaderItem(i).text() for i in range(q.table.columnCount())]
    assert "Amount" in headers and "Weigher" in headers
    assert q.table.rowCount() == 1 and q.kpi_wait.value.text() == "1"
    q.filter_seg.set_key("finalized"); q.refresh()
    assert q.table.rowCount() == 0


def test_cashier_opens_intake_corrects_prices_finalizes_and_payment_flow_runs(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.weigher.dialogs.weigh_ticket_review_dialog import WeighTicketReviewDialog
    from PySide2.QtWidgets import QApplication
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier"); line = W.add_line(tid, CROP_ID, "Gwarzo", "A")
    W.record_round(line, 3, 245); W.record_round(line, 2, 100)
    W.submit_ticket(tid)
    before = stock(CROP_ID)

    as_role("cashier", CASHIER)
    paid = []
    monkeypatch.setattr(WeighTicketReviewDialog, "_process_payment", lambda self, res: paid.append(res))
    d = WeighTicketReviewDialog(tid)
    assert d.editable and d.lines_table.rowCount() == 1 and d.rounds_table.rowCount() == 2

    d.finalize()                                                  # no price yet -> refused
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
    W.record_round(line, 1, 10); W.submit_ticket(tid)
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
