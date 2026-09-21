"""Unit tests for per-KB LLM overrides: resolution and LLMService model routing."""

import pytest

from app.core import config
from app.services.llm import LLMService


@pytest.fixture(autouse=True)
def system_settings(monkeypatch):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)
    monkeypatch.setattr(config.settings, "LLM_MODEL", "gemma4-12b-q4", raising=False)
    monkeypatch.setattr(config.settings, "CHAT_MODEL", None, raising=False)
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
            # Only meaningful for OpenAI-compatible endpoints.
            "base_url": None,
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


class TestOpenAiCompatConfig:
    """A KB may point at any OpenAI-compatible server: URL + key + model name."""

    def test_endpoint_url_is_part_of_the_effective_config(self):
        from app.services.kb_registry import effective_llm_config

        eff = effective_llm_config(
            {
                "llm_provider": "openai_compat",
                "llm_base_url": "https://openrouter.ai/api/v1",
                "llm_model": "anthropic/claude-sonnet-4.5",
            }
        )
        assert eff["provider"] == "openai_compat"
        assert eff["base_url"] == "https://openrouter.ai/api/v1"
        assert eff["model"] == "anthropic/claude-sonnet-4.5"
        assert eff["inherited"] is False

    def test_base_url_alone_counts_as_an_override(self):
        from app.services.kb_registry import effective_llm_config

        assert effective_llm_config({"llm_base_url": "https://x.test/v1"})["inherited"] is False

    def test_non_compat_provider_reports_no_endpoint(self):
        from app.services.kb_registry import effective_llm_config

        assert effective_llm_config({"llm_provider": "gemini"})["base_url"] is None

    def test_openai_compat_is_an_allowed_provider(self):
        from app.services.kb_registry import LLM_PROVIDERS

        assert "openai_compat" in LLM_PROVIDERS


class TestLLMServiceEndpoint:
    def test_instance_base_url_wins_over_settings(self, monkeypatch):
        monkeypatch.setattr(config.settings, "LLM_BASE_URL", "https://global.test/v1", raising=False)
        svc = LLMService.__new__(LLMService)
        svc.provider = "openai_compat"
        svc._base_url_override = "https://per-kb.test/v1"
        assert svc.get_base_url() == "https://per-kb.test/v1"

    def test_falls_back_to_settings_base_url(self, monkeypatch):
        monkeypatch.setattr(config.settings, "LLM_BASE_URL", "https://global.test/v1/", raising=False)
        svc = LLMService.__new__(LLMService)
        svc.provider = "openai_compat"
        svc._base_url_override = None
        # Trailing slash is normalised away so it matches the stored credential.
        assert svc.get_base_url() == "https://global.test/v1"

    def test_malformed_url_is_ignored_rather_than_crashing(self, monkeypatch):
        monkeypatch.setattr(config.settings, "LLM_BASE_URL", "notaurl", raising=False)
        svc = LLMService.__new__(LLMService)
        svc.provider = "openai_compat"
        svc._base_url_override = None
        assert svc.get_base_url() is None

    def test_endpoint_without_a_key_uses_a_placeholder(self):
        """llama-server / LM Studio accept any token; the SDK demands one."""
        assert LLMService.get_endpoint_key("https://nokey.test/v1") == "not-needed"


def test_ingestion_follows_the_globally_selected_chat_model(monkeypatch):
    monkeypatch.setattr(config.settings, "CHAT_MODEL", "gguf/picked-in-settings.gguf", raising=False)
    assert _svc().get_ingestion_model() == "gguf/picked-in-settings.gguf"
