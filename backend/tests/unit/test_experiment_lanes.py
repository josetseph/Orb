"""A snapshot taken in one lane works in another: the KB registry's paths are moved to the new data dir."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import experiment  # noqa: E402


def test_relocate_moves_registry_paths_and_nothing_else(tmp_path, monkeypatch):
    old, new = "/workspace/orb/data", tmp_path / "data-lane2"
    new.mkdir()
    conn = sqlite3.connect(new / "orb.db")
    conn.execute("CREATE TABLE knowledge_bases (id TEXT, vault_path TEXT, kuzu_path TEXT)")
    conn.execute("INSERT INTO knowledge_bases VALUES ('default', ?, ?)", (f"{old}/vaults/default", f"{old}/kuzu/default"))
    conn.execute("INSERT INTO knowledge_bases VALUES ('elsewhere', '/srv/notes', '/srv/kuzu')")
    conn.commit()
    conn.close()
    (new / "paths.json").write_text("{}")
    monkeypatch.setattr(experiment, "DATA", new)
    experiment.relocate(old)
    rows = dict((r[0], r[1:]) for r in sqlite3.connect(new / "orb.db").execute("SELECT * FROM knowledge_bases"))
    assert rows["default"] == (f"{new}/vaults/default", f"{new}/kuzu/default")
    assert rows["elsewhere"] == ("/srv/notes", "/srv/kuzu")
    assert not (new / "paths.json").exists()


def test_same_lane_restore_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "DATA", tmp_path)
    experiment.relocate(str(tmp_path))  # no database needed: returns before touching it
