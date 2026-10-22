"""Receipts are never printed by themselves - every one has a button - plus the weigher's two receipts."""
import pytest
from PySide2.QtWidgets import QMessageBox

from apps.product_management_app.features.receipts_v2 import ShopInfo, render_weigh_slip
from apps.product_management_app.features.receipts_v2.receipt_render import WIDTH
from database.services.payment_service import PaymentService
from database.services.weigh_service import WeighService as W
from tests.conftest import ADMIN_ID, CASHIER_A, CUSTOMER_ID, SUPPLIER_ID, CROP_ID, make_sale
from tests.test_weigh_service import CASHIER, as_role

SHOP = ShopInfo(name="Alh. Zakari Rafawa Enterprises", address="Kwanar Tsaure, Gwarzo L.G.A, Kano State",
                phone="07046329444", email="x@y.z", footer_note="Thank you for trading with us")
TICKET = {
    "id": 128, "ticket_no": "WT-00128", "party_name": "Sharifadden", "party_phone": "0000050000",
    "party_role": "customer", "weigher_name": "Basiru Rafawa", "created_at": None, "total_bags": 205,
    "total_kg": 11970, "total_amount": 4878000, "payment": {"status": "Unpaid", "paid": 0, "balance": 4878000},
    "lines": [
        {"product_name": "Paddy Rice", "bags": 100, "kg": 5940, "unit_price": 400, "amount": 2376000, "rounds": [1]},
        {"product_name": "Maize", "bags": 60, "kg": 3420, "unit_price": 350, "amount": 1197000, "rounds": [1]},
        {"product_name": "Soybean", "bags": 45, "kg": 2610, "unit_price": 500, "amount": 1305000, "rounds": [1]}],
}


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.No))
    return shown


@pytest.fixture
def printed(monkeypatch):
    from apps.product_management_app.features.receipts_v2 import receipt_print
    out = []
    monkeypatch.setattr(receipt_print, "print_text", lambda parent, text, **k: out.append(text))
    return out


BOLD = "\x01"


def _plain(text):
    """What is on paper: the bold marks print nothing."""
    return text.replace(BOLD, "")


def _bold_words(text):
    """Everything the printer is told to print in bold."""
    return [seg.strip() for i, seg in enumerate(text.split(BOLD)) if i % 2 == 1]


def test_option_1_weighing_receipt_is_short_with_bold_product_names_and_one_signature():
    raw = render_weigh_slip(dict(TICKET, party_code="WT-WKN-00001"), SHOP, "weighing")
    t = _plain(raw)
    for needle in ("Alh. Zakari Rafawa Enterprises", "07046329444", "WT-00128", "Customer: Sharifadden (0000050000)",
                   "Walk-in ID: WT-WKN-00001", "Weigher: Basiru Rafawa", "Product", "Bags", "KG", "Paddy Rice",
                   "5,940", "TOTAL BAGS:", "205", "Weigher sign:", "Thank you for trading with us"):
        assert needle in t, needle
    assert "Price" not in t and "Payment" not in t and "Received by" not in t and "Kwanar Tsaure" not in t
    assert "Paddy Rice" in _bold_words(raw) and "Soybean" in _bold_words(raw)
    assert len(t.splitlines()) <= 18 and all(len(line) <= WIDTH for line in t.splitlines())     # was ~35 lines


def test_option_2_full_value_receipt_is_short_with_bold_products_and_balance():
    pay = {"status": "Part paid", "paid": 1000000, "balance": 3878000, "total": 4878000}
    raw = render_weigh_slip(dict(TICKET, payment=pay), SHOP, "value")
    t = _plain(raw)
    for needle in ("WEIGHING RECEIPT", "Product", "Price", "Amount", "2,376,000", "1,197,000", "TOTAL:", "N4,878,000",
                   "Paid:", "N1,000,000", "BALANCE:", "N3,878,000", "Status: Part paid", "Cashier:", "Customer:"):
        assert needle in t, needle
    bold = _bold_words(raw)
    assert "Paddy Rice" in bold and any(b.startswith("BALANCE:") and "N3,878,000" in b for b in bold)
    assert len(t.splitlines()) <= 22 and all(len(line) <= WIDTH for line in t.splitlines())
    assert "400" in t and "2,376,000" in t and "4002,376,000" not in t                       # columns never run together
    sup = dict(TICKET, party_role="supplier")
    assert "Supplier: Sharifadden" in _plain(render_weigh_slip(sup, SHOP, "weighing"))


def test_the_value_receipt_shows_the_real_payment_after_finalizing():
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line = W.add_line(tid, CROP_ID, area="Gwarzo", grade="A")
    W.record_round(line, 2, 100)
    W.set_line_price(line, 400)
    W.submit_ticket(tid, "value")
    assert W.get_ticket(tid)["payment"] == {"status": "Unpaid", "paid": 0.0, "balance": 40000.0, "total": 40000.0}
    as_role("cashier", CASHIER)
    res = W.finalize_ticket(tid, {line: 400})
    PaymentService.record_purchase_payment(res["purchase_id"], SUPPLIER_ID, 15000, CASHIER, "cash")
    t = W.get_ticket(tid)
    assert t["payment"]["status"] == "Part paid" and t["payment"]["paid"] == 15000 and t["payment"]["balance"] == 25000
    text = _plain(render_weigh_slip(t, SHOP, "value"))
    assert "Part paid" in text and "N15,000" in text and "N25,000" in text


def test_admin_ticket_queue_prints_the_weigher_receipts_and_the_purchase_receipt_only_on_click(qapp, boxes, printed,
                                                                                               monkeypatch):
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    import apps.product_management_app.features.receipts_v2 as r2
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line = W.add_line(tid, CROP_ID, area="Gwarzo", grade="A")
    W.record_round(line, 2, 100)
    W.set_line_price(line, 400)
    W.submit_ticket(tid)
    as_role("cashier", CASHIER)
    v = WeighTicketsAdminView(mode="cashier")
    assert printed == [] and v.btn_print.isEnabled() and not v.btn_print_purchase.isEnabled()   # not finalized yet
    v.btn_print_value.click()
    assert len(printed) == 1 and "WEIGHING RECEIPT" in _plain(printed[0]) and "OPTION 2" not in printed[0]
    res = W.finalize_ticket(tid, {line: 400})
    v.filter_seg.set_key("finalized")
    v.refresh()
    bought = []
    monkeypatch.setattr(r2, "print_receipt", lambda parent, doc_type, entity_id, mode, **k: bought.append((doc_type, entity_id)))
    assert v.btn_print_purchase.isEnabled()
    v.btn_print_purchase.click()
    assert bought == [(r2.PURCHASE, res["purchase_id"])]


def test_finalizing_an_intake_does_not_print_the_purchase_receipt(qapp, boxes, printed, monkeypatch):
    from apps.product_management_app.features.weigher.dialogs import weigh_ticket_review_dialog as d
    import apps.product_management_app.features.receipts_v2 as r2
    from apps.product_management_app.features.profiling.dialogs import purchase_payment_dialog as ppd
    import apps.product_management_app.features.profiling.dialogs.advance_choice_dialog as adv
    spy = []
    monkeypatch.setattr(r2, "print_full_then_optional_delivery", lambda *a, **k: spy.append(a))
    monkeypatch.setattr(ppd.PurchasePaymentDialog, "exec_", lambda self: 1)
    monkeypatch.setattr(adv, "prompt_and_apply_advance", lambda *a, **k: 0)
    as_role("weigher", CASHIER_A)
    tid = W.create_ticket(SUPPLIER_ID, "supplier")
    line = W.add_line(tid, CROP_ID, area="Gwarzo", grade="A")
    W.record_round(line, 2, 100)
    W.set_line_price(line, 400)
    W.submit_ticket(tid)
    as_role("cashier", CASHIER)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))   # confirm finalizing
    dlg = d.WeighTicketReviewDialog(tid)
    dlg.finalize()
    assert W.get_ticket(tid)["status"] == "finalized" and spy == [] and printed == []
    dlg._t = W.get_ticket(tid)
    dlg.btn_print_value.click()                                    # the receipts are one click away
    assert len(printed) == 1 and "WEIGHING RECEIPT" in printed[0] and "OPTION 2" not in printed[0] and "Unpaid" in printed[0]


def test_documents_tab_and_intake_list_print_only_when_asked(qapp, boxes, monkeypatch):
    import apps.product_management_app.features.receipts_v2 as r2
    from apps.product_management_app.features.profiling.views.records_tabs import DocumentsTab
    from apps.product_management_app.features.cashier.views import cashier_intake as ci
    calls = []
    monkeypatch.setattr(r2, "print_full_then_optional_delivery", lambda parent, doc_type, entity_id, **k: calls.append((doc_type, entity_id)))
    sale = make_sale(buyer=CUSTOMER_ID)
    tab = DocumentsTab(CUSTOMER_ID, "sale")
    assert calls == []
    tab.btn_print.click()
    assert calls == [] and boxes[-1][0] == "information"          # no row clicked: nothing printed
    tab.table.selectRow(0)
    tab.btn_print.click()
    assert calls == [(r2.SALE, int(sale["sale_id"]))]

    monkeypatch.setattr(ci, "print_full_then_optional_delivery", lambda parent, doc_type, entity_id, **k: calls.append((doc_type, entity_id)))
    v = ci.CashierIntakeView()
    v.table.setRowCount(1)
    from PySide2.QtWidgets import QTableWidgetItem
    v.table.setItem(0, 1, QTableWidgetItem("42"))
    v.table.selectRow(0)
    v.btn_print.click()
    assert calls[-1] == (r2.PURCHASE, 42) and not v.btn_print_swap.isEnabled()
    v._last_swap = {"purchase_id": 7, "sale_id": 9}
    v.btn_print_swap.setEnabled(True)
    v.btn_print_swap.click()
    assert calls[-2:] == [(r2.PURCHASE, 7), (r2.SALE, 9)]


def test_quick_purchases_screen_and_admin_menu(qapp, boxes, monkeypatch):
    import apps.product_management_app.features.receipts_v2 as r2
    from apps.product_management_app.features.cashier.views.quick_purchases import QuickPurchasesView
    from apps.product_management_app.workspaces import build_workspaces
    pm = build_workspaces()["pm"]
    assert "admin.quick_purchases" in pm.routes and pm.title_map["admin.quick_purchases"] == "Quick Purchases"
    assert any(i.route == "admin.quick_purchases" for i in pm.menu)
    v = pm.routes["admin.quick_purchases"]()
    assert isinstance(v, QuickPurchasesView) and not v.btn_print_sale.isEnabled() and not v.btn_print_buy.isEnabled()
    calls = []
    monkeypatch.setattr(r2, "print_full_then_optional_delivery", lambda parent, doc_type, entity_id, **k: calls.append((doc_type, entity_id)))
    from database.services.quick_purchase_order_service import QuickPurchaseOrderService as Q
    from tests.conftest import FERT_ID
    cart = [{"product_id": FERT_ID, "product_type": "fertilizer", "name": "NPK", "unit_price": 10000,
             "unit_cost_at_sale": 9000, "qty_bags": 10, "total_weight_kg": 0}]
    oid = Q.create([{"product_id": FERT_ID, "qty_bags": 10, "unit_cost": 9300, "supplier_id": SUPPLIER_ID}], send=True,
                   cart=cart, buyer_party_id=CUSTOMER_ID)
    res = Q.confirm(oid, {FERT_ID: 10000}, buyer_party_id=CUSTOMER_ID)
    v.refresh()
    v.table.selectRow(0)
    assert v.btn_print_sale.isEnabled() and v.btn_print_buy.isEnabled() and calls == []
    v.btn_print_sale.click()
    v.btn_print_buy.click()
    assert calls == [(r2.SALE, res["sale_id"]), (r2.PURCHASE, res["purchases"][0])]


def test_option_3_payment_receipt_is_small_with_bold_products_and_balance():
    raw = render_weigh_slip(dict(TICKET, payment={"status": "Part paid", "paid": 1000000, "balance": 3878000,
                                                  "total": 4878000}), SHOP, "payment")
    t = _plain(raw)
    for needle in ("PAYMENT RECEIPT", "WT-00128", "Sharifadden", "Paddy Rice", "N2,376,000", "Maize", "Soybean",
                   "Total:", "N4,878,000", "PAID:", "N1,000,000", "BALANCE:", "N3,878,000", "Part paid", "Signature:"):
        assert needle in t, needle
    assert "Price/KG" not in t and "Round" not in t
    bold = _bold_words(raw)
    assert {"Paddy Rice", "Maize", "Soybean"} <= set(bold) and any(b.startswith("BALANCE:") for b in bold)
    assert len(t.splitlines()) <= 22 and all(len(line) <= WIDTH for line in t.splitlines())
