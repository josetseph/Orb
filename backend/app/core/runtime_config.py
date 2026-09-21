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
    "base_url": "LLM_BASE_URL",
}
# Local runtime knobs: key == lower-cased Settings attribute.
LOCAL_RUNTIME_KEYS: tuple[str, ...] = (
    "llama_n_ctx", "llama_max_tokens", "llama_swa_full", "llama_flash_attn",
    "llama_backend", "llama_n_gpu_layers", "llama_n_threads", "llama_repeat_penalty",
    "llama_prompt_reserve", "embed_n_ctx", "rerank_n_ctx", "model_idle_seconds",
    "extraction_chunk_tokens", "large_attachment_tokens",
)
_SETTING_FOR.update({k: k.upper() for k in LOCAL_RUNTIME_KEYS})
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
        # A stored null is meaningful for the local-runtime keys ("automatic").
        if key in overrides and (overrides[key] is not None or key in LOCAL_RUNTIME_KEYS):
            setattr(settings, attr, overrides[key])
