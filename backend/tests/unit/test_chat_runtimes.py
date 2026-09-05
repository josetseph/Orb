"""Unit tests for the non-GGUF chat runtimes and format dispatch.

No real weights: the runtimes are exercised with fake models and tokenizers, so
these cover the parts Orb owns — residency, prompt rendering, output budgeting
and response shape — rather than mlx_lm or transformers themselves.
"""

import pytest

from app.services import chat_runtimes as cr
from app.services.model_formats import ModelFormat


class _FakeTokenizer:
    chat_template = "{% for m in messages %}{{ m.content }}{% endfor %}"

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        body = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        return f"{body}\nassistant:"

    def encode(self, text):
        return text.split()


class _FakeConfig:
    max_position_embeddings = 4096


class _FakeModel:
    config = _FakeConfig()


@pytest.fixture()
def runtime(monkeypatch):
    """An MLX runtime with a model already 'loaded', peers stubbed out."""
    rt = cr.MlxChatRuntime()
    rt._model = _FakeModel()
    rt._tokenizer = _FakeTokenizer()
    rt._path = None
    return rt


class TestPromptRendering:
    def test_uses_the_models_chat_template(self, runtime):
        prompt = runtime._render_prompt(
            [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}]
        )
        assert "system: be terse" in prompt and prompt.endswith("assistant:")

    def test_falls_back_when_the_template_raises(self, runtime):
        class Broken(_FakeTokenizer):
            def apply_chat_template(self, *a, **k):
                raise ValueError("no template")

        runtime._tokenizer = Broken()
        prompt = runtime._render_prompt([{"role": "user", "content": "hello"}])
        assert "User: hello" in prompt and prompt.endswith("Assistant:")

    def test_falls_back_without_a_tokenizer(self, runtime):
        runtime._tokenizer = None
        assert "Assistant:" in runtime._render_prompt([{"role": "user", "content": "x"}])


class TestOutputBudget:
    def test_budget_is_context_minus_prompt(self, runtime):
        prompt = " ".join(["w"] * 100)
        assert runtime._output_budget(prompt, None) == 4096 - 100 - cr._GEN_SAFETY_MARGIN

    def test_explicit_cap_is_honoured_when_smaller(self, runtime):
        assert runtime._output_budget("a b c", 128) == 128

    def test_prompt_that_fills_context_raises(self, runtime):
        from app.services.local_models import PromptTooLongError

        with pytest.raises(PromptTooLongError):
            runtime._output_budget(" ".join(["w"] * 4000), None)

    def test_missing_config_uses_a_safe_default(self, runtime):
        runtime._model = object()
        assert runtime.context_length() == cr._FALLBACK_CONTEXT


class TestTokenCounting:
    def test_uses_the_tokenizer(self, runtime):
        assert runtime.count_tokens("one two three") == 3

    def test_heuristic_without_a_tokenizer(self, runtime):
        runtime._tokenizer = None
        assert runtime.count_tokens("a" * 40) == 11

    def test_empty_text(self, runtime):
        assert runtime.count_tokens("") == 0


class TestResidency:
    def test_unload_clears_the_model(self, runtime, monkeypatch):
        monkeypatch.setattr(cr, "_release_memory", lambda: None)
        assert runtime.loaded is True
        runtime.unload()
        assert runtime.loaded is False and runtime.path is None

    def test_unload_is_idempotent(self, runtime, monkeypatch):
        monkeypatch.setattr(cr, "_release_memory", lambda: None)
        runtime.unload()
        runtime.unload()  # must not raise

    def test_unload_chat_runtimes_keeps_one(self, monkeypatch):
        monkeypatch.setattr(cr, "_release_memory", lambda: None)
        cr.mlx_chat_runtime._model = _FakeModel()
        cr.transformers_chat_runtime._model = _FakeModel()
        cr.unload_chat_runtimes(keep=cr.mlx_chat_runtime)
        assert cr.mlx_chat_runtime.loaded is True
        assert cr.transformers_chat_runtime.loaded is False
        cr.unload_chat_runtimes()
        assert cr.any_loaded() is False

    def test_idle_unload_respects_the_limit(self, runtime, monkeypatch):
        import time as _time

        monkeypatch.setattr(cr, "_release_memory", lambda: None)
        runtime._last_used = _time.monotonic()
        assert runtime.unload_if_idle(300) is False
        runtime._last_used = _time.monotonic() - 600
        assert runtime.unload_if_idle(300) is True

    def test_zero_limit_never_unloads(self, runtime):
        assert runtime.unload_if_idle(0) is False


class TestDispatch:
    def test_each_non_gguf_format_has_a_runtime(self):
        assert cr.runtime_for(ModelFormat.MLX) is cr.mlx_chat_runtime
        assert cr.runtime_for(ModelFormat.TRANSFORMERS) is cr.transformers_chat_runtime

    def test_gguf_is_not_served_here(self):
        assert cr.runtime_for(ModelFormat.GGUF) is None


class TestResponseShape:
    def test_matches_what_the_openai_shim_expects(self):
        response = cr._chat_response("hello", "some-model")
        choice = response["choices"][0]
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "hello"
        assert choice["finish_reason"] == "stop"

    def test_truncation_is_reported(self):
        assert cr._chat_response("x", "m", "length")["choices"][0]["finish_reason"] == "length"
