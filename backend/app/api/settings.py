"""Runtime LLM settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.log import get_logger

logger = get_logger("API")
router = APIRouter()


class LLMSettings(BaseModel):
    """Request body for updating runtime LLM settings."""

    provider: str | None = None
    model: str | None = None
    ingestion_model: str | None = None
    base_url: str | None = None


@router.get("/api/v1/settings")
async def get_runtime_settings():
    """Return the current effective chat and ingestion LLM settings."""
    from app.core.config import settings
    from app.services.llm import llm_service

    return {
        "provider": settings.LLM_PROVIDER,
        "model": llm_service.get_chat_model() or settings.LLM_MODEL,
        "ingestion_model": llm_service.get_ingestion_model() or settings.LLM_MODEL,
        "base_url": settings.LLM_BASE_URL,
    }


@router.patch("/api/v1/settings")
async def update_runtime_settings(body: LLMSettings):
    """Update the active LLM provider, model, or base URL without restarting the server.

    Model-only changes take effect immediately (no client reinitialization needed).
    Provider or base URL changes trigger a full LLM client reinitialization.
    API keys are never accepted here — use the credentials API.
    """
    from app.core import runtime_config
    from app.core.config import settings
    from app.services.llm import llm_service

    overrides = runtime_config.load()

    provider_changed = bool(body.provider and body.provider != settings.LLM_PROVIDER)
    base_url_changed = bool(body.base_url and body.base_url != settings.LLM_BASE_URL)

    if body.provider is not None:
        overrides["provider"] = body.provider
        settings.LLM_PROVIDER = body.provider
    if body.model is not None:
        overrides["model"] = body.model
        settings.CHAT_MODEL = body.model
    if body.ingestion_model is not None:
        overrides["ingestion_model"] = body.ingestion_model
        settings.INGESTION_MODEL = body.ingestion_model
    if body.base_url is not None:
        overrides["base_url"] = body.base_url
        settings.LLM_BASE_URL = body.base_url

    runtime_config.save(overrides)

    if provider_changed or base_url_changed:
        llm_service.provider = settings.LLM_PROVIDER.lower()
        llm_service.init_clients()
        logger.info(
            "LLM clients reinitialized",
            extra={"provider": llm_service.provider, "base_url": settings.LLM_BASE_URL},
        )

    return {
        "provider": settings.LLM_PROVIDER,
        "model": settings.CHAT_MODEL or settings.LLM_MODEL,
        "ingestion_model": settings.INGESTION_MODEL or settings.LLM_MODEL,
        "base_url": settings.LLM_BASE_URL,
    }
