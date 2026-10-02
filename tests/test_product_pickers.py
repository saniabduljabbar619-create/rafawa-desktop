"""Every product dropdown opens on "Select Product" even though products exist."""
import pytest
from PySide2.QtWidgets import QMessageBox

from shared.uix.widgets.product_select import SELECT_PRODUCT


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    shown = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[2] if len(a) > 2 else ""))))
    return shown


def _assert_placeholder(combo):
    assert combo.count() > 1, "products should be listed"
    assert combo.currentIndex() == 0 and combo.currentText() == SELECT_PRODUCT
    assert combo.currentData() is None


def test_supplier_sale_dialog_placeholder(qapp):
    from apps.product_management_app.features.suppliers.views.supplier_new_sale_dialog import SupplierNewSaleDialog
    dlg = SupplierNewSaleDialog(2, "Supplier One")
    dlg.category_cb.setCurrentText("Crops")
    _assert_placeholder(dlg.product_cb)


def test_intake_pickers_start_on_placeholder_and_refuse(qapp, quiet):
    from apps.product_management_app.features.cashier.dialogs.intake_crops_dialog import CropsIntakeDialog
    from apps.product_management_app.features.cashier.dialogs.intake_fertilizer_dialog import FertilizerIntakeDialog
    from apps.product_management_app.features.cashier.dialogs.product_picker import resolve_selected_product
    for dlg, combo, kind in ((CropsIntakeDialog(), "crop_cb", "crops"), (FertilizerIntakeDialog(), "product_cb", "fertilizer")):
        c = getattr(dlg, combo)
        assert c.currentText() == SELECT_PRODUCT and c.currentData() is None
        assert c.itemText(1).startswith("+ New")            # still offered, just never the default
        assert resolve_selected_product(c, kind) is None
        assert any("Select Product" in m for _k, m in quiet)
        c.setCurrentIndex(2)
        assert resolve_selected_product(c, kind)["id"]


def test_advance_in_kind_picker(qapp, monkeypatch):
    from apps.product_management_app.app_state import state
    from apps.product_management_app.features.cashier.dialogs.payment_dialog import PaymentDialog
    monkeypatch.setattr(state, "list_products_by_advance_type_db",
                        lambda t: [{"id": 1, "name": "Maize"}, {"id": 2, "name": "Rice"}])
    dlg = PaymentDialog(mode="advance")
    dlg.advance_type.setCurrentText("Crops")
    _assert_placeholder(dlg.product)
    assert dlg.payload()["product_id"] == 0
