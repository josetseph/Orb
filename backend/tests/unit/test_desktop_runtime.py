"""Smallest checks that fail if the desktop runtime's bookkeeping breaks."""

import json

from app import desktop_runtime as d


def test_meili_key_is_random_for_fresh_install_and_sticky(tmp_path, monkeypatch):
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)
    key = d.resolve_meili_master_key(tmp_path)
    assert key != "orb-dev-key" and len(key) > 20
    assert d.resolve_meili_master_key(tmp_path) == key  # persisted, not regenerated


def test_meili_key_keeps_legacy_default_when_data_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)
    (tmp_path / "meilisearch").mkdir()
    (tmp_path / "meilisearch" / "data.ms").write_bytes(b"x")
    assert d.resolve_meili_master_key(tmp_path) == "orb-dev-key"


def test_status_file_is_written_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_STATUS_FILE", tmp_path / "boot-status.json")
    d.status("Downloading Qdrant… 40%")
    assert json.loads((tmp_path / "boot-status.json").read_text())["status"] == "Downloading Qdrant… 40%"
    assert not (tmp_path / "boot-status.tmp").exists()


def test_firefly_env_and_runtime_keys(tmp_path):
    (tmp_path / "firefly" / "app").mkdir(parents=True)
    d._ensure_firefly_env(tmp_path)
    env = dict(line.split("=", 1) for line in (tmp_path / "firefly" / "app" / ".env").read_text().splitlines())
    runtime = json.loads((tmp_path / "firefly" / "runtime.json").read_text())
    assert env["APP_KEY"] == runtime["appKey"] and env["APP_KEY"].startswith("base64:")
    assert env["STATIC_CRON_TOKEN"] == runtime["cronToken"] and len(runtime["cronToken"]) == 32
    assert env["DB_DATABASE"].endswith("firefly.sqlite")
