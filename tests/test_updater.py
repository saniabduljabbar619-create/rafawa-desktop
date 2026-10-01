import json

from shared import updater
from shared.version import __version__


def test_version_parsing_and_comparison():
    assert updater.parse_version("v3.10.2-beta") == (3, 10, 2)
    assert updater.is_newer("3.10.0", "3.9.9")
    assert updater.is_newer("4", "3.9.9")
    assert not updater.is_newer("3.5.0", "3.5")
    assert not updater.is_newer("3.4.9", "3.5.0")


def test_newer_manifest_is_reported(tmp_path):
    f = tmp_path / "latest.json"
    f.write_text(json.dumps({"version": "99.0.0", "url": "https://x/setup.exe", "notes": "big"}))
    info = updater.check_for_update(str(f))
    assert info and (info.version, info.url, info.notes) == ("99.0.0", "https://x/setup.exe", "big")


def test_same_older_missing_or_broken_manifest_gives_none(tmp_path):
    f = tmp_path / "latest.json"
    f.write_text(json.dumps({"version": __version__}))
    assert updater.check_for_update(str(f)) is None
    f.write_text("not json")
    assert updater.check_for_update(str(f)) is None
    assert updater.check_for_update(str(tmp_path / "missing.json")) is None
    assert updater.check_for_update("") is None


def test_http_manifest(monkeypatch):
    import httpx

    class R:
        def raise_for_status(self): pass
        def json(self): return {"version": "99.1.0", "url": "u"}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert updater.check_for_update("https://example.com/latest.json").version == "99.1.0"
