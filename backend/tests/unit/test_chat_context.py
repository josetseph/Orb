"""Unit tests for chat follow-up query rewrite and history shaping."""

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
