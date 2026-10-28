"""Long lists stay quick: the number of database round trips must not grow with the number of tickets / rounds."""
import pytest
from sqlalchemy import event

from database.engine import engine
from database.services.weigh_service import WeighService as W
from tests.conftest import ADMIN_ID, CROP_ID, SUPPLIER_ID
from tests.test_weigh_service import CASHIER, WEIGHER, as_role


class Counter:
    def __enter__(self):
        self.n = 0
        event.listen(engine, "before_cursor_execute", self._hit)
        return self

    def _hit(self, *a):
        self.n += 1

    def __exit__(self, *a):
        event.remove(engine, "before_cursor_execute", self._hit)


@pytest.fixture
def many():
    """60 tickets (a third finalized) plus one ticket with 60 rounds."""
    as_role("weigher", WEIGHER)
    ids = []
    for i in range(60):
        tid = W.create_ticket(SUPPLIER_ID, "supplier")
        ln = W.add_line(tid, CROP_ID, area="A", grade="A")
        for r in range(3):
            W.record_round(ln, 2, 100 + r)
        W.set_line_price(ln, 450)
        W.submit_ticket(tid, "full")
        ids.append(tid)
    as_role("cashier", CASHIER)
    for tid in ids[::3]:
        W.finalize_ticket(tid)
    as_role("weigher", WEIGHER)
    big = W.create_ticket(SUPPLIER_ID, "supplier")
    line = W.add_line(big, CROP_ID)
    for r in range(60):
        W.record_round(line, 2, 100 + r)
    return ids, big


def test_listing_sixty_tickets_takes_a_handful_of_queries_not_hundreds(many):
    with Counter() as c:
        rows = W.list_tickets()
    assert len(rows) == 61
    assert c.n <= 15, f"{c.n} queries for 61 tickets"
    done = [r for r in rows if r["status"] == "finalized"]
    assert len(done) == 20 and all(r["payment"]["total"] == r["total_amount"] for r in done)


def test_opening_a_sixty_round_ticket_is_a_handful_of_queries(many):
    _ids, big = many
    with Counter() as c:
        t = W.get_ticket(big)
    assert len(t["lines"][0]["rounds"]) == 60 and c.n <= 10, f"{c.n} queries"


def test_the_lists_do_not_ask_the_database_again_for_every_click(qapp, many):
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    as_role("admin", ADMIN_ID)
    v = WeighTicketsAdminView("admin")
    v.filter_seg.set_key("all")
    v.refresh()
    with Counter() as c:
        for row in (3, 7, 9, 2):
            v.table.setCurrentCell(row, 0)               # moving through the list
    assert c.n == 0
    with Counter() as c:
        v.refresh()
    assert c.n <= 15, f"{c.n} queries for one refresh"
