"""Alembic must work on a brand-new database AND on an existing installation."""
import os
import subprocess
import sys

import sqlalchemy as sa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic(url, *args):
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def _tables(url):
    return set(sa.inspect(sa.create_engine(url)).get_table_names())


def test_fresh_database_gets_full_schema_at_head(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    r = _alembic(url, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    t = _tables(url)
    assert {"users", "sales", "payments", "audit_logs", "alembic_version",
            "weigh_tickets", "weigh_ticket_lines", "weigh_rounds", "expenses", "document_offsets", "debt_people", "debt_entries", "expenditures", "order_reviews", "debt_payments"} <= t
    assert "0016" in _alembic(url, "current").stderr + _alembic(url, "current").stdout


def test_existing_installation_is_upgraded_in_place(tmp_path):
    """A DB that already has the app's tables (but no audit_logs / no alembic_version)."""
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(80))"))
        c.execute(sa.text("INSERT INTO users VALUES (1, 'keepme')"))
        c.execute(sa.text("CREATE TABLE sale_returns (id INTEGER PRIMARY KEY, unit_price_snapshot FLOAT "
                          "NOT NULL, qty_kg FLOAT NOT NULL, amount FLOAT NOT NULL)"))
        c.execute(sa.text("INSERT INTO sale_returns VALUES (1, 90.5, 10.25, -905.12)"))
        c.execute(sa.text("CREATE TABLE purchase_returns (id INTEGER PRIMARY KEY, unit_cost_snapshot FLOAT "
                          "NOT NULL, qty_kg FLOAT NOT NULL, amount FLOAT NOT NULL)"))
    r = _alembic(url, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    assert "audit_logs" in _tables(url)
    with eng.connect() as c:                                   # existing data untouched
        assert c.execute(sa.text("SELECT username FROM users")).scalar() == "keepme"
        assert abs(float(c.execute(sa.text("SELECT amount FROM sale_returns")).scalar()) + 905.12) < 0.005
    assert _alembic(url, "upgrade", "head").returncode == 0     # idempotent


def test_plain_alembic_command_works_not_only_python_dash_m(tmp_path):
    """On Windows users run `alembic upgrade head` (the .exe launcher), which - unlike
    `python -m alembic` - does not put the project folder on sys.path."""
    import shutil
    exe = shutil.which("alembic", path=os.path.dirname(sys.executable)) or shutil.which("alembic")
    if not exe:
        import pytest
        pytest.skip("alembic launcher not on PATH")
    url = f"sqlite:///{tmp_path / 'exe.db'}"
    env = dict(os.environ, DATABASE_URL=url)
    r = subprocess.run([exe, "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "audit_logs" in _tables(url)


def test_0014_gives_each_walk_in_a_code_and_splits_the_shared_account_by_name(tmp_path):
    url = f"sqlite:///{tmp_path / 'walkins.db'}"
    eng = sa.create_engine(url)
    ts = "created_at DATETIME, updated_at DATETIME"
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(80))"))
        c.execute(sa.text(f"CREATE TABLE parties (id INTEGER PRIMARY KEY, party_type VARCHAR(20) NOT NULL, "
                          f"display_name VARCHAR(180) NOT NULL, phone VARCHAR(40), address VARCHAR(255), "
                          f"is_active BOOLEAN NOT NULL DEFAULT 1, is_customer BOOLEAN NOT NULL DEFAULT 0, "
                          f"is_supplier BOOLEAN NOT NULL DEFAULT 0, {ts})"))
        c.execute(sa.text("CREATE TABLE sales (id INTEGER PRIMARY KEY, buyer_party_id INTEGER, walkin_name VARCHAR(120))"))
        c.execute(sa.text("CREATE TABLE purchases (id INTEGER PRIMARY KEY, supplier_party_id INTEGER, "
                          "walkin_name VARCHAR(120))"))
        c.execute(sa.text("CREATE TABLE weigh_tickets (id INTEGER PRIMARY KEY, party_id INTEGER, walkin_name VARCHAR(120))"))
        c.execute(sa.text("CREATE TABLE payments (id INTEGER PRIMARY KEY, party_id INTEGER, entity_type VARCHAR(20), "
                          "entity_id INTEGER)"))
        c.execute(sa.text("CREATE TABLE purchase_returns (id INTEGER PRIMARY KEY, party_id INTEGER, purchase_id INTEGER, "
                          "unit_cost_snapshot FLOAT NOT NULL DEFAULT 0, qty_kg FLOAT NOT NULL DEFAULT 0, "
                          "amount FLOAT NOT NULL DEFAULT 0)"))
        c.execute(sa.text("CREATE TABLE sale_returns (id INTEGER PRIMARY KEY, party_id INTEGER, sale_id INTEGER, "
                          "unit_price_snapshot FLOAT NOT NULL DEFAULT 0, qty_kg FLOAT NOT NULL DEFAULT 0, "
                          "amount FLOAT NOT NULL DEFAULT 0)"))
        for pid, ptype, name in ((1, "customer", "Real Customer"), (7, "walkin", "Old Casual"),
                                 (11, "walkin", "Walk-in")):
            c.execute(sa.text("INSERT INTO parties (id, party_type, display_name) VALUES (:i, :t, :n)"),
                      {"i": pid, "t": ptype, "n": name})
        c.execute(sa.text("UPDATE parties SET is_supplier = 1, is_customer = 1 WHERE id = 7"))   # wrongly flagged earlier
        # shared account: two different names bought at the till, one weighed name, plus a real customer's sale
        c.execute(sa.text("INSERT INTO sales VALUES (1, 11, 'Musa'), (2, 11, 'Ladi'), (3, 1, NULL)"))
        c.execute(sa.text("INSERT INTO purchases VALUES (1, 11, 'Musa'), (2, 11, 'Sule'), (3, 1, NULL)"))
        c.execute(sa.text("INSERT INTO weigh_tickets VALUES (1, 11, 'Sule'), (2, 11, 'Sule')"))
        c.execute(sa.text("INSERT INTO payments VALUES (1, 11, 'purchase', 2), (2, 11, 'sale', 2), (3, 1, 'sale', 3)"))
        c.execute(sa.text("INSERT INTO purchase_returns (id, party_id, purchase_id) VALUES (1, 11, 2)"))
        c.execute(sa.text("INSERT INTO sale_returns (id, party_id, sale_id) VALUES (1, 11, 1)"))
    r = _alembic(url, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    with eng.connect() as c:
        q = lambda s: c.execute(sa.text(s)).fetchall()                    # noqa: E731
        assert q("SELECT walkin_code FROM parties WHERE id = 7")[0][0] == "WKN-00001"
        by_name = {n: (i, code) for i, n, code in q(
            "SELECT id, display_name, walkin_code FROM parties WHERE walkin_code IS NOT NULL")}
        assert set(by_name) == {"Old Casual", "Musa", "Ladi", "Sule"}
        assert by_name["Musa"][1].startswith("WKN-") and by_name["Ladi"][1].startswith("WKN-")
        assert by_name["Sule"][1] == "WT-WKN-00001"                       # weighed -> the weighing series
        assert len({v[1] for v in by_name.values()}) == 4                 # all codes different
        sule, musa, ladi = by_name["Sule"][0], by_name["Musa"][0], by_name["Ladi"][0]
        assert q("SELECT supplier_party_id FROM purchases ORDER BY id") == [(musa,), (sule,), (1,)]
        assert q("SELECT buyer_party_id FROM sales ORDER BY id") == [(musa,), (ladi,), (1,)]
        assert q("SELECT party_id FROM weigh_tickets") == [(sule,), (sule,)]
        assert q("SELECT party_id FROM payments ORDER BY id") == [(sule,), (ladi,), (1,)]
        assert q("SELECT party_id FROM purchase_returns") == [(sule,)]
        assert q("SELECT party_id FROM sale_returns") == [(musa,)]
        assert q("SELECT COUNT(*) FROM parties WHERE id = 11")[0][0] == 1  # the shared account itself stays
        assert q("SELECT COUNT(*) FROM parties WHERE party_type = 'walkin' AND (is_customer = 1 OR is_supplier = 1)")[0][0] == 0
    assert _alembic(url, "upgrade", "head").returncode == 0                # idempotent: nothing left to split
