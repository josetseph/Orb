"""In-memory API-key store for cloud providers.

Keys reach the backend one of two ways:

* **Desktop (the product path).** The Electron shell owns the secrets: it
  encrypts them with the OS keychain (``safeStorage``) into
  ``DATA_DIR/credentials.enc`` and pushes them into the running API over
  localhost. The backend keeps them in memory only and never writes them to
  disk — ``DATA_DIR`` is frequently a synced folder (OneDrive/NAS), so a
  plaintext secret there would leave the machine.

* **Contributors / Docker.** Environment variables (optionally via
  ``backend/.env``) seed the store at first use. End users never edit ``.env``;
  this path exists so a checkout keeps working.

Key material is never returned by the API — only whether a provider is
configured, and where its key came from.
"""

from __future__ import annotations

import threading

from app.core.log import get_logger

logger = get_logger("Credentials")

# Providers whose keys Orb manages. "local" needs no credential.
CLOUD_PROVIDERS: tuple[str, ...] = ("openai", "gemini", "anthropic", "huggingface")

SOURCE_KEYCHAIN = "keychain"
SOURCE_ENV = "env"

# settings attribute that seeds each provider (legacy .env / env var path).
_ENV_SETTING = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
}


def normalize_provider(provider: str) -> str:
    name = (provider or "").strip().lower()
    return {"google": "gemini", "hf": "huggingface"}.get(name, name)


class CredentialStore:
    """Thread-safe provider -> API key map held only in memory."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keys: dict[str, str] = {}
        self._sources: dict[str, str] = {}
        self._seeded = False
        # Bumped on every change so cached LLM clients can notice a new key.
        self._version = 0

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def _seed_from_env_unlocked(self) -> None:
        """Import env/.env keys once, without overwriting pushed values."""
        if self._seeded:
            return
        self._seeded = True
        from app.core.config import settings

        for provider, attr in _ENV_SETTING.items():
            if provider in self._keys:
                continue
            value = (getattr(settings, attr, None) or "").strip()
            if value:
                self._keys[provider] = value
                self._sources[provider] = SOURCE_ENV
        seeded = sorted(k for k, v in self._sources.items() if v == SOURCE_ENV)
        if seeded:
            logger.info("Seeded credentials from environment for: %s", ", ".join(seeded))

    def get(self, provider: str) -> str | None:
        name = normalize_provider(provider)
        with self._lock:
            self._seed_from_env_unlocked()
            return self._keys.get(name)

    def has(self, provider: str) -> bool:
        return bool(self.get(provider))

    def set(self, provider: str, api_key: str, *, source: str = SOURCE_KEYCHAIN) -> None:
        name = normalize_provider(provider)
        value = (api_key or "").strip()
        if not value:
            raise ValueError("API key must not be empty")
        with self._lock:
            self._seed_from_env_unlocked()
            self._keys[name] = value
            self._sources[name] = source
            self._version += 1
        # Length only — never the key itself.
        logger.info("Credential set for %s (%s, %d chars)", name, source, len(value))

    def clear(self, provider: str) -> bool:
        name = normalize_provider(provider)
        with self._lock:
            self._seed_from_env_unlocked()
            existed = self._keys.pop(name, None) is not None
            self._sources.pop(name, None)
            if existed:
                self._version += 1
        if existed:
            logger.info("Credential cleared for %s", name)
        return existed

    def source(self, provider: str) -> str | None:
        name = normalize_provider(provider)
        with self._lock:
            self._seed_from_env_unlocked()
            return self._sources.get(name)

    def status(self) -> dict[str, dict]:
        """Per-provider configuration state. Never includes key material."""
        with self._lock:
            self._seed_from_env_unlocked()
            known = set(CLOUD_PROVIDERS) | set(self._keys)
            return {
                name: {
                    "configured": bool(self._keys.get(name)),
                    "source": self._sources.get(name),
                }
                for name in sorted(known)
            }

    def reset(self) -> None:
        """Drop everything, including the env seed (tests)."""
        with self._lock:
            self._keys.clear()
            self._sources.clear()
            self._seeded = False
            self._version += 1


credentials = CredentialStore()


def get_api_key(provider: str) -> str | None:
    return credentials.get(provider)


def require_api_key(provider: str) -> str:
    """Return the key or raise with a message pointing at the in-app setting."""
    key = credentials.get(provider)
    if not key:
        name = normalize_provider(provider)
        raise ValueError(
            f"No API key configured for {name}. Add it in Settings -> AI provider."
        )
    return key
