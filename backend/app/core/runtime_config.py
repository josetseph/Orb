"""Persistent runtime configuration overrides.

Stored under DATA_DIR/runtime_config.json (desktop) with a repo data/ fallback.
API keys are never stored here — those live in the OS keychain.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.config import BACKEND_DIR, settings
from app.core.log import get_logger

logger = get_logger("RuntimeConfig")

_lock = threading.Lock()

_SETTING_FOR = {
    "provider": "LLM_PROVIDER",
    "model": "CHAT_MODEL",
    "ingestion_model": "INGESTION_MODEL",
    "base_url": "LLM_BASE_URL",
}
MUTABLE_KEYS: frozenset[str] = frozenset(_SETTING_FOR)


def _data_path() -> Path:
    try:
        from app.core.paths import resolve_data_dir

        return resolve_data_dir() / "runtime_config.json"
    except Exception:  # pylint: disable=broad-exception-caught
        return BACKEND_DIR.parent / "data" / "runtime_config.json"


def load() -> dict:
    """Return saved overrides from disk, or ``{}`` if none exist."""
    path = _data_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k in MUTABLE_KEYS}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load runtime config: %s", exc)
    return {}


def save(overrides: dict) -> None:
    """Persist overrides to disk (only allowed keys are written)."""
    safe = {k: v for k, v in overrides.items() if k in MUTABLE_KEYS}
    path = _data_path()
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save runtime config: %s", exc)


def apply_to_settings(overrides: dict) -> None:
    """Mutate the global ``settings`` object with the given overrides."""
    for key, attr in _SETTING_FOR.items():
        if overrides.get(key) is not None:
            setattr(settings, attr, overrides[key])
