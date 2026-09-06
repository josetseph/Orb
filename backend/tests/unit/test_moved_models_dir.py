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
