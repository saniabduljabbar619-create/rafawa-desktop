"""Swap dialog, the 'settle with this purchase?' prompt, and the worksheet's net-off / reverse."""
import pytest
from PySide2.QtWidgets import QMessageBox

from database.models.party import Party
from database.services.document_balance import document_balances
from database.session import get_session
from tests.conftest import CUSTOMER_ID, FERT_ID, SUPPLIER_ID, make_sale
from tests.test_offsets import _p


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, _n=n, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Yes))
    return shown


def _bal(kind, doc):
    with get_session() as s:
        return document_balances(s, kind, [doc])[doc]


def test_prompt_settles_unpaid_sales_when_accepted(qapp, boxes):
    sale = make_sale()["sale_id"]
    p = _p(CUSTOMER_ID, 500, 100)
    from apps.product_management_app.features.profiling.dialogs.offset_choice_dialog import prompt_and_apply_offset
    left = prompt_and_apply_offset(None, party_id=CUSTOMER_ID, purchase_id=p, party_name="Customer One")
    assert left == 30000 and _bal("sale", sale).balance == 0
    assert "still owes us 20,000.00" in boxes[0][1] and "still owe them 30,000.00" in boxes[0][1]


def test_prompt_is_silent_when_nothing_is_owed_and_respects_no(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.profiling.dialogs.offset_choice_dialog import prompt_and_apply_offset
    p = _p(SUPPLIER_ID, 500, 100)
    assert prompt_and_apply_offset(None, party_id=SUPPLIER_ID, purchase_id=p) == 50000 and boxes == []
    sale = make_sale(buyer=SUPPLIER_ID)["sale_id"]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert prompt_and_apply_offset(None, party_id=SUPPLIER_ID, purchase_id=p) == 50000
    assert _bal("sale", sale).balance == 20000                     # declined -> nothing applied


def test_swap_dialog_records_and_shows_the_difference(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.dialogs import swap_dialog as sd
    dlg = sd.SwapDialog(None, party_id=CUSTOMER_ID, party_name="Customer One")
    monkeypatch.setattr(sd.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Crops", True)))
    monkeypatch.setattr(sd, "capture_intake_line", lambda parent, kind: {
        "product_name": "Maize", "sku": "CROP-MAIZE", "product_type": "crops", "qty_bags": 2,
        "total_weight_kg": 100.0, "unit_cost": 100.0})
    dlg._add_recv()
    assert dlg.lbl_result.text() == "" and dlg.totals() == (10000.0, 0.0)

    gd = sd.GiveItemDialog()
    gd.product.setCurrentIndex(gd.product.findData(FERT_ID))
    assert gd.price.value() == 10000 and not gd.kg.isEnabled() and "50 bags in stock" in gd.product.currentText()
    gd.bags.setValue(2)
    assert gd.lbl_total.text() == "20,000.00"
    chosen = gd.line()
    monkeypatch.setattr(sd.GiveItemDialog, "exec_", lambda self: 1)
    monkeypatch.setattr(sd.GiveItemDialog, "line", lambda self: chosen)
    dlg._add_give()
    assert dlg.lbl_result.text() == "He owes us 10,000.00"
    dlg.btn_save.click()
    r = dlg.result_data
    assert r["he_owes_us"] == 10000 and _bal("sale", r["sale_id"]).balance == 10000
    assert _bal("purchase", r["purchase_id"]).status == "paid"


def test_swap_dialog_refuses_one_sided_and_shows_service_errors(qapp, boxes, monkeypatch):
    from apps.product_management_app.features.cashier.dialogs import swap_dialog as sd
    dlg = sd.SwapDialog(None, party_id=CUSTOMER_ID, party_name="Customer One")
    dlg.btn_save.click()
    assert boxes[-1][0] == "warning" and dlg.result_data is None
    dlg._recv.append({"product_name": "Maize", "sku": "CROP-MAIZE", "product_type": "crops", "qty_bags": 1,
                      "total_weight_kg": 10.0, "unit_cost": 10.0})
    dlg._give.append({"product_id": FERT_ID, "name": "NPK", "type": "fertilizer", "qty_bags": 9999,
                      "total_weight_kg": 0.0, "unit_price": 1.0})
    dlg.btn_save.click()
    assert boxes[-1][0] == "warning" and dlg.result_data is None          # not enough stock


def test_cashier_intake_has_a_swap_button(qapp):
    from apps.product_management_app.features.cashier.views.cashier_intake import CashierIntakeView
    from apps.product_management_app.app_state import state
    v = CashierIntakeView()
    assert v.btn_swap.text().startswith("Swap")


def test_party_picker_any_lists_customers_and_suppliers_but_not_walkins(qapp):
    from apps.product_management_app.features.cashier.dialogs.party_search_picker_dialog import PartySearchPickerDialog
    d = PartySearchPickerDialog("any")
    names = {r["name"] for r in d._rows}
    assert "Customer One" in names and "Supplier One" in names and "Walk-in" not in names
