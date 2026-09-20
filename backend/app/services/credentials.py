"""Cloud-provider API keys, persisted in the OS keychain via ``keyring``.

The user enters keys in Settings; the backend stores them in the keychain
(macOS Keychain, Windows Credential Manager, Secret Service) and keeps them
in memory for the session. They are never written under ``DATA_DIR`` — that
is frequently a synced folder — and never returned by the API, which only
reports whether a provider is configured.
"""

from __future__ import annotations

import json
import threading
from urllib.parse import unquote, urlsplit, urlunsplit

from app.core.log import get_logger

logger = get_logger("Credentials")

# Providers whose keys Orb manages. "local" needs no credential.
CLOUD_PROVIDERS: tuple[str, ...] = ("openai", "gemini", "anthropic", "huggingface")

SOURCE_KEYCHAIN = "keychain"


# OpenAI-compatible endpoints are identified by their URL rather than a name the
# user has to invent: two KBs pointing at different servers therefore get
# different keys automatically, and two pointing at the same one share a key.
ENDPOINT_PREFIX = "endpoint:"


class InvalidEndpointError(ValueError):
    """The supplied base URL is not a usable http(s) endpoint."""


def normalize_base_url(url: str) -> str:
    """Canonical form of an OpenAI-compatible endpoint.

    Lowercases scheme and host, drops a trailing slash and any query, so
    ``HTTPS://Api.Example.com/v1/`` and ``https://api.example.com/v1`` resolve
    to one credential. A fragment is kept as a *profile name*: ``…/v1#Work``
    and ``…/v1#Personal`` are two endpoints with two keys on one server.
    Never sent over the wire — see :func:`request_base_url`.
    """
    raw = (url or "").strip()
    if not raw:
        raise InvalidEndpointError("Endpoint URL must not be empty")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidEndpointError(
            f"Endpoint URL must start with http:// or https:// (got {raw!r})"
        )
    if not parts.hostname:
        raise InvalidEndpointError(f"Endpoint URL has no host: {raw!r}")
    netloc = parts.hostname.lower()
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/")
    # Decoded and whitespace-collapsed so the desktop keychain (which goes
    # through the browser URL parser) and this side agree on the identity.
    profile = " ".join(unquote(parts.fragment).split())
    return urlunsplit((parts.scheme.lower(), netloc, path, "", profile))


def request_base_url(url: str) -> str:
    """The URL to actually call: the normalised endpoint without its profile name."""
    return normalize_base_url(url).split("#", 1)[0]


def endpoint_credential_id(base_url: str) -> str:
    """Credential id for an OpenAI-compatible endpoint."""
    return f"{ENDPOINT_PREFIX}{normalize_base_url(base_url)}"


def is_endpoint_id(provider: str) -> bool:
    return (provider or "").startswith(ENDPOINT_PREFIX)


def normalize_provider(provider: str) -> str:
    name = (provider or "").strip()
    if is_endpoint_id(name):
        # Endpoint ids carry a URL; only the URL portion is canonicalised, since
        # a path can be case-sensitive.
        return endpoint_credential_id(name[len(ENDPOINT_PREFIX) :])
    name = name.lower()
    return {"google": "gemini", "hf": "huggingface"}.get(name, name)


_KEYRING_SERVICE = "Orb"
_KEYRING_INDEX = "__index__"  # keyring has no "list": the ids live in one JSON entry


class CredentialStore:
    """Thread-safe provider -> API key map, persisted in the OS keychain.

    macOS Keychain, Windows Credential Manager or Secret Service on Linux via
    ``keyring``. When no backend is usable the keys still work for this session.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keys: dict[str, str] = {}
        self._loaded = False
        # Bumped on every change so cached LLM clients can notice a new key.
        self._version = 0

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def _load_unlocked(self) -> None:
        """Load the keychain keys once."""
        if self._loaded:
            return
        self._loaded = True
        try:
            import keyring

            index = keyring.get_password(_KEYRING_SERVICE, _KEYRING_INDEX)
            for name in json.loads(index) if index else []:
                value = keyring.get_password(_KEYRING_SERVICE, name)
                if value:
                    self._keys[name] = value
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Keychain unavailable, credentials are session-only: %s", exc)

    def get(self, provider: str) -> str | None:
        name = normalize_provider(provider)
        with self._lock:
            self._load_unlocked()
            return self._keys.get(name)

    def has(self, provider: str) -> bool:
        return bool(self.get(provider))

    def set(self, provider: str, api_key: str) -> None:
        name = normalize_provider(provider)
        value = (api_key or "").strip()
        if not value:
            raise ValueError("API key must not be empty")
        with self._lock:
            self._load_unlocked()
            self._keys[name] = value
            self._version += 1
            self._persist_unlocked(name, value)
        # Length only — never the key itself.
        logger.info("Credential set for %s (%d chars)", name, len(value))

    def _persist_unlocked(self, name: str, value: str | None) -> None:
        """Write (or delete, when value is None) one key and keep the index in step."""
        try:
            import keyring

            if value is None:
                try:
                    keyring.delete_password(_KEYRING_SERVICE, name)
                except keyring.errors.PasswordDeleteError:
                    pass
            else:
                keyring.set_password(_KEYRING_SERVICE, name, value)
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_INDEX, json.dumps(sorted(self._keys)))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Could not update the keychain (key kept for this session): %s", exc)

    def clear(self, provider: str) -> bool:
        name = normalize_provider(provider)
        with self._lock:
            self._load_unlocked()
            existed = self._keys.pop(name, None) is not None
            if existed:
                self._version += 1
                self._persist_unlocked(name, None)
        if existed:
            logger.info("Credential cleared for %s", name)
        return existed

    def endpoints(self) -> list[str]:
        """Base URLs that currently have a stored key."""
        with self._lock:
            self._load_unlocked()
            return sorted(
                name[len(ENDPOINT_PREFIX) :]
                for name in self._keys
                if is_endpoint_id(name)
            )

    def status(self) -> dict[str, dict]:
        """Per-provider configuration state. Never includes key material."""
        with self._lock:
            self._load_unlocked()
            known = set(CLOUD_PROVIDERS) | set(self._keys)
            return {
                name: {
                    "configured": bool(self._keys.get(name)),
                    "source": SOURCE_KEYCHAIN if self._keys.get(name) else None,
                }
                for name in sorted(known)
            }

    def reset(self) -> None:
        """Drop everything (tests)."""
        with self._lock:
            self._keys.clear()
            self._loaded = False
            self._version += 1


credentials = CredentialStore()


def get_api_key(provider: str) -> str | None:
    return credentials.get(provider)
