import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
import sqlalchemy as sa

from database import backup
from database.engine import engine
from database.schema_check import check_schema, head_revision
from shared import crash, logging_setup, paths
from tests.conftest import make_sale


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAFAWA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


# ---------------------------------------------------------------- paths / logging
def test_data_dir_is_created_and_overridable(data_dir):
    assert paths.data_dir() == data_dir and data_dir.is_dir()
    assert paths.sub_dir("logs").is_dir()


def test_logging_writes_rotating_files_and_routes_security_events(data_dir):
    root = logging.getLogger()
    saved = list(root.handlers)
    logging_setup._configured = False
    try:
        logging_setup.setup_logging(console=False)
        logging.getLogger("rafawa.security").warning("LOGIN FAILED user='x'")
        logging.getLogger("rafawa.other").info("hello")
        for h in root.handlers:
            h.flush()
        assert "LOGIN FAILED" in (data_dir / "logs" / "security.log").read_text()
        assert "hello" not in (data_dir / "logs" / "security.log").read_text()
        assert "hello" in (data_dir / "logs" / "app.log").read_text()
    finally:
        for h in list(root.handlers):
            if h not in saved:
                root.removeHandler(h); h.close()
        logging_setup._configured = False


# ---------------------------------------------------------------- crash reports
def test_crash_report_contains_version_and_traceback(data_dir):
    try:
        1 / 0
    except ZeroDivisionError:
        et, e, tb = sys.exc_info()
    path = crash.write_crash_report(et, e, tb, {"User": "alice"})
    text = path.read_text()
    assert "Rafawa Desktop crash report" in text and "ZeroDivisionError" in text and "alice" in text
    assert path.parent == data_dir / "crashes"


def test_excepthook_logs_writes_report_and_notifies(data_dir, monkeypatch):
    seen = []
    old = sys.excepthook
    try:
        crash.install_excepthooks(lambda msg, p: seen.append((msg, p)))
        try:
            raise ValueError("boom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = old
    assert seen and "boom" in seen[0][0] and Path(seen[0][1]).exists()


# ---------------------------------------------------------------- schema check
def test_schema_check_reports_unmigrated_then_current(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 's.db'}")
    st = check_schema(eng)
    assert not st.up_to_date and st.current is None and "alembic upgrade head" in st.message
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        c.execute(sa.text("INSERT INTO alembic_version VALUES ('0001')"))
    st = check_schema(eng)
    assert not st.up_to_date and st.current == "0001" and st.head == head_revision()
    with eng.begin() as c:
        c.execute(sa.text("UPDATE alembic_version SET version_num = :h"), {"h": head_revision()})
    assert check_schema(eng).up_to_date


# ---------------------------------------------------------------- backups
def test_sqlite_backup_is_a_usable_consistent_copy(tmp_path):
    make_sale()
    out = backup.backup_database(engine, tmp_path / "bk")
    assert out.exists() and out.stat().st_size > 0
    with sqlite3.connect(out) as c:
        assert c.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1


def test_backups_are_pruned_to_the_newest_n(tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    stem = Path(engine.url.database).stem
    for i in range(6):
        f = d / f"{stem}-old{i}.sqlite"
        f.write_bytes(b"x")
        os.utime(f, (time.time() - 1000 + i, time.time() - 1000 + i))
    backup.backup_database(engine, d, keep=3)
    assert len(list(d.glob(f"{stem}-*"))) == 3


def test_backup_due_logic(tmp_path):
    d = tmp_path / "bk"
    assert backup.backup_due(d)                                   # nothing there yet
    d.mkdir()
    (d / "x.sql.gz").write_bytes(b"x")
    assert not backup.backup_due(d, every_hours=20)
    old = time.time() - 48 * 3600
    os.utime(d / "x.sql.gz", (old, old))
    assert backup.backup_due(d, every_hours=20)


def test_mysqldump_command_never_contains_the_password():
    url = sa.engine.make_url("mysql+pymysql://app:S3cr3t!@db.local:3307/rafawa?charset=utf8mb4")
    cmd = backup.mysqldump_command(url)
    assert "--host=db.local" in cmd and "--port=3307" in cmd and "--user=app" in cmd and cmd[-1] == "rafawa"
    assert not any("S3cr3t" in part for part in cmd)


def test_missing_mysqldump_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.shutil, "which", lambda _n: None)
    from types import SimpleNamespace
    fake = SimpleNamespace(url=sa.engine.make_url("mysql+pymysql://u:p@localhost/db"))
    with pytest.raises(backup.BackupError, match="mysqldump was not found"):
        backup.backup_database(fake, tmp_path)
