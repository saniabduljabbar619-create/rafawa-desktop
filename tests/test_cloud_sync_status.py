"""The cloud sync bar: the worker is started, and the bar says what is true (and why, when it is not syncing)."""
from types import SimpleNamespace

import pytest

from apps.product_management_app.services import cloud_sync_worker as cw
from database.services.cloud_sync_service import CloudSyncEngine as E
from shared.config import settings as cfg
from shared.uix.layout.sync_status_bar import SyncStatusBar


def _worker(monkeypatch, **engine):
    for name, val in engine.items():
        monkeypatch.setattr(E, name, staticmethod(val) if callable(val) else staticmethod(lambda v=val: v))
    monkeypatch.setattr(cw, "settings", SimpleNamespace(CLOUD_SYNC_INTERVAL_SECONDS=1))
    return cw.CloudSyncWorker()


def test_unconfigured_worker_says_why_once_and_ends(qapp, monkeypatch):
    w = _worker(monkeypatch, config_problem="not set up - add the CLOUD_DB_HOST lines to the .env file")
    seen = []
    w.not_configured.connect(seen.append)
    w.run()                                                    # returns instead of looping forever
    assert seen == ["not set up - add the CLOUD_DB_HOST lines to the .env file"]


def test_configured_worker_reports_progress_and_finishes(qapp, monkeypatch):
    def sync_all(progress_callback=None):
        progress_callback("parties", 1, 2, {"ok": True, "pushed": 3})
        progress_callback("sales", 2, 2, {"ok": False, "error": "Access denied"})
        w.stop()                                               # one pass is enough for the test
        return {"ok": True}

    w = _worker(monkeypatch, config_problem="", is_configured=True, sync_all=sync_all)
    log = []
    w.sync_started.connect(lambda: log.append("start"))
    w.table_progress.connect(lambda *a: log.append(a))
    w.sync_detail.connect(lambda t: log.append(("detail", t)))
    w.sync_finished.connect(lambda ok, n, failed: log.append(("done", ok, n, failed)))
    w.run()
    assert log == ["start", ("parties", 1, 2, True, 3), ("sales", 2, 2, False, 0),
                   ("detail", "sales: Access denied"), ("done", False, 3, ["sales"])]


def test_bar_reads_the_state_and_keeps_the_error_for_the_tooltip(qapp):
    bar = SyncStatusBar()
    assert "starting" in bar.label.text()
    bar.on_not_configured("not set up - add the CLOUD_DB_HOST lines to the .env file")
    assert bar.label.text() == "Cloud sync: not set up" and "CLOUD_DB_HOST" in bar.toolTip()
    bar.on_sync_started()
    bar.on_sync_detail("sales: Access denied")
    bar.on_sync_finished(False, 0, ["sales"])
    assert "issue" in bar.label.text() and bar.toolTip() == "sales: Access denied"
    bar.on_sync_detail("")
    bar.on_sync_finished(True, 5, [])
    assert "Synced to cloud" in bar.label.text()
    bar.on_not_configured()                                    # the old no-argument call still works
    assert "not configured" in bar.label.text()


def test_config_problem_names_what_is_missing(monkeypatch):
    for k in ("CLOUD_DB_HOST", "CLOUD_DB_NAME", "CLOUD_DB_USER", "CLOUD_DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(E, "is_configured", staticmethod(lambda: False))
    stub = SimpleNamespace(cloud_database_url="", CLOUD_SYNC_ENABLED=False)
    monkeypatch.setattr("database.services.cloud_sync_service.settings", stub)
    assert "CLOUD_DB_HOST" in E.config_problem() and ".env" in E.config_problem()
    monkeypatch.setenv("CLOUD_DB_HOST", "h")
    assert "missing CLOUD_DB_NAME" in E.config_problem()
    stub2 = SimpleNamespace(cloud_database_url="mysql+pymysql://u:p@h/db", CLOUD_SYNC_ENABLED=False)
    monkeypatch.setattr("database.services.cloud_sync_service.settings", stub2)
    assert "switched off" in E.config_problem()
    stub3 = SimpleNamespace(cloud_database_url="mysql+pymysql://u:p@h/db", CLOUD_SYNC_ENABLED=True)
    monkeypatch.setattr("database.services.cloud_sync_service.settings", stub3)
    assert E.config_problem() == ""


def test_a_relative_ca_path_is_found_where_the_env_file_is(tmp_path, monkeypatch):
    (tmp_path / "ca.pem").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert cfg._find_file("ca.pem") == str(tmp_path / "ca.pem")
    assert cfg._find_file("") == "" and cfg._find_file("/abs/ca.pem") == "/abs/ca.pem"
    assert cfg._find_file("nowhere.pem") == "nowhere.pem"


def test_main_window_starts_the_worker_after_launch(qapp, monkeypatch):
    from apps.product_management_app.main import ProductManagementMainWindow
    started = []
    monkeypatch.setattr(cw.CloudSyncWorker, "start", lambda self, *a: started.append(1))
    monkeypatch.setattr(cw.CloudSyncWorker, "isRunning", lambda self: False)
    w = ProductManagementMainWindow()
    assert started == []                    # not during construction - startup is never delayed
    w._start_cloud_sync()
    assert started == [1]


def test_every_synced_table_can_be_read_for_pushing():
    """The worker never ran in the app before, so prove each table really can be read (needs updated_at + id)."""
    from database.services.cloud_sync_service import _sync_table_specs
    for model, name in _sync_table_specs():
        E._fetch_dirty_rows(model, None, None)                  # raises if a model lacks a column the engine needs
        assert hasattr(model, "updated_at") and hasattr(model, "id"), name
