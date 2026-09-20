"""AI setup gating — block chat/ingest/entity enrichment when AI is not configured."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings


def provider_is_configured(provider: str | None, base_url: str | None = None) -> bool:
    """Can ``provider`` answer right now (endpoint set / key present / GGUFs on disk)?"""
    name = (provider or "").lower().strip()
    if name in ("local", "ollama", "lm_studio"):
        return _local_models_present()
    if name == "openai_compat":
        # An endpoint's credential is stored under "endpoint:<url>", never under
        # the literal "openai_compat", so the credential lookup below always
        # missed and a KB pinned to a working endpoint was refused with a 503.
        # What makes it usable is having a URL to call; a key is optional
        # (llama-server and LM Studio need none) and a remote endpoint missing
        # one fails loudly on the first call, which is the better error.
        return _endpoint_is_configured(base_url)
    from app.services.credentials import credentials

    return credentials.has(name)


def _local_models_present() -> bool:
    try:
        from app.services.local_models import gguf_paths_if_present

        return gguf_paths_if_present() is not None
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _endpoint_is_configured(base_url: str | None = None) -> bool:
    """An OpenAI-compatible endpoint the user actually filled in.

    ``base_url`` lets a per-KB pin be judged on its own endpoint rather than the
    system one. A key is not required: llama-server and LM Studio need none, and
    a remote endpoint missing its key fails loudly on the first call, which is a
    better error than "AI is not configured".
    """
    return bool((base_url or settings.LLM_BASE_URL or "").strip())


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
        return provider_is_configured(
            kb_provider, getattr(kb, "llm_base_url", None)
        )

    if _local_models_present():
        return True

    from app.services.credentials import CLOUD_PROVIDERS, credentials

    if any(credentials.has(p) for p in CLOUD_PROVIDERS):
        return True
    return _endpoint_is_configured()


def require_ai(kb=None) -> None:
    """Raise 503 if AI features are unavailable (Obsidian-like limited mode)."""
    if not ai_is_configured(kb):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ai_not_configured",
                "message": (
                    "AI is not configured. Notes, wikilinks, and finance still work. "
                    "Open Models to choose a local model or a cloud endpoint."
                ),
            },
        )
