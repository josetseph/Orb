"""AI readiness is derived from real configuration, not a mode picked in Setup."""

import pytest

from app.core import config
from app.services import ai_gate


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)
    monkeypatch.setattr(config.settings, "LLM_BASE_URL", "", raising=False)
    monkeypatch.setattr(config.settings, "LLM_API_KEY", "", raising=False)
    # The mode is set to the value that used to block everything, to prove
    # nothing consults it any more.
    monkeypatch.setattr(config.settings, "AI_SETUP_MODE", "none", raising=False)
    monkeypatch.setattr(ai_gate, "_local_models_present", lambda: False)
    from app.services.credentials import credentials

    monkeypatch.setattr(credentials, "has", lambda _p: False)


class TestNoLongerGatedOnMode:
    def test_local_weights_are_enough(self, monkeypatch):
        """The case the old code got wrong: a usable model, mode still 'none'."""
        monkeypatch.setattr(ai_gate, "_local_models_present", lambda: True)
        assert config.settings.AI_SETUP_MODE == "none"
        assert ai_gate.ai_is_configured() is True

    def test_cloud_key_is_enough(self, monkeypatch):
        from app.services.credentials import credentials

        monkeypatch.setattr(credentials, "has", lambda p: p == "openai")
        assert ai_gate.ai_is_configured() is True

    def test_openai_compat_endpoint_is_enough(self, monkeypatch):
        monkeypatch.setattr(config.settings, "LLM_BASE_URL", "http://127.0.0.1:8080/v1")
        assert ai_gate.ai_is_configured() is True

    def test_nothing_configured_is_still_false(self):
        assert ai_gate.ai_is_configured() is False

    def test_a_mode_of_local_does_not_fake_readiness(self, monkeypatch):
        """The other direction the old code got wrong."""
        monkeypatch.setattr(config.settings, "AI_SETUP_MODE", "local")
        assert ai_gate.ai_is_configured() is False


class TestKBPinStillWins:
    def test_pinned_provider_checked_directly(self, monkeypatch):
        from app.services.credentials import credentials

        monkeypatch.setattr(credentials, "has", lambda p: p == "anthropic")

        class KB:
            llm_provider = "anthropic"

        assert ai_gate.ai_is_configured(KB()) is True


class TestLocalOnlyPrivacy:
    """Replaces AI_SETUP_MODE == 'local' for keeping media off the network."""

    @pytest.mark.parametrize("provider", ["local", "ollama", "lm_studio", "", None])
    def test_local_providers_stay_offline(self, monkeypatch, provider):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", provider)
        assert ai_gate.chat_is_local_only() is True

    @pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic", "openai_compat"])
    def test_cloud_providers_may_call_out(self, monkeypatch, provider):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", provider)
        assert ai_gate.chat_is_local_only() is False


class TestDerivedMode:
    def test_none_when_nothing_configured(self):
        assert ai_gate.derived_setup_mode() == "none"

    def test_local_when_weights_present(self, monkeypatch):
        monkeypatch.setattr(ai_gate, "_local_models_present", lambda: True)
        assert ai_gate.derived_setup_mode() == "local"

    def test_cloud_when_endpoint_configured(self, monkeypatch):
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", "openai_compat")
        monkeypatch.setattr(config.settings, "LLM_BASE_URL", "https://api.example.com/v1")
        assert ai_gate.derived_setup_mode() == "cloud"
