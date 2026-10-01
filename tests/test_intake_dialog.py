import pytest
from PySide2.QtWidgets import QMessageBox

from shared.uix.widgets.dialogs import DeliveryIntakeDialog as D


@pytest.fixture(autouse=True)
def fake_products(monkeypatch):
    """list_products_db is raw MySQL-paramstyle SQL (it cannot run on the SQLite test database)."""
    from apps.product_management_app.app_state import state
    monkeypatch.setattr(state, "list_products_db", lambda q="", product_type=None: [
        {"id": 1, "name": "Maize" if product_type != "fertilizer" else "NPK", "sku": "SKU-1"}])


@pytest.fixture
def no_boxes(monkeypatch):
    shown = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append(a[2] if len(a) > 2 else "")))
    return shown


def _fill(dlg, r, bags, kg, price):
    for c, v in enumerate((bags, kg, price)):
        dlg.weights_tbl.item(r, c).setText(v)


def test_defaults_quick_total_and_select_product(qapp, db):
    d = D()
    assert d.mode_in.currentText() == D.MODE_TOTAL
    assert d.product_in.currentText() == "Select Product" and d.product_in.currentIndex() == 0
    assert d._product_text() == ""                       # the placeholder is not a product
    assert d.product_in.count() > 1                      # products exist, still not pre-picked


def test_price_label_follows_ledger(qapp, db):
    d = D()
    assert d.rate_lbl.text() == "Price Per Kg *"
    d.ledger_in.setCurrentText("Fertilizers")
    assert d.rate_lbl.text() == "Price Per Bag *"
    d.ledger_in.setCurrentText("Crops")
    assert d.rate_lbl.text() == "Price Per Kg *"


def test_placeholder_blocks_save(qapp, db, no_boxes):
    d = D()
    d.save_btn.click()
    assert d.result() != d.Accepted and no_boxes


def test_exact_rounds_compute_and_payload(qapp, db):
    d = D()
    d.mode_in.setCurrentText(D.MODE_EXACT)
    d.show()
    assert d.exact_box.isVisible() and not d.bags_in.isVisible() and not d.rate_in.isVisible()
    assert [d.weights_tbl.horizontalHeaderItem(i).text() for i in range(4)] == \
        ["Bags Round", "Weight (kg)", "Price Per Kg", "Total"]
    _fill(d, 0, "3bags", "245", "450")
    assert d.weights_tbl.item(0, 3).text() == "110,250.00"
    assert d.weights_tbl.rowCount() == 2                 # a fresh blank row appeared
    _fill(d, 1, "2", "100", "500")
    d.product_in.setCurrentIndex(1)
    p = d.payload()
    assert p["bags"] == 5 and p["total_kg"] == 345
    assert p["total_amount"] == 110250 + 50000
    assert round(p["total_kg"] * p["rate_per_kg"], 2) == 160250     # avg price keeps the line total exact
    assert "160,250.00" in d.total_amt_lbl.text()


def test_exact_validation_messages(qapp, db, no_boxes):
    d = D()
    d.mode_in.setCurrentText(D.MODE_EXACT)
    d.product_in.setCurrentIndex(1)
    d.save_btn.click()                                   # nothing entered
    assert d.result() != d.Accepted and no_boxes
    _fill(d, 0, "3", "245", "")
    d.save_btn.click()
    assert "price per kg" in no_boxes[-1].lower()
    _fill(d, 0, "3", "245", "450")
    d.save_btn.click()
    assert d.result() == d.Accepted
    d2 = D(); d2.mode_in.setCurrentText(D.MODE_EXACT)
    _fill(d2, 0, "x", "1", "1")
    assert "bags" in d2._read_rounds()[1].lower()


def test_remove_round_keeps_a_blank_row(qapp, db):
    d = D()
    d.mode_in.setCurrentText(D.MODE_EXACT)
    _fill(d, 0, "1", "10", "10")
    d.weights_tbl.setCurrentCell(0, 0)
    d._remove_round_row()
    assert d.weights_tbl.rowCount() == 1 and d._read_rounds()[0] is None   # back to one empty row


def test_quick_total_still_works(qapp, db):
    d = D()
    d.product_in.setCurrentIndex(1)
    d.bags_in.setValue(4); d.totalkg_in.setValue(200); d.rate_in.setValue(10)
    p = d.payload()
    assert p["bags"] == 4 and p["total_kg"] == 200 and p["total_amount"] == 2000
