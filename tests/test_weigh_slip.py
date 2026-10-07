from apps.product_management_app.features.receipts_v2 import ShopInfo, render_weigh_slip
from apps.product_management_app.features.receipts_v2.receipt_render import WIDTH

TICKET = {
    "ticket_no": "WT-00007", "party_name": "Musa Farms", "party_phone": "08031234567", "party_role": "supplier",
    "weigher_name": "Bala", "created_at": None, "submitted_at": None, "total_bags": 5.5, "total_kg": 426.75,
    "lines": [{"product_name": "Maize", "area": "Gwarzo", "grade": "A", "bags": 5.5, "kg": 426.75,
               "unit_price": 450, "amount": 192037.5,
               "rounds": [{"round_no": 1, "bags": 3, "weight_kg": 245.5},
                          {"round_no": 2, "bags": 2, "weight_kg": 160},
                          {"round_no": 3, "bags": 0.5, "weight_kg": 21.25}]}],
}
SHOP = ShopInfo(name="Rafawa Enterprises", address="Kano", phone="0800", footer_note="Thanks")


def test_full_slip_lists_every_round_with_totals_and_signatures():
    t = render_weigh_slip(TICKET, SHOP, "full")
    for needle in ("WEIGHING SLIP", "not a payment receipt", "WT-00007", "Musa Farms", "Bala", "Gwarzo",
                   "Grade A", "245.50", "160.00", "21.25", "TOTAL BAGS:", "5.5", "426.75",
                   "Weigher: ____", "Supplier: ____"):
        assert needle in t, needle
    assert all(len(line) <= WIDTH for line in t.splitlines())


def test_summary_slip_hides_individual_rounds():
    t = render_weigh_slip(TICKET, SHOP, "summary")
    assert "245.50" not in t and "426.75" in t and "Subtotal (3 rounds)" in t


def test_slip_shows_the_price_and_amounts_the_weigher_set():
    t = render_weigh_slip(dict(TICKET, total_amount=192037.5), SHOP, "full")
    for needle in ("Price per kg", "450.00", "Amount", "192,037.50", "TOTAL AMOUNT:", "not a payment receipt"):
        assert needle in t, needle
    assert all(len(line) <= WIDTH for line in t.splitlines())
    assert "192,037.50" in render_weigh_slip(dict(TICKET, total_amount=192037.5), SHOP, "summary")


def test_slip_without_a_price_prints_none():
    bare = dict(TICKET, lines=[dict(TICKET["lines"][0], unit_price=None, amount=None)], total_amount=None)
    t = render_weigh_slip(bare, SHOP, "full")
    assert "Price per kg" not in t and "TOTAL AMOUNT" not in t
