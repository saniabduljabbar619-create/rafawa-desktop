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
            "weigh_tickets", "weigh_ticket_lines", "weigh_rounds", "expenses", "document_offsets"} <= t
    assert "0006" in _alembic(url, "current").stderr + _alembic(url, "current").stdout


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
