"""The printed balance statement: what is left on one document and on everything still pending, in Hausa or English."""
import pytest

from apps.product_management_app.features.receipts_v2.balance_slip import (
    LANGUAGES, SCOPE_BOTH, SCOPE_CURRENT, SCOPE_TOTAL, STRINGS, render_balance_slip,
)
from apps.product_management_app.features.receipts_v2.receipt_models import BOLD_MARKER, ShopInfo
from apps.product_management_app.features.receipts_v2.receipt_render import WIDTH
from database.services.advance_service import AdvanceService
from database.services.balance_statement_service import BalanceStatementService as B
from database.services.payment_service import PaymentService
from tests.conftest import ADMIN_ID, CROP_ID, CUSTOMER_ID, FERT_ID, SUPPLIER_ID, make_sale, pay

SHOP = ShopInfo(name="Rafawa Enterprises", address="Kano", phone="0700", footer_note="Thank you")


def _sales():
    """SAL A: 20,000 unpaid.  SAL B: 50,000, 20,000 paid (30,000 left)."""
    a = make_sale([{"product_id": CROP_ID, "qty_bags": 2, "total_weight_kg": 200, "unit_price": 100}])["sale_id"]
    b = make_sale([{"product_id": FERT_ID, "qty_bags": 5, "total_weight_kg": 0, "unit_price": 10000}])["sale_id"]
    pay("sale", b, CUSTOMER_ID, "in", 20000)
    return a, b


def test_sale_statement_has_this_sale_and_everything_pending():
    a, b = _sales()
    d = B.for_document("sale", b)
    assert d["ref"] == f"SAL-{b:05d}" and d["name"] == "Customer One"
    assert (d["current"]["total"], d["current"]["paid"], d["current"]["balance"]) == (50000, 20000, 30000)
    assert d["current"]["status"] == "partial"
    p = d["pending"]
    assert {x["id"] for x in p["sales"]} == {a, b} and p["sales_balance"] == 50000
    assert p["they_owe_us"] == 50000 and p["we_owe_them"] == 0 and d["has_pending"]


def test_advance_out_left_counts_as_money_they_owe_us():
    _sales()
    adv = AdvanceService.record_advance_out(CUSTOMER_ID, 100000, ADMIN_ID)
    PaymentService.record_advance_payment(adv, CUSTOMER_ID, "in", 40000, ADMIN_ID, method="cash")
    d = B.for_document("advance", adv)
    c = d["current"]
    assert (c["direction"], c["amount"], c["used"], c["remaining"]) == ("out", 100000, 40000, 60000)
    assert d["pending"]["advance_out_left"] == 60000
    assert d["pending"]["they_owe_us"] == 50000 + 60000


def test_a_payment_is_printed_against_the_document_it_paid():
    a, _b = _sales()
    pid = pay("sale", a, CUSTOMER_ID, "in", 5000)
    d = B.for_document("payment", pid)
    assert d["kind"] == "sale" and d["doc_id"] == a and d["current"]["balance"] == 15000


def test_supplier_side_we_owe_them_and_net():
    from tests.test_purchase_price_edit import _purchase
    pid = _purchase()                                                  # 140,000 owed to the supplier
    AdvanceService.record_advance_out(SUPPLIER_ID, 30000, ADMIN_ID)    # they owe us 30,000 back
    d = B.for_document("purchase", pid)
    p = d["pending"]
    assert p["we_owe_them"] == 140000 and p["they_owe_us"] == 30000 and p["net"] == -110000


def test_party_total_without_a_document():
    _sales()
    d = B.for_party(CUSTOMER_ID)
    assert d["kind"] == "party" and d["current"] is None and d["pending"]["they_owe_us"] == 50000


def test_nothing_pending_is_flagged():
    a, _b = _sales()
    pay("sale", a, CUSTOMER_ID, "in", 20000)
    pay("sale", _b, CUSTOMER_ID, "in", 30000)
    assert B.for_document("sale", a)["has_pending"] is False


# --------------------------------------------------------------------------- the text
def _slip(d, lang, scope):
    return render_balance_slip(d, SHOP, lang, scope).replace(BOLD_MARKER, "")


def test_hausa_and_english_text_for_a_sale():
    _a, b = _sales()
    d = B.for_document("sale", b)
    ha = _slip(d, "ha", SCOPE_BOTH)
    assert "TAKARDAR RAGOWAR KUDI" in ha and "WANNAN SAYARWA" in ha and "RAGOWA:" in ha
    assert "KUNA BIN MU" in ha and "N30,000.00" in ha and "N50,000.00" in ha
    en = _slip(d, "en", SCOPE_BOTH)
    assert "BALANCE STATEMENT" in en and "THIS SALE" in en and "YOU OWE US" in en
    assert "Part paid" in en and "Matsayi" not in en


def test_scope_picks_the_sections():
    _a, b = _sales()
    d = B.for_document("sale", b)
    cur = _slip(d, "en", SCOPE_CURRENT)
    tot = _slip(d, "en", SCOPE_TOTAL)
    assert "THIS SALE" in cur and "TOTAL BALANCE" not in cur
    assert "TOTAL BALANCE" in tot and "THIS SALE" not in tot


def test_party_slip_is_total_only_whatever_the_scope():
    _sales()
    text = _slip(B.for_party(CUSTOMER_ID), "ha", SCOPE_CURRENT)
    assert "JIMILLAR RAGOWAR KUDI" in text and "WANNAN SAYARWA" not in text


def test_advance_slip_in_hausa():
    adv = AdvanceService.record_advance_out(CUSTOMER_ID, 100000, ADMIN_ID)
    PaymentService.record_advance_payment(adv, CUSTOMER_ID, "in", 50000, ADMIN_ID, method="cash")
    text = _slip(B.for_document("advance", adv), "ha", SCOPE_CURRENT)
    assert "WANNAN KUDIN GABA" in text and "Mun ba ku kudin gaba" in text
    assert "N100,000.00" in text and "N50,000.00" in text and "An yi amfani da wani bangare" in text


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_slip_fits_an_80mm_thermal_printer(lang):
    """Plain ASCII only (the printer is CP437) and never wider than the paper."""
    _a, b = _sales()
    AdvanceService.record_advance_out(CUSTOMER_ID, 100000, ADMIN_ID)
    for scope in (SCOPE_CURRENT, SCOPE_TOTAL, SCOPE_BOTH):
        text = _slip(B.for_document("sale", b), lang, scope)
        text.encode("ascii")
        assert max(len(line) for line in text.splitlines()) <= WIDTH, (lang, scope)


def test_every_english_string_has_a_hausa_one():
    assert set(STRINGS["ha"]) == set(STRINGS["en"])


def test_a_long_pending_list_is_shortened():
    from apps.product_management_app.features.receipts_v2.balance_slip import MAX_LISTED
    for _ in range(MAX_LISTED + 3):
        make_sale([{"product_id": FERT_ID, "qty_bags": 1, "total_weight_kg": 0, "unit_price": 1000}])
    d = B.for_party(CUSTOMER_ID)
    assert len(d["pending"]["sales"]) == MAX_LISTED + 3
    assert "...da wasu 3 kuma" in _slip(d, "ha", SCOPE_TOTAL)
