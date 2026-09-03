"""Cloud provider API-key endpoints.

The desktop shell owns the secrets: it encrypts them with the OS keychain and
pushes them here at boot and whenever the user edits one. The backend holds
them in memory only.

Key material is write-only across this API — nothing here ever returns a key.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.log import get_logger
from app.services.credentials import (
    CLOUD_PROVIDERS,
    SOURCE_ENV,
    SOURCE_KEYCHAIN,
    credentials,
    normalize_provider,
)

logger = get_logger("API")
router = APIRouter()


class CredentialInput(BaseModel):
    """A provider API key pushed in by the desktop shell."""

    api_key: str = Field(min_length=1, max_length=8192)
    # "keychain" for desktop pushes; "env" is reserved for the seeded fallback.
    source: str = SOURCE_KEYCHAIN


def _reload_llm_clients() -> None:
    """Rebuild clients so a key change takes effect without a restart."""
    try:
        from app.services.llm import llm_service

        llm_service.init_clients()
        llm_service.credentials_version = credentials.version
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # A provider with no key yet raises here; that is expected while the
        # user is still filling the form.
        logger.info("LLM clients not reloaded after credential change: %s", exc)


@router.get("/api/v1/credentials")
async def get_credentials():
    """Which providers have a key, and where it came from. Never the key itself."""
    return {"providers": credentials.status(), "known": list(CLOUD_PROVIDERS)}


@router.put("/api/v1/credentials/{provider}")
async def set_credential(provider: str, body: CredentialInput):
    """Store a provider key in memory for this session."""
    name = normalize_provider(provider)
    if name not in CLOUD_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Expected one of: {', '.join(CLOUD_PROVIDERS)}",
        )
    source = body.source if body.source in (SOURCE_KEYCHAIN, SOURCE_ENV) else SOURCE_KEYCHAIN
    try:
        credentials.set(name, body.api_key, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_llm_clients()
    return {"provider": name, "configured": True, "source": source}


@router.delete("/api/v1/credentials/{provider}")
async def delete_credential(provider: str):
    """Forget a provider key for this session."""
    name = normalize_provider(provider)
    if name not in CLOUD_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    existed = credentials.clear(name)
    _reload_llm_clients()
    return {"provider": name, "configured": False, "cleared": existed}
