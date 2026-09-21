"""Unit tests for chat follow-up query rewrite and history shaping."""

import asyncio
from unittest.mock import patch

import pytest

from app.services.llm import LLMService


@pytest.fixture(scope="module")
def svc() -> LLMService:
    service = LLMService.__new__(LLMService)
    service.get_chat_model = lambda: "test-model"
    return service


class TestRewriteFollowUpQuery:
    def test_returns_latest_when_no_history(self, svc):
        assert svc.rewrite_follow_up_query([], "Who are its partners?") == "Who are its partners?"

    def test_returns_latest_when_empty_query(self, svc):
        history = [{"role": "user", "content": "Tell me about Fido."}]
        assert svc.rewrite_follow_up_query(history, "   ") == ""

    def test_uses_llm_rewrite_when_history_present(self, svc):
        history = [
            {"role": "user", "content": "Tell me everything about Fido."},
            {"role": "assistant", "content": "Fido is a Canadian fintech company."},
        ]
        with patch.object(
            svc,
            "_reason_step_sync",
            return_value="Fido partners and institutional support",
        ):
            result = svc.rewrite_follow_up_query(history, "Who are its partners?")
        assert result == "Fido partners and institutional support"

    def test_falls_back_to_latest_on_llm_failure(self, svc):
        history = [{"role": "user", "content": "Tell me about Fido."}]
        with patch.object(svc, "_reason_step_sync", side_effect=RuntimeError("offline")):
            result = svc.rewrite_follow_up_query(history, "What about its partners?")
        assert result == "What about its partners?"

    def test_ignores_invalid_roles_and_empty_content(self, svc):
        history = [
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Fido overview."},
        ]
        with patch.object(svc, "_reason_step_sync", return_value="Fido partners") as mock_reason:
            svc.rewrite_follow_up_query(history, "Partners?")
        prompt = mock_reason.call_args[0][0]
        assert "ignored" not in prompt
        assert "Fido overview." in prompt

    def test_truncates_long_history_turns(self, svc):
        long_text = "x" * 800
        history = [{"role": "user", "content": long_text}]
        with patch.object(svc, "_reason_step_sync", return_value="short query") as mock_reason:
            svc.rewrite_follow_up_query(history, "Follow up")
        prompt = mock_reason.call_args[0][0]
        assert "..." in prompt
        assert long_text not in prompt


# ── json_mode + <think> handling in _chat ────────────────────────────────────


class _FakeOpenAI:
    """Records the kwargs of one chat.completions.create call and returns ``text``."""

    def __init__(self, text: str):
        self.seen: dict = {}
        create = self._create
        self.chat = type("Chat", (), {"completions": type("C", (), {"create": staticmethod(create)})()})()
        self._text = text

    def _create(self, **kwargs):
        self.seen = kwargs
        msg = type("M", (), {"content": self._text, "reasoning_content": None})()
        return type("R", (), {"choices": [type("Ch", (), {"message": msg, "finish_reason": "stop"})()]})()


def _openai_svc(text: str) -> tuple[LLMService, _FakeOpenAI]:
    svc = LLMService.__new__(LLMService)
    svc.provider = svc.ingestion_provider = "openai"
    svc._chat_model_override = "m"
    svc._base_url_override = None
    svc.chat_client = svc.i_chat_client = _FakeOpenAI(text)
    return svc, svc.chat_client


class TestJsonMode:
    def test_response_format_lands_and_prompt_gets_json_guard(self):
        svc, client = _openai_svc('{"a": 1}')
        text, _ = svc._chat([{"role": "user", "content": "give me a thing"}], json_mode=True)
        assert client.seen["response_format"] == {"type": "json_object"}
        assert client.seen["messages"][-1]["content"].endswith("Respond with a JSON object.")
        assert text == '{"a": 1}'

    def test_prompt_already_naming_json_is_untouched(self):
        svc, client = _openai_svc("{}")
        svc._chat([{"role": "user", "content": "Return a JSON object"}], json_mode=True)
        assert client.seen["messages"][-1]["content"] == "Return a JSON object"

    def test_off_by_default(self):
        svc, client = _openai_svc("hi")
        svc._chat([{"role": "user", "content": "x"}])
        assert "response_format" not in client.seen

    def test_unclosed_think_is_cut_to_end(self):
        svc, _ = _openai_svc("answer <think>reasoning that got truncated")
        text, meta = svc._chat([{"role": "user", "content": "x"}])
        assert text == "answer"
        assert meta["thinking"] == "reasoning that got truncated"

    def test_closed_think_still_stripped(self):
        svc, _ = _openai_svc("<think>why</think>\nanswer")
        text, meta = svc._chat([{"role": "user", "content": "x"}])
        assert (text, meta["thinking"]) == ("answer", "why")


# ── iterative_step parses the JSON research step ─────────────────────────────


def _step(svc, raw: str, docs=None):
    with patch.object(svc, "_reason_step", return_value=(raw, "thought")):
        return asyncio.run(
            svc.iterative_step("q?", [], "search" if docs else None, docs or [], tried_queries=["search"])
        )


NO_OP = {"reasoning": "", "full_answer": "", "can_answer": False, "final_answer": None, "next_query": None, "thinking": None}


class TestIterativeStep:
    """The step reply is used exactly as written, or the step produces nothing."""

    def test_an_answer_ends_the_loop(self, svc):
        got = _step(svc, '{"reasoning": "r", "finding": "f", "answer": "42", "next_query": null}', [{"text": "d"}])
        assert got == {
            "reasoning": "r", "full_answer": "f", "can_answer": True,
            "final_answer": "42", "next_query": None, "thinking": "thought",
        }

    def test_a_next_query_continues_it(self, svc):
        got = _step(svc, '{"reasoning": "", "finding": "", "answer": null, "next_query": "who?"}')
        assert not got["can_answer"] and got["next_query"] == "who?"

    def test_an_answer_is_never_reinterpreted(self, svc):
        """"INSUFFICIENT" used to be read as "no answer". It is what the model answered."""
        got = _step(svc, '{"reasoning": "r", "finding": "f", "answer": "INSUFFICIENT", "next_query": null}', [{"text": "d"}])
        assert got["final_answer"] == "INSUFFICIENT"

    @pytest.mark.parametrize(
        "raw",
        [
            "not json at all",
            '```json\n{"reasoning": "", "finding": "", "answer": null, "next_query": "who?"}\n```',
            '{"reasoning": "r", "finding": "f", "answer": "42", "next_query": "also this"}',
            '{"reasoning": "r", "finding": "f", "answer": null, "next_query": null}',
            '{"reasoning": "", "finding": null, "answer": null, "next_query": "who?"}',
            '{"answer": "42", "next_query": null}',
        ],
        ids=["garbage", "code-fence", "both-outcomes", "no-outcome", "null-finding", "missing-keys"],
    )
    def test_an_unusable_reply_is_a_counted_no_op(self, svc, raw, tmp_path, monkeypatch):
        from app.core.config import settings
        from app.services import model_output

        monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
        assert _step(svc, raw, [{"text": "d"}]) == NO_OP
        assert model_output.failure_log().read_text().count('"stage": "research step"') == 1

    def test_runtime_errors_propagate(self, svc):
        with patch.object(svc, "_reason_step", side_effect=RuntimeError("no GGUF")):
            with pytest.raises(RuntimeError, match="no GGUF"):
                asyncio.run(svc.iterative_step("q?", [], "s", [{"text": "d"}]))

    def test_prompt_asks_for_json(self, svc):
        with patch.object(svc, "_reason_step", return_value=("{}", None)) as reason:
            asyncio.run(svc.iterative_step("q?", [], "s", [{"text": "d"}]))
        prompt = reason.call_args.args[0]
        assert reason.call_args.kwargs == {"json_mode": True}
        assert '"next_query"' in prompt and "NEXT_QUERY" not in prompt
