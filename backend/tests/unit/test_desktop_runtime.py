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
    env = {k: v.strip("'") for k, v in (line.split("=", 1) for line in (tmp_path / "firefly" / "app" / ".env").read_text().splitlines())}
    runtime = json.loads((tmp_path / "firefly" / "runtime.json").read_text())
    assert env["APP_KEY"] == runtime["appKey"] and env["APP_KEY"].startswith("base64:")
    assert env["STATIC_CRON_TOKEN"] == runtime["cronToken"] and len(runtime["cronToken"]) == 32
    assert env["DB_DATABASE"].endswith("firefly.sqlite")


class TestFireflyEnvQuoting:
    """phpdotenv rejects unquoted values with spaces; the default macOS data dir
    lives under 'Application Support'."""

    def test_values_are_single_quoted(self, tmp_path, monkeypatch):
        from app import desktop_runtime as dr

        data = tmp_path / "Application Support" / "Orb" / "data"
        dr._ensure_firefly_env(data)
        env = (dr.firefly_app_dir(data) / ".env").read_text()
        db_line = next(l for l in env.splitlines() if l.startswith("DB_DATABASE="))
        assert db_line == f"DB_DATABASE='{dr.firefly_app_dir(data) / 'storage' / 'database' / 'firefly.sqlite'}'"
        assert all("=" in l and (l.split("=", 1)[1][0] in "'\"") for l in env.splitlines() if l)

    def test_apostrophe_falls_back_to_double_quotes(self):
        from app.desktop_runtime import _env_quote

        assert _env_quote("plain") == "'plain'"
        assert _env_quote("Joe's $HOME \"x\"") == '"Joe\'s \\$HOME \\"x\\""'


def test_sidecar_log_rotates_past_limit_and_keeps_one_generation(tmp_path):
    log = tmp_path / "logs" / "qdrant.log"
    d._rotate_log(log)  # missing file: creates the dir, no error
    log.write_bytes(b"x")
    d._rotate_log(log)
    assert log.exists() and not (tmp_path / "logs" / "qdrant.log.1").exists()
    with open(log, "wb") as fh:
        fh.truncate(11 * 1024 * 1024)
    d._rotate_log(log)
    assert not log.exists() and (tmp_path / "logs" / "qdrant.log.1").stat().st_size == 11 * 1024 * 1024
