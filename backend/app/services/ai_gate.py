"""AI setup gating — block chat/ingest/entity enrichment when AI is not configured."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings


def provider_is_configured(provider: str | None) -> bool:
    """Can ``provider`` answer right now (key present / GGUFs on disk)?"""
    name = (provider or "").lower().strip()
    if name in ("local", "ollama", "lm_studio"):
        try:
            from app.services.local_models import gguf_paths_if_present

            return gguf_paths_if_present() is not None
        except Exception:  # pylint: disable=broad-exception-caught
            return False
    from app.services.credentials import credentials

    return credentials.has(name)


def _local_models_present() -> bool:
    try:
        from app.services.local_models import gguf_paths_if_present

        return gguf_paths_if_present() is not None
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _endpoint_is_configured() -> bool:
    """An OpenAI-compatible endpoint the user actually filled in.

    A key is not required: llama-server and LM Studio need none, and a remote
    endpoint missing its key fails loudly on the first call, which is a better
    error than "AI is not configured".
    """
    return bool((settings.LLM_BASE_URL or "").strip())


def chat_is_local_only() -> bool:
    """True when the effective chat provider runs on this device.

    Used to keep images and audio off the network for someone running purely
    local — replaces the old ``AI_SETUP_MODE == "local"`` reading of intent.
    """
    provider = (settings.LLM_PROVIDER or "local").lower().strip()
    return provider in ("local", "ollama", "lm_studio", "none", "")


def ai_is_configured(kb=None) -> bool:
    """Is anything actually able to answer right now?

    Derived from real configuration rather than a mode the user picks in Setup.
    A stored mode was a second source of truth that could contradict the Models
    page in both directions — "none" blocked a perfectly good model, and
    "local" claimed readiness with no weights on disk. What matters is whether
    a model is reachable, which is exactly what the Models page decides.
    """
    # A KB that pins its own provider is usable whenever that provider is.
    kb_provider = getattr(kb, "llm_provider", None) if kb is not None else None
    if kb_provider:
        return provider_is_configured(kb_provider)

    if _local_models_present():
        return True

    from app.services.credentials import CLOUD_PROVIDERS, credentials

    if any(credentials.has(p) for p in CLOUD_PROVIDERS):
        return True
    return _endpoint_is_configured()


def derived_setup_mode() -> str:
    """What Setup used to ask for, reported back as an observation.

    ``local`` / ``cloud`` / ``none`` purely for display and for callers that
    still record a mode; nothing gates on it.
    """
    if not ai_is_configured():
        return "none"
    return "local" if chat_is_local_only() and _local_models_present() else "cloud"


def require_ai(kb=None) -> None:
    """Raise 503 if AI features are unavailable (Obsidian-like limited mode)."""
    if not ai_is_configured(kb):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ai_not_configured",
                "message": (
                    "AI is not configured. Notes, wikilinks, and finance still work. "
                    "Open Setup to enable local models or a cloud provider."
                ),
            },
        )
