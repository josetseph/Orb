"""Unit tests for per-call output budgeting and per-KB GGUF resolution in LocalLlamaRuntime.

No llama.cpp: a fake resident model exposes only ``n_ctx()`` and ``tokenize()``.
"""

from pathlib import Path

import pytest

from app.services import local_models as lm


class _FakeLlama:
    def __init__(self, n_ctx: int):
        self._n = n_ctx

    def n_ctx(self):
        return self._n

    def tokenize(self, data: bytes, add_bos=False, special=True):
        # one token per whitespace-separated word
        return list(range(len(data.decode("utf-8").split())))


@pytest.fixture()
def runtime():
    rt = lm.LocalLlamaRuntime()
    rt._chat = _FakeLlama(1000)
    return rt


class TestOutputBudget:
    def test_budget_is_context_minus_prompt_and_margin(self, runtime):
        messages = [{"role": "user", "content": " ".join(["w"] * 100)}]
        budget = runtime._remaining_output_budget(messages)
        # 1000 - (100 + 8 per message + 4) - safety margin
        assert budget == 1000 - 112 - lm._GEN_SAFETY_MARGIN

    def test_prompt_that_fills_context_raises(self, runtime):
        messages = [{"role": "user", "content": " ".join(["w"] * 900)}]
        with pytest.raises(lm.PromptTooLongError):
            runtime._remaining_output_budget(messages)

    def test_count_tokens_without_model_uses_heuristic(self):
        rt = lm.LocalLlamaRuntime()
        assert rt.count_tokens("") == 0
        assert rt.count_tokens("a" * 40) == 11  # 40 // 4 + 1

    def test_count_tokens_uses_resident_tokenizer(self, runtime):
        assert runtime.count_tokens("one two three") == 3


class TestMaxTokensEnv:
    def test_unset_means_dynamic(self, monkeypatch):
        monkeypatch.delenv("ORB_LLAMA_MAX_TOKENS", raising=False)
        assert lm._default_chat_max_tokens() is None

    def test_explicit_cap_is_honoured(self, monkeypatch):
        monkeypatch.setenv("ORB_LLAMA_MAX_TOKENS", "2048")
        assert lm._default_chat_max_tokens() == 2048

    def test_garbage_is_ignored(self, monkeypatch):
        monkeypatch.setenv("ORB_LLAMA_MAX_TOKENS", "lots")
        assert lm._default_chat_max_tokens() is None


class TestResolveChatGguf:
    def test_default_ids_mean_setup_selection(self):
        rt = lm.LocalLlamaRuntime()
        assert rt.resolve_chat_gguf(None) is None
        assert rt.resolve_chat_gguf("") is None
        assert rt.resolve_chat_gguf("local-chat") is None

    def test_known_catalog_id_missing_on_disk_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lm, "resolve_models_dir", lambda: tmp_path)
        rt = lm.LocalLlamaRuntime()
        with pytest.raises(RuntimeError, match="not downloaded"):
            rt.resolve_chat_gguf("gemma4-e4b-q4")

    def test_known_catalog_id_on_disk_resolves(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lm, "resolve_models_dir", lambda: tmp_path)
        monkeypatch.setattr(lm, "_gguf_looks_complete", lambda dest, mid: True)
        rt = lm.LocalLlamaRuntime()
        from app.services.model_catalog import get_option

        opt = get_option("gemma4-e4b-q4")
        assert rt.resolve_chat_gguf("gemma4-e4b-q4") == tmp_path / "gguf" / opt.hf_file

    def test_embed_id_is_rejected(self):
        rt = lm.LocalLlamaRuntime()
        with pytest.raises(RuntimeError, match="not a chat model"):
            rt.resolve_chat_gguf("qwen3-embed-0.6b-q8")

    def test_explicit_gguf_path(self, tmp_path):
        from tests.unit.test_model_discovery import make_model

        f = make_model(tmp_path / "custom.gguf", name="Custom")
        assert lm.LocalLlamaRuntime().resolve_chat_gguf(str(f)) == Path(f)

    def test_a_file_that_is_not_really_a_gguf_is_refused(self, tmp_path):
        """Pinning must validate the file, not just its extension."""
        f = tmp_path / "fake.gguf"
        f.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="not a readable GGUF"):
            lm.LocalLlamaRuntime().resolve_chat_gguf(str(f))

    def test_unknown_name_falls_back_to_selection(self):
        assert lm.LocalLlamaRuntime().resolve_chat_gguf("something-else") is None


class TestJsonMode:
    def test_response_format_reaches_llama(self, runtime):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return iter([{"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]}])

        runtime._chat.create_chat_completion = fake
        raw = runtime._chat_completion_once(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=10,
            repeat_penalty=1.0,
            response_format={"type": "json_object"},
        )
        assert seen["response_format"] == {"type": "json_object"}
        assert raw["choices"][0]["message"]["content"] == "{}"

    def test_compat_client_forwards_response_format(self, runtime):
        seen = {}
        runtime.create_chat_completion = lambda **kw: seen.update(kw)
        lm.LocalOpenAICompat(runtime, "m").chat.completions.create(
            messages=[], response_format={"type": "json_object"}
        )
        assert seen["response_format"] == {"type": "json_object"}
