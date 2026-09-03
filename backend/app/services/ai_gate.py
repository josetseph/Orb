"""AI setup gating — block chat/ingest/entity enrichment when AI is not configured."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings


_CLOUD_KEYS = {
    "openai": lambda: settings.OPENAI_API_KEY,
    "gemini": lambda: settings.GEMINI_API_KEY,
    "anthropic": lambda: settings.ANTHROPIC_API_KEY,
    "huggingface": lambda: settings.HUGGINGFACE_API_KEY,
}


def provider_is_configured(provider: str | None) -> bool:
    """Can ``provider`` answer right now (key present / GGUFs on disk)?"""
    name = (provider or "").lower().strip()
    if name in ("local", "ollama", "lm_studio"):
        try:
            from app.services.local_models import gguf_paths_if_present

            return gguf_paths_if_present() is not None
        except Exception:  # pylint: disable=broad-exception-caught
            return False
    getter = _CLOUD_KEYS.get(name)
    return bool(getter and getter())


def ai_is_configured(kb=None) -> bool:
    # A KB that pins its own provider is usable whenever that provider is,
    # regardless of the global Setup mode.
    kb_provider = getattr(kb, "llm_provider", None) if kb is not None else None
    if kb_provider:
        return provider_is_configured(kb_provider)
    mode = (settings.AI_SETUP_MODE or "none").lower().strip()
    if mode in ("none", "", "skip"):
        return False
    if mode == "local":
        # Local is only "configured" once chat+embed GGUFs are on disk.
        try:
            from app.services.local_models import gguf_paths_if_present

            return gguf_paths_if_present() is not None
        except Exception:  # pylint: disable=broad-exception-caught
            return False
    if mode in ("cloud", "hybrid"):
        # Cloud/hybrid needs at least one provider key or custom OpenAI-compat URL
        if settings.OPENAI_API_KEY or settings.GEMINI_API_KEY or settings.ANTHROPIC_API_KEY:
            return True
        provider = (settings.LLM_PROVIDER or "").lower().strip()
        if provider not in ("local", "ollama", "lm_studio", "none", ""):
            return True
        key = (settings.LLM_API_KEY or "").strip()
        if settings.LLM_BASE_URL and key and key not in ("local", "lm-studio", "ollama"):
            return True
        return bool(settings.LLM_BASE_URL)
    return False


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
