"""Unit tests for chat follow-up query rewrite and history shaping."""

import asyncio
from unittest.mock import patch

import pytest

from app.services.llm import LLMService


@pytest.fixture(scope="module")
def svc() -> LLMService:
    return LLMService.__new__(LLMService)


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


class TestIterativeStep:
    def test_answer_wins(self, svc):
        got = _step(svc, '{"reasoning": "r", "finding": "f", "answer": "42", "next_query": "ignored"}', [{"text": "d"}])
        assert got == {
            "reasoning": "r", "full_answer": "f", "can_answer": True,
            "final_answer": "42", "next_query": None, "thinking": "thought",
        }

    def test_next_query_when_no_answer(self, svc):
        got = _step(svc, '```json\n{"reasoning": "", "finding": null, "answer": null, "next_query": "who?"}\n```')
        assert not got["can_answer"] and got["next_query"] == "who?"
        assert got["full_answer"] == ""

    def test_non_answer_strings_do_not_count(self, svc):
        got = _step(svc, '{"answer": "INSUFFICIENT", "next_query": "null"}', [{"text": "d"}])
        assert not got["can_answer"] and got["next_query"] is None

    def test_garbage_is_a_no_op_step(self, svc):
        got = _step(svc, "not json at all")
        assert got["can_answer"] is False and got["next_query"] is None and got["thinking"] is None

    def test_prompt_asks_for_json(self, svc):
        with patch.object(svc, "_reason_step", return_value=("{}", None)) as reason:
            asyncio.run(svc.iterative_step("q?", [], "s", [{"text": "d"}]))
        prompt = reason.call_args.args[0]
        assert reason.call_args.kwargs == {"json_mode": True}
        assert '"next_query"' in prompt and "NEXT_QUERY" not in prompt
