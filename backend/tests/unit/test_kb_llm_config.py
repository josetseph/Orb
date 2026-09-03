"""Unit tests for per-KB LLM overrides: resolution and LLMService model routing."""

import pytest

from app.core import config
from app.services.llm import LLMService


@pytest.fixture(autouse=True)
def system_settings(monkeypatch):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)
    monkeypatch.setattr(config.settings, "LLM_MODEL", "gemma4-12b-q4", raising=False)
    monkeypatch.setattr(config.settings, "CHAT_MODEL", None, raising=False)
    monkeypatch.setattr(config.settings, "INGESTION_MODEL", None, raising=False)
    # Default "local-chat" placeholder resolves to the Setup selection at runtime.
    monkeypatch.setattr(config.settings, "INGESTION_LLM_MODEL", None, raising=False)
    monkeypatch.setattr(config.settings, "GEMINI_MODEL", "gemini-2.5-flash", raising=False)
    monkeypatch.setattr(config.settings, "OPENAI_MODEL", None, raising=False)


def _svc(**overrides) -> LLMService:
    """LLMService without __init__ (no clients), with override attrs set directly."""
    svc = LLMService.__new__(LLMService)
    svc.provider = overrides.pop("provider", "local")
    svc._chat_model_override = overrides.pop("chat_model", None)
    svc._ingestion_model_override = overrides.pop("ingestion_model", None)
    return svc


class TestEffectiveLLMConfig:
    def test_inherits_system_when_nothing_pinned(self):
        from app.services.kb_registry import effective_llm_config

        eff = effective_llm_config({})
        assert eff == {
            "provider": "local",
            "model": "gemma4-12b-q4",
            "ingestion_model": "gemma4-12b-q4",
            "inherited": True,
        }

    def test_model_only_override_keeps_system_provider(self):
        from app.services.kb_registry import effective_llm_config

        eff = effective_llm_config({"llm_model": "gemma4-e4b-q4"})
        assert eff["provider"] == "local"
        assert eff["model"] == "gemma4-e4b-q4"
        # Ingestion follows the pinned chat model unless pinned separately.
        assert eff["ingestion_model"] == "gemma4-e4b-q4"
        assert eff["inherited"] is False

    def test_provider_override_uses_that_providers_system_default(self):
        from app.services.kb_registry import effective_llm_config

        eff = effective_llm_config({"llm_provider": "gemini"})
        assert eff["provider"] == "gemini"
        assert eff["model"] == "gemini-2.5-flash"

    def test_blank_strings_count_as_inherit(self):
        from app.services.kb_registry import effective_llm_config

        assert effective_llm_config({"llm_provider": "  ", "llm_model": ""})["inherited"] is True

    def test_global_chat_model_setting_applies_to_inheriting_kb(self, monkeypatch):
        from app.services.kb_registry import effective_llm_config

        monkeypatch.setattr(config.settings, "CHAT_MODEL", "custom-chat", raising=False)
        assert effective_llm_config({})["model"] == "custom-chat"


class TestLLMServiceOverrides:
    def test_no_override_reads_settings(self):
        assert _svc().get_chat_model() == "gemma4-12b-q4"
        assert _svc().get_ingestion_model() == "gemma4-12b-q4"

    def test_chat_override_wins_over_settings(self, monkeypatch):
        monkeypatch.setattr(config.settings, "CHAT_MODEL", "global-override", raising=False)
        assert _svc(chat_model="gemma4-e4b-q4").get_chat_model() == "gemma4-e4b-q4"

    def test_ingestion_falls_back_to_pinned_chat_model(self):
        svc = _svc(chat_model="gemma4-e4b-q4")
        assert svc.get_ingestion_model() == "gemma4-e4b-q4"

    def test_separate_ingestion_override(self):
        svc = _svc(chat_model="gemma4-12b-q4", ingestion_model="qwen35-4b-q4")
        assert svc.get_chat_model() == "gemma4-12b-q4"
        assert svc.get_ingestion_model() == "qwen35-4b-q4"

    def test_instances_built_via_new_still_work_without_override_attrs(self):
        svc = LLMService.__new__(LLMService)
        svc.provider = "local"
        assert svc.get_chat_model() == "gemma4-12b-q4"
