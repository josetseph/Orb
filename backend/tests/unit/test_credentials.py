"""Unit tests for the in-memory credential store and its write-only API.

The load-bearing property is that key material never leaves the process: the
API reports whether a provider is configured, never the key.
"""

import pytest

from app.core import config
from app.services.credentials import (
    CLOUD_PROVIDERS,
    SOURCE_ENV,
    SOURCE_KEYCHAIN,
    CredentialStore,
    normalize_provider,
)


@pytest.fixture()
def store(monkeypatch) -> CredentialStore:
    """A store with no environment keys seeded."""
    for attr in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "HUGGINGFACE_API_KEY"):
        monkeypatch.setattr(config.settings, attr, None, raising=False)
    return CredentialStore()


class TestBasicStorage:
    def test_unset_provider_has_no_key(self, store):
        assert store.get("openai") is None
        assert store.has("openai") is False

    def test_set_then_get(self, store):
        store.set("openai", "sk-test-123")
        assert store.get("openai") == "sk-test-123"
        assert store.has("openai") is True

    def test_keys_are_stripped(self, store):
        store.set("openai", "  sk-padded  ")
        assert store.get("openai") == "sk-padded"

    def test_empty_key_is_rejected(self, store):
        with pytest.raises(ValueError, match="must not be empty"):
            store.set("openai", "   ")

    def test_clear_removes_the_key(self, store):
        store.set("gemini", "g-1")
        assert store.clear("gemini") is True
        assert store.get("gemini") is None
        assert store.clear("gemini") is False

    def test_provider_names_are_normalised(self, store):
        store.set("OpenAI", "sk-1")
        assert store.get("openai") == "sk-1"
        assert normalize_provider("google") == "gemini"
        assert normalize_provider("hf") == "huggingface"
        store.set("google", "g-1")
        assert store.get("gemini") == "g-1"


class TestEnvSeeding:
    def test_env_keys_seed_the_store_once(self, monkeypatch):
        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-from-env", raising=False)
        monkeypatch.setattr(config.settings, "GEMINI_API_KEY", None, raising=False)
        monkeypatch.setattr(config.settings, "ANTHROPIC_API_KEY", None, raising=False)
        monkeypatch.setattr(config.settings, "HUGGINGFACE_API_KEY", None, raising=False)
        s = CredentialStore()
        assert s.get("openai") == "sk-from-env"
        assert s.source("openai") == SOURCE_ENV

    def test_pushed_key_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-from-env", raising=False)
        for attr in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "HUGGINGFACE_API_KEY"):
            monkeypatch.setattr(config.settings, attr, None, raising=False)
        s = CredentialStore()
        s.set("openai", "sk-from-keychain")
        assert s.get("openai") == "sk-from-keychain"
        assert s.source("openai") == SOURCE_KEYCHAIN

    def test_cleared_env_key_does_not_come_back(self, monkeypatch):
        monkeypatch.setattr(config.settings, "OPENAI_API_KEY", "sk-env", raising=False)
        for attr in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "HUGGINGFACE_API_KEY"):
            monkeypatch.setattr(config.settings, attr, None, raising=False)
        s = CredentialStore()
        assert s.clear("openai") is True
        assert s.get("openai") is None


class TestVersioning:
    def test_version_bumps_on_change_only(self, store):
        start = store.version
        store.get("openai")
        assert store.version == start  # reads do not bump
        store.set("openai", "a")
        after_set = store.version
        assert after_set > start
        store.clear("openai")
        assert store.version > after_set

    def test_clearing_absent_provider_does_not_bump(self, store):
        v = store.version
        store.clear("openai")
        assert store.version == v


class TestStatus:
    def test_status_never_contains_key_material(self, store):
        store.set("openai", "sk-super-secret-value")
        status = store.status()
        assert status["openai"] == {"configured": True, "source": SOURCE_KEYCHAIN}
        assert "sk-super-secret-value" not in repr(status)

    def test_status_lists_all_known_providers(self, store):
        assert set(CLOUD_PROVIDERS).issubset(store.status())

    def test_unconfigured_providers_report_false(self, store):
        assert store.status()["anthropic"]["configured"] is False


class TestRequireApiKey:
    def test_error_points_at_settings_not_dotenv(self, store, monkeypatch):
        import app.services.credentials as mod

        monkeypatch.setattr(mod, "credentials", store)
        with pytest.raises(ValueError) as exc:
            mod.require_api_key("openai")
        assert "Settings" in str(exc.value)
        assert ".env" not in str(exc.value)

    def test_returns_key_when_present(self, store, monkeypatch):
        import app.services.credentials as mod

        monkeypatch.setattr(mod, "credentials", store)
        store.set("openai", "sk-ok")
        assert mod.require_api_key("openai") == "sk-ok"


class TestEndpointIdentity:
    """OpenAI-compatible endpoints are keyed by URL, so no naming step is needed."""

    def test_url_is_canonicalised(self):
        from app.services.credentials import normalize_base_url

        assert normalize_base_url("HTTPS://Api.Example.COM/v1/") == "https://api.example.com/v1"
        assert normalize_base_url("  https://api.test/v1  ") == "https://api.test/v1"
        # The query goes; the fragment stays as a profile name (see test_endpoint_profiles).
        assert normalize_base_url("https://api.test/v1?x=1") == "https://api.test/v1"
        assert normalize_base_url("https://api.test/v1?x=1#f") == "https://api.test/v1#f"

    def test_port_is_preserved(self):
        from app.services.credentials import normalize_base_url

        assert normalize_base_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"

    def test_non_http_urls_are_rejected(self):
        from app.services.credentials import InvalidEndpointError, normalize_base_url

        for bad in ("ftp://x/v1", "file:///etc/passwd", "notaurl", "", "   "):
            with pytest.raises(InvalidEndpointError):
                normalize_base_url(bad)

    def test_equivalent_urls_share_one_credential(self, store):
        from app.services.credentials import endpoint_credential_id

        store.set(endpoint_credential_id("https://API.test/v1/"), "k-1")
        assert store.get(endpoint_credential_id("https://api.test/v1")) == "k-1"

    def test_different_endpoints_get_different_keys(self, store):
        from app.services.credentials import endpoint_credential_id

        store.set(endpoint_credential_id("https://openrouter.ai/api/v1"), "or-key")
        store.set(endpoint_credential_id("https://api.groq.com/openai/v1"), "groq-key")
        assert store.get(endpoint_credential_id("https://openrouter.ai/api/v1")) == "or-key"
        assert store.get(endpoint_credential_id("https://api.groq.com/openai/v1")) == "groq-key"

    def test_endpoints_are_listed_without_keys(self, store):
        from app.services.credentials import endpoint_credential_id

        store.set(endpoint_credential_id("https://openrouter.ai/api/v1"), "secret-or-key")
        assert store.endpoints() == ["https://openrouter.ai/api/v1"]
        assert "secret-or-key" not in repr(store.endpoints())
        assert store.has_endpoint("https://openrouter.ai/api/v1/") is True
        assert store.has_endpoint("https://other.test/v1") is False

    def test_malformed_endpoint_lookup_is_false_not_an_error(self, store):
        assert store.has_endpoint("notaurl") is False

    def test_endpoint_keys_do_not_collide_with_provider_names(self, store):
        from app.services.credentials import endpoint_credential_id

        store.set("openai", "provider-key")
        store.set(endpoint_credential_id("https://api.test/v1"), "endpoint-key")
        assert store.get("openai") == "provider-key"
        assert store.status()["openai"]["configured"] is True
