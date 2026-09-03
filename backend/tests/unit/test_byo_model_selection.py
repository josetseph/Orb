"""Bring-your-own local model: resolution, n_ctx clamping, and KB API validation.

Covers the path from "user drops a GGUF in the models folder" to "a KB can pin
it", including the failure modes that must be loud rather than silent.
"""

import pytest

from app.services import local_models as lm
from app.services import model_discovery as md
from tests.unit.test_gguf_metadata import CHAT_KV, build_gguf
from tests.unit.test_model_discovery import PAD, make_model


@pytest.fixture()
def models_dir(tmp_path, monkeypatch):
    """A MODELS_DIR containing one user-supplied GGUF."""
    root = tmp_path / "models"
    (root / "gguf").mkdir(parents=True)
    monkeypatch.setattr(lm, "resolve_models_dir", lambda: root)
    monkeypatch.setattr(md, "resolve_models_dir", lambda: root)
    md.clear_cache()
    return root


class TestResolveChatGguf:
    def test_none_and_placeholder_mean_setup_selection(self, models_dir):
        rt = lm.LocalLlamaRuntime()
        assert rt.resolve_chat_gguf(None) is None
        assert rt.resolve_chat_gguf("local-chat") is None

    def test_models_dir_relative_ref_resolves(self, models_dir):
        make_model(models_dir / "gguf" / "Mine.gguf", name="Mine")
        got = lm.LocalLlamaRuntime().resolve_chat_gguf("gguf/Mine.gguf")
        assert got == models_dir / "gguf" / "Mine.gguf"

    def test_absolute_path_outside_models_dir_resolves(self, models_dir, tmp_path):
        outside = make_model(tmp_path / "elsewhere" / "Custom.gguf", name="Custom")
        assert lm.LocalLlamaRuntime().resolve_chat_gguf(str(outside)) == outside

    def test_missing_pinned_file_raises_rather_than_falling_back(self, models_dir):
        """A KB pinned to a deleted model must fail loudly, not answer with another."""
        with pytest.raises(RuntimeError, match="No such file"):
            lm.LocalLlamaRuntime().resolve_chat_gguf("gguf/Gone.gguf")

    def test_embedding_model_is_refused(self, models_dir):
        make_model(models_dir / "gguf" / "E.gguf", name="Qwen3 Embedding 4B",
                   extra={"qwen3.pooling_type": 3})
        with pytest.raises(RuntimeError, match="embedding model"):
            lm.LocalLlamaRuntime().resolve_chat_gguf("gguf/E.gguf")

    def test_corrupt_file_is_refused(self, models_dir):
        (models_dir / "gguf" / "Bad.gguf").write_bytes(b"NOPE" + b"\0" * PAD)
        with pytest.raises(RuntimeError, match="not a readable GGUF"):
            lm.LocalLlamaRuntime().resolve_chat_gguf("gguf/Bad.gguf")

    def test_shard_continuation_is_refused(self, models_dir):
        make_model(models_dir / "gguf" / "Big-00002-of-00003.gguf")
        with pytest.raises(RuntimeError, match="first shard"):
            lm.LocalLlamaRuntime().resolve_chat_gguf("gguf/Big-00002-of-00003.gguf")

    def test_unknown_non_path_name_falls_back_to_selection(self, models_dir):
        assert lm.LocalLlamaRuntime().resolve_chat_gguf("some-unknown-model") is None

    def test_catalog_ids_still_work(self, models_dir, monkeypatch):
        from app.services.model_catalog import get_option

        opt = get_option("gemma4-e4b-q4")
        make_model(models_dir / "gguf" / opt.hf_file)
        monkeypatch.setattr(lm, "_gguf_looks_complete", lambda dest, mid: True)
        got = lm.LocalLlamaRuntime().resolve_chat_gguf("gemma4-e4b-q4")
        assert got == models_dir / "gguf" / opt.hf_file


class TestContextClamping:
    def test_ctx_lowered_to_the_models_trained_window(self, tmp_path):
        kv = {**CHAT_KV, "gemma4.context_length": 4096}
        path = build_gguf(tmp_path / "small.gguf", kv, pad_to=PAD)
        assert lm._clamp_ctx_to_model(path, 16384) == 4096

    def test_ctx_kept_when_model_supports_more(self, tmp_path):
        path = build_gguf(tmp_path / "big.gguf", CHAT_KV, pad_to=PAD)  # 131072
        assert lm._clamp_ctx_to_model(path, 16384) == 16384

    def test_unknown_context_length_leaves_request_untouched(self, tmp_path):
        path = build_gguf(tmp_path / "bare.gguf", {"general.architecture": "x"}, pad_to=PAD)
        assert lm._clamp_ctx_to_model(path, 16384) == 16384

    def test_unreadable_file_leaves_request_untouched(self, tmp_path):
        (tmp_path / "junk.gguf").write_bytes(b"nope")
        assert lm._clamp_ctx_to_model(tmp_path / "junk.gguf", 16384) == 16384


class TestKbApiValidation:
    """The KB endpoint must accept discovered models and reject unusable ones."""

    @staticmethod
    def _validate(mid: str):
        """Mirror of update_kb_llm's local-model branch."""
        from fastapi import HTTPException

        from app.services.model_catalog import chat_model_downloaded, get_option
        from app.services.model_discovery import inspect_chat_model, resolve_model_ref

        opt = get_option(mid)
        if opt is not None:
            if opt.role != "chat":
                raise HTTPException(status_code=400, detail="not a chat model")
            if not chat_model_downloaded(opt):
                raise HTTPException(status_code=400, detail="not downloaded")
            return
        path = resolve_model_ref(mid)
        if path is None:
            raise HTTPException(status_code=400, detail="neither a known model id nor a path")
        _info, error = inspect_chat_model(path)
        if error:
            raise HTTPException(status_code=400, detail=error)

    def test_discovered_model_is_accepted(self, models_dir):
        make_model(models_dir / "gguf" / "Mine.gguf", name="Mine")
        self._validate("gguf/Mine.gguf")  # must not raise

    def test_garbage_name_is_rejected_with_a_useful_message(self, models_dir):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._validate("totally-made-up")
        assert "neither a known model id nor a path" in exc.value.detail

    def test_embedding_model_rejected(self, models_dir):
        from fastapi import HTTPException

        make_model(models_dir / "gguf" / "E.gguf", name="Emb", extra={"qwen3.pooling_type": 3})
        with pytest.raises(HTTPException) as exc:
            self._validate("gguf/E.gguf")
        assert "embedding model" in exc.value.detail


class TestPayloadListing:
    def test_discovered_models_appear_alongside_catalog_entries(self, models_dir, monkeypatch):
        from app.api import kb as kb_api

        make_model(models_dir / "gguf" / "Mine.gguf", name="My Custom Model")
        monkeypatch.setattr("app.core.paths.resolve_models_dir", lambda: models_dir)
        rows = kb_api._local_chat_models()
        discovered = [r for r in rows if r["source"] == "discovered"]
        assert any(r["label"].startswith("My Custom Model") for r in discovered)
        assert all("id" in r and "label" in r for r in rows)

    def test_embedding_models_never_offered(self, models_dir, monkeypatch):
        from app.api import kb as kb_api

        make_model(models_dir / "gguf" / "E.gguf", name="Emb", extra={"qwen3.pooling_type": 3})
        monkeypatch.setattr("app.core.paths.resolve_models_dir", lambda: models_dir)
        assert kb_api._local_chat_models() == []
