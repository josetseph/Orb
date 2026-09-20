"""A moved MODELS_DIR must not make downloaded models look missing.

manifest.json records absolute paths. Moving the models directory — an
external drive to the local disk, say — leaves every selection pointing at a
file that is no longer there, and the user is told the model "is not
downloaded" while it sits in the new directory under the same name.
"""

import json

import pytest

from app.services import local_models


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    gguf = tmp_path / "gguf"
    gguf.mkdir(parents=True)
    for name, size in (
        ("chat.gguf", 2_000_000),
        ("embed.gguf", 4096),
        ("rerank.gguf", 4096),
    ):
        (gguf / name).write_bytes(b"\0" * size)
    monkeypatch.setattr(local_models, "resolve_models_dir", lambda: tmp_path)
    return tmp_path


def _manifest(monkeypatch, sel):
    monkeypatch.setattr(local_models, "load_manifest", lambda: {"selection": sel})


class TestSelectedGguf:
    def test_existing_path_is_returned_as_is(self, models_dir):
        real = models_dir / "gguf" / "chat.gguf"
        assert local_models.selected_gguf(str(real)) == real

    def test_stale_absolute_path_falls_back_to_current_dir(self, models_dir):
        stale = "/Volumes/NAS/Projects/liveos/gguf/embed.gguf"
        assert local_models.selected_gguf(stale) == models_dir / "gguf" / "embed.gguf"

    def test_genuinely_missing_file_is_still_none(self, models_dir):
        assert local_models.selected_gguf("/Volumes/NAS/gguf/absent.gguf") is None

    def test_empty_is_none(self, models_dir):
        assert local_models.selected_gguf(None) is None
        assert local_models.selected_gguf("") is None


class TestGgufPathsAfterAMove:
    def test_all_three_resolve_from_stale_paths(self, models_dir, monkeypatch):
        """The reported failure: every path points at an unmounted drive."""
        _manifest(
            monkeypatch,
            {
                "chat_path": "/Volumes/NAS/Projects/liveos/gguf/chat.gguf",
                "embed_path": "/Volumes/NAS/Projects/liveos/gguf/embed.gguf",
                "reranker_path": "/Volumes/NAS/Projects/liveos/gguf/rerank.gguf",
            },
        )
        got = local_models.gguf_paths_if_present()
        assert got is not None, "downloaded models reported as missing"
        assert got["chat"] == models_dir / "gguf" / "chat.gguf"
        assert got["embed"] == models_dir / "gguf" / "embed.gguf"
        assert got["reranker"] == models_dir / "gguf" / "rerank.gguf"

    def test_reranker_alone_may_be_absent(self, models_dir, monkeypatch):
        _manifest(
            monkeypatch,
            {
                "chat_path": "/old/chat.gguf",
                "embed_path": "/old/embed.gguf",
                "reranker_path": "/old/gone.gguf",
            },
        )
        got = local_models.gguf_paths_if_present()
        assert got is not None and "reranker" not in got

    def test_a_truly_absent_embed_still_reports_missing(self, models_dir, monkeypatch):
        _manifest(
            monkeypatch,
            {"chat_path": "/old/chat.gguf", "embed_path": "/old/never-had-it.gguf"},
        )
        assert local_models.gguf_paths_if_present() is None

    def test_reranker_helper_follows_the_move(self, models_dir, monkeypatch):
        _manifest(monkeypatch, {"reranker_path": "/Volumes/NAS/gguf/rerank.gguf"})
        assert local_models.reranker_gguf_path() == models_dir / "gguf" / "rerank.gguf"


class TestHealIsIdempotent:
    """Repairing a stale path must happen once, not on every read.

    `selected_gguf` returns an absolute path; the manifest stores a relative
    one. Comparing those directly never matches, so the manifest was rewritten
    (and the repair logged) on every single call.
    """

    def test_second_read_does_not_rewrite(self, models_dir, monkeypatch, tmp_path):
        import json

        store = tmp_path / "manifest.json"
        sel = {
            "chat_path": "/Volumes/NAS/gguf/chat.gguf",
            "embed_path": "/Volumes/NAS/gguf/embed.gguf",
        }
        store.write_text(json.dumps({"selection": sel}))
        monkeypatch.setattr(local_models, "load_manifest", lambda: json.loads(store.read_text()))
        writes = []
        monkeypatch.setattr(
            local_models, "save_manifest", lambda m: (writes.append(1), store.write_text(json.dumps(m)))
        )

        local_models._heal_selection_paths(json.loads(store.read_text())["selection"])
        assert writes == [1], "first read repairs"
        local_models._heal_selection_paths(json.loads(store.read_text())["selection"])
        assert writes == [1], "second read must be a no-op"


class TestSelectionWrites:
    def test_read_never_rewrites_the_manifest(self, models_dir, monkeypatch):
        """Healing runs once at boot (sync_embedding_infrastructure), not per read."""
        _manifest(monkeypatch, {"chat_path": "/old/chat.gguf", "embed_path": "/old/embed.gguf"})
        monkeypatch.setattr(local_models, "save_manifest", lambda m: pytest.fail("wrote on read"))
        assert local_models.gguf_paths_if_present() is not None

    def test_legacy_guess_is_persisted_once(self, models_dir, monkeypatch):
        _manifest(monkeypatch, {})
        monkeypatch.setattr(local_models, "CHAT_MODEL_ID", "org/repo/chat.gguf")
        monkeypatch.setattr(local_models, "EMBED_MODEL_ID", "org/repo/embed.gguf")
        writes = []
        monkeypatch.setattr(local_models, "save_manifest", writes.append)
        got = local_models.gguf_paths_if_present()
        assert got == {"chat": models_dir / "gguf" / "chat.gguf", "embed": models_dir / "gguf" / "embed.gguf"}
        assert writes == [{"selection": {"chat_path": "gguf/chat.gguf", "embed_path": "gguf/embed.gguf"}}]


class TestPruneMissingGgufs:
    """Deleting a GGUF from disk must not leave its manifest record behind."""

    def test_deleted_entries_are_dropped_and_present_ones_kept(self, models_dir, monkeypatch):
        man = {
            "gguf": {
                "chat.gguf": {"path": str(models_dir / "gguf" / "chat.gguf")},
                "gone.gguf": {"path": str(models_dir / "gguf" / "gone.gguf")},
                "embed.gguf": {},  # no recorded path: falls back to gguf/<name>
            }
        }
        monkeypatch.setattr(local_models, "load_manifest", lambda: man)
        writes = []
        monkeypatch.setattr(local_models, "save_manifest", writes.append)
        local_models._prune_missing_ggufs()
        assert writes == [{"gguf": {"chat.gguf": man["gguf"]["chat.gguf"], "embed.gguf": {}}}]

    def test_nothing_missing_writes_nothing(self, models_dir, monkeypatch):
        man = {"gguf": {"chat.gguf": {"path": "gguf/chat.gguf"}}}
        monkeypatch.setattr(local_models, "load_manifest", lambda: man)
        monkeypatch.setattr(local_models, "save_manifest", lambda m: pytest.fail("wrote with nothing to prune"))
        local_models._prune_missing_ggufs()
