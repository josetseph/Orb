"""Port resolution, stale-listener cleanup and Firefly env wiring of the desktop
runtime, without ever calling main() or spawning a sidecar."""

import os
import socket

import pytest

from app import desktop_runtime as d

pytestmark = pytest.mark.integration


def test_port_env_override_and_blank_fallback(monkeypatch):
    monkeypatch.setenv("ORB_API_PORT", " 18000 ")
    assert d._port("API", 17401) == 18000
    monkeypatch.setenv("ORB_API_PORT", "   ")
    assert d._port("API", 17401) == 17401
    assert set(d.PORTS) == {"api", "firefly", "qdrant", "meili"}
    assert len(set(d.PORTS.values())) == 4  # no two sidecars share a port


def test_free_ports_never_kills_this_process():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert os.getpid() in d._pids_on_port(port)
        assert d.free_ports([port]) == 0
        assert s.fileno() != -1  # still ours


def test_free_ports_kills_strangers_and_counts_them(monkeypatch):
    killed = []
    monkeypatch.setattr(d, "_pids_on_port", lambda p: {1111: [99999, os.getpid()], 2222: []}[p])
    monkeypatch.setattr(d, "_kill_pid", killed.append)
    assert d.free_ports([1111, 2222]) == 1
    assert killed == [99999]


def test_firefly_env_lands_under_data_dir_and_survives_reruns(tmp_path, monkeypatch):
    monkeypatch.setitem(d.PORTS, "firefly", 18412)
    d._ensure_firefly_env(tmp_path)
    env_file = d.firefly_app_dir(tmp_path) / ".env"
    env = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert env["APP_URL"] == "'http://127.0.0.1:18412'"
    assert env["DB_DATABASE"] == d._env_quote(str(d.firefly_app_dir(tmp_path) / "storage" / "database" / "firefly.sqlite"))
    assert env_file.stat().st_mode & 0o777 == 0o600
    d._ensure_firefly_env(tmp_path)  # second boot keeps the app key, or sessions die
    assert f"APP_KEY={env['APP_KEY']}" in env_file.read_text()
