"""Editing the price of a purchase or sale already recorded: per product, additive, audited, balances follow."""
import pytest

from database.models.audit_log import AuditLog
from database.models.purchase_price_adjustment import PurchasePriceAdjustment, PurchasePriceAdjustmentLine
from database.models.sale_discount import SaleDiscount, SalePriceAdjustmentLine
from database.services.document_balance import document_balances
from database.services.payment_service import PaymentService
from database.services.purchase_adjustment_service import PurchaseAdjustmentService as A
from database.services.purchase_service import PurchaseService
from database.services.sale_adjustment_service import SaleAdjustmentService as SA
from database.session import get_session
from shared.auth.permissions import PermissionDenied
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CROP_ID, CUSTOMER_ID, FERT_ID, SUPPLIER_ID, make_sale


def _purchase():                                    # Maize 1000 kg x 50 = 50,000 ; NPK 10 bags x 9,000 = 90,000
    return PurchaseService.record({"supplier_party_id": SUPPLIER_ID, "created_by_user_id": CASHIER_A, "items": [
        {"sku": "CROP-MAIZE", "product_name": "Maize", "product_type": "crops", "qty_bags": 10,
         "total_weight_kg": 1000, "unit_cost": 50},
        {"sku": "FERT-NPK", "product_name": "NPK", "product_type": "fertilizer", "qty_bags": 10,
         "total_weight_kg": 0, "unit_cost": 9000}]})["purchase_id"]


def _sale(buyer=SUPPLIER_ID):                       # Maize 200 kg x 100 = 20,000 ; NPK 5 bags x 10,000 = 50,000 -> 70,000
    return make_sale([
        {"product_id": CROP_ID, "qty_bags": 2, "total_weight_kg": 200, "unit_price": 100},
        {"product_id": FERT_ID, "qty_bags": 5, "total_weight_kg": 0, "unit_price": 10000},
    ], buyer=buyer)["sale_id"]


def _balance(pid):
    with get_session() as s:
        b = document_balances(s, "purchase", [pid])[pid]
        return b.net_total, b.balance


def _sale_balance(sid):
    with get_session() as s:
        b = document_balances(s, "sale", [sid])[sid]
        return b.net_total, b.balance


def _ids(pid):
    return {l["product"]: l["item_id"] for l in A.line_prices(pid)}


def _sale_ids(sid):
    return {l["product"]: l["item_id"] for l in SA.line_prices(sid)}


def test_lines_show_each_products_price_and_basis():
    pid = _purchase()
    lines = {l["product"]: l for l in A.line_prices(pid)}
    assert (lines["Maize"]["unit"], lines["Maize"]["qty"], lines["Maize"]["rate"], lines["Maize"]["amount"]) == ("kg", 1000, 50, 50000)
    assert (lines["NPK"]["unit"], lines["NPK"]["qty"], lines["NPK"]["rate"], lines["NPK"]["amount"]) == ("bag", 10, 9000, 90000)


def test_edit_one_price_raises_the_total_and_keeps_the_original_rows():
    pid = _purchase()
    ids = _ids(pid)
    aid = A.edit_prices(pid, {ids["Maize"]: 52.5}, ADMIN_ID, "supplier asked for more")
    assert _balance(pid) == (142500, 142500)                               # +1000 x 2.50
    line = {l["product"]: l for l in A.line_prices(pid)}["Maize"]
    assert line["rate"] == 52.5 and line["original_rate"] == 50 and line["amount"] == 52500
    with get_session() as s:
        adj = s.get(PurchasePriceAdjustment, aid)
        assert float(adj.amount) == 2500 and "Maize" in adj.reason and "supplier asked for more" in adj.reason
        rows = s.query(PurchasePriceAdjustmentLine).filter_by(adjustment_id=aid).all()
        assert len(rows) == 1 and float(rows[0].old_rate) == 50 and float(rows[0].new_rate) == 52.5
        assert s.query(AuditLog).filter(AuditLog.action == "purchase.price_edit").count() == 1


def test_edit_sale_price_updates_total_and_keeps_original_rows():
    sid = _sale()
    ids = _sale_ids(sid)
    did = SA.edit_prices(sid, {ids["Maize"]: 110}, ADMIN_ID, "customer grade adjustment")
    assert _sale_balance(sid) == (72000, 72000)                            # +200 x 10.00
    line = {l["product"]: l for l in SA.line_prices(sid)}["Maize"]
    assert line["rate"] == 110 and line["original_rate"] == 100 and line["amount"] == 22000
    with get_session() as s:
        disc = s.get(SaleDiscount, did)
        assert float(disc.amount) == -2000 and "Maize" in disc.reason and "customer grade adjustment" in disc.reason
        rows = s.query(SalePriceAdjustmentLine).filter_by(discount_id=did).all()
        assert len(rows) == 1 and float(rows[0].old_rate) == 100 and float(rows[0].new_rate) == 110
        assert s.query(AuditLog).filter(AuditLog.action == "sale.price_edit").count() == 1


def test_a_second_edit_starts_from_the_price_now_in_force():
    pid = _purchase()
    ids = _ids(pid)
    A.edit_prices(pid, {ids["Maize"]: 52}, ADMIN_ID)
    A.edit_prices(pid, {ids["Maize"]: 51}, ADMIN_ID)                       # back down by 1.00 x 1000
    assert _balance(pid)[0] == 141000
    assert {l["product"]: l["rate"] for l in A.line_prices(pid)}["Maize"] == 51


def test_several_products_in_one_edit_and_untouched_ones_stay():
    pid = _purchase()
    ids = _ids(pid)
    A.edit_prices(pid, {ids["Maize"]: 55, ids["NPK"]: 9000}, ADMIN_ID)      # NPK unchanged -> ignored
    assert _balance(pid)[0] == 145000
    with get_session() as s:
        assert s.query(PurchasePriceAdjustmentLine).count() == 1


def test_refusals_change_nothing():
    pid = _purchase()
    ids = _ids(pid)
    with pytest.raises(ValueError):
        A.edit_prices(pid, {ids["Maize"]: 50}, ADMIN_ID)                   # nothing changed
    with pytest.raises(ValueError):
        A.edit_prices(pid, {ids["Maize"]: 0}, ADMIN_ID)                    # not a price
    with pytest.raises(ValueError):
        A.edit_prices(pid, {ids["Maize"]: "abc"}, ADMIN_ID)
    with pytest.raises(ValueError):
        A.edit_prices(pid, {999999: 10}, ADMIN_ID)                         # not on this purchase
    PaymentService.record_purchase_payment(pid, SUPPLIER_ID, 100000, ADMIN_ID, method="cash")
    with pytest.raises(ValueError):
        A.edit_prices(pid, {ids["NPK"]: 4000}, ADMIN_ID)                   # would drop below what is paid
    assert _balance(pid)[0] == 140000
    with get_session() as s:
        assert s.query(PurchasePriceAdjustment).count() == 0


def test_a_fully_paid_purchase_owes_again_after_a_price_rise():
    pid = _purchase()
    PaymentService.record_purchase_payment(pid, SUPPLIER_ID, 140000, ADMIN_ID, method="cash")
    assert _balance(pid)[1] == 0
    A.edit_prices(pid, {_ids(pid)["Maize"]: 51}, ADMIN_ID)
    assert _balance(pid) == (141000, 1000)


def _login(role, uid):
    set_session(UserSession(user_id=uid, username=f"{role}{uid}", role=role))


def test_a_weigher_may_edit_only_the_purchase_of_their_own_ticket():
    pid = _purchase()
    ids = _ids(pid)
    _login("weigher", 7)
    with pytest.raises(PermissionDenied):
        A.edit_prices(pid, {ids["Maize"]: 51}, 7)                          # not from one of their tickets
    assert _balance(pid)[0] == 140000


# ---------------------------------------------------------------- the screens
def test_right_click_edit_on_the_purchases_and_sales_tab(qapp, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs import purchase_price_edit_dialog as ped
    from apps.product_management_app.features.profiling.views.records_tabs import SalesPurchasesTab
    pid = _purchase()
    sid = _sale(SUPPLIER_ID)
    tab = SalesPurchasesTab(SUPPLIER_ID)
    assert tab.purchases.btn_edit.isVisibleTo(tab) or not tab.purchases.btn_edit.isHidden()
    assert tab.sales.btn_edit.isVisibleTo(tab) or not tab.sales.btn_edit.isHidden()
    seen = {}

    def fake_exec(self):
        seen["lines"] = [(l["product"], l["rate"]) for l in self.lines]
        self.edits[0].setText("52.5" if self.kind == "purchase" else "110")
        assert self.btn_save.isEnabled()
        self.reason.setText("adjusted")
        self._save()
        return self.Accepted

    monkeypatch.setattr(ped.PurchasePriceEditDialog, "exec_", fake_exec)
    changed = []
    tab.changed.connect(lambda: changed.append(1))
    tab.purchases.table.setCurrentCell(0, 1)
    assert tab.purchases.edit_prices() is True
    assert seen["lines"] == [("Maize", 50.0), ("NPK", 9000.0)] and changed == [1]
    assert _balance(pid)[0] == 142500
    assert tab.purchases.table.item(0, 4).text() == "142,500.00"          # the row follows the new total

    tab.sales.table.setCurrentCell(0, 1)
    assert tab.sales.edit_prices() is True
    assert _sale_balance(sid)[0] == 72000
    assert tab.sales.table.item(0, 4).text() == "72,000.00"


def test_edit_prices_across_customer_profile_tabs_and_payment_dialogs(qapp, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs import purchase_price_edit_dialog as ped
    from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog
    from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import SalePaymentDialog
    from apps.product_management_app.features.profiling.views.customer_profile import CustomerProfileView

    pid = _purchase()
    sid = _sale(SUPPLIER_ID)
    PaymentService.record_purchase_payment(pid, SUPPLIER_ID, 10000, ADMIN_ID, method="cash")
    PaymentService.record_sale_payment(sid, SUPPLIER_ID, 5000, ADMIN_ID, method="cash")

    view = CustomerProfileView()
    view.load_party(SUPPLIER_ID)

    rate_step = {"purchase": 51.0, "sale": 105.0}

    def fake_exec(self):
        r = rate_step[self.kind]
        rate_step[self.kind] = r + 1.0
        self.edits[0].setText(str(r))
        self.reason.setText("row price edit")
        self._save()
        return self.Accepted

    monkeypatch.setattr(ped.PurchasePriceEditDialog, "exec_", fake_exec)

    # 1. Activity table (edit first purchase or sale row)
    view.activity_table.setCurrentCell(0, 0)
    assert view.edit_row_prices(view.activity_table) is True

    # 2. Crops table
    view.crops_table.setCurrentCell(0, 0)
    assert view.edit_row_prices(view.crops_table) is True

    # 3. Fertilizers table
    view.fertilizers_table.setCurrentCell(0, 0)
    assert view.edit_row_prices(view.fertilizers_table) is True

    # 4. Payments table (payment row resolves to its parent purchase/sale document)
    view.payments_table.setCurrentCell(0, 0)
    assert view.edit_row_prices(view.payments_table) is True

    # 5. Products tab
    view.tab_products.table.setCurrentCell(0, 0)
    assert view.tab_products.edit_prices() is True

    # 6. History tab
    view.tab_history.table.setCurrentCell(0, 0)
    assert view.tab_history.edit_prices() is True

    # 7. PurchasePaymentDialog & SalePaymentDialog
    pdlg = PurchasePaymentDialog(None, party_id=SUPPLIER_ID, party_name="Supplier One", user_id=ADMIN_ID)
    pdlg.table.setCurrentCell(0, 0)
    assert pdlg.edit_prices() is True

    sdlg = SalePaymentDialog(None, party_id=SUPPLIER_ID, party_name="Supplier One", user_id=ADMIN_ID)
    sdlg.table.setCurrentCell(0, 0)
    assert sdlg.edit_prices() is True


def test_edit_dialog_will_not_confirm_an_unchanged_or_bad_price(qapp):
    from apps.product_management_app.features.profiling.dialogs.purchase_price_edit_dialog import PurchasePriceEditDialog
    pid = _purchase()
    dlg = PurchasePriceEditDialog(None, purchase_id=pid, ref=f"PUR-{pid}", user_id=ADMIN_ID)
    assert not dlg.btn_save.isEnabled()                                   # nothing changed yet
    dlg.edits[0].setText("abc")
    assert not dlg.btn_save.isEnabled() and "number" in dlg.lbl.text()
    dlg.edits[0].setText("0")
    assert not dlg.btn_save.isEnabled()
    dlg.edits[0].setText("51")
    assert dlg.btn_save.isEnabled() and dlg.new_rates() != {}


# ---------------------------------------------------------------- the weigher
def _finalized_ticket(price=450, kg=100):
    from database.services.weigh_service import WeighService as W
    from tests.test_weigh_service import CASHIER, WEIGHER, as_role
    as_role("weigher", WEIGHER)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line = W.add_line(tid, 1, area="Gwarzo", grade="A")
    W.record_round(line, 3, kg)
    W.set_line_price(line, price)
    W.submit_ticket(tid, "full")
    as_role("cashier", CASHIER)
    return tid, W.finalize_ticket(tid)["purchase_id"]


def test_weigher_edits_the_price_of_their_own_finalized_weighing_and_the_ticket_follows():
    from database.services.weigh_service import WeighService as W
    from tests.test_weigh_service import WEIGHER, as_role
    tid, pid = _finalized_ticket()                                         # 100 kg x 450 = 45,000
    as_role("weigher", WEIGHER)
    item = A.line_prices(pid)[0]["item_id"]
    A.edit_prices(pid, {item: 460}, WEIGHER, "a little more")
    t = W.get_ticket(tid)
    assert t["lines"][0]["unit_price"] == 460 and t["lines"][0]["amount"] == 46000 and t["total_amount"] == 46000
    assert t["payment"]["total"] == 46000
    assert _balance(pid)[0] == 46000


def test_another_weigher_cannot_edit_it():
    from tests.test_weigh_service import WEIGHER_2, as_role
    _tid, pid = _finalized_ticket()
    as_role("weigher", WEIGHER_2)
    with pytest.raises(PermissionDenied):
        A.edit_prices(pid, {A.line_prices(pid)[0]["item_id"]: 460}, WEIGHER_2)


def test_finalize_list_edit_on_a_finalized_weighing_opens_the_price_editor(qapp, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs import purchase_price_edit_dialog as ped
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    from tests.test_weigh_service import WEIGHER, as_role
    tid, pid = _finalized_ticket()
    as_role("weigher", WEIGHER)
    v = WeigherTicketsView()
    v.table.setCurrentCell(0, 0)
    assert v.btn_edit.isEnabled() and v.btn_edit.text() == "Edit prices"

    def fake_exec(self):
        self.edits[0].setText("470")
        self._save()
        return self.Accepted

    monkeypatch.setattr(ped.PurchasePriceEditDialog, "exec_", fake_exec)
    v.btn_edit.click()
    assert _balance(pid)[0] == 47000
    assert v.table.item(0, 6).text() == "47,000.00"                        # the list shows the new amount


def test_admin_queue_has_edit_prices_for_a_finalized_ticket(qapp, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs import purchase_price_edit_dialog as ped
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    from tests.test_weigh_service import as_role
    _tid, pid = _finalized_ticket()
    as_role("admin", ADMIN_ID)
    v = WeighTicketsAdminView("admin")
    v.filter_seg.set_key("finalized")
    v.refresh()
    v.table.setCurrentCell(0, 0)
    assert v.btn_edit_prices.isEnabled()
    monkeypatch.setattr(ped.PurchasePriceEditDialog, "exec_",
                        lambda self: (self.edits[0].setText("455"), self._save(), self.Accepted)[-1])
    v.btn_edit_prices.click()
    assert _balance(pid)[0] == 45500
