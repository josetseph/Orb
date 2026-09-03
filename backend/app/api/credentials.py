"""Cloud provider API-key endpoints.

The desktop shell owns the secrets: it encrypts them with the OS keychain and
pushes them here at boot and whenever the user edits one. The backend holds
them in memory only.

Key material is write-only across this API — nothing here ever returns a key.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.log import get_logger
from app.services.credentials import (
    CLOUD_PROVIDERS,
    SOURCE_ENV,
    SOURCE_KEYCHAIN,
    InvalidEndpointError,
    credentials,
    endpoint_credential_id,
    normalize_base_url,
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


class EndpointCredentialInput(BaseModel):
    """An OpenAI-compatible endpoint and its key.

    The URL is the credential's identity, so the user never invents a name and
    two knowledge bases on different servers cannot share a key by accident.
    """

    base_url: str = Field(min_length=1, max_length=2048)
    # Servers such as llama-server or LM Studio accept any token.
    api_key: str = Field(default="not-needed", max_length=8192)


@router.get("/api/v1/credentials")
async def get_credentials():
    """Which providers have a key, and where it came from. Never the key itself."""
    return {
        "providers": credentials.status(),
        "known": list(CLOUD_PROVIDERS),
        "endpoints": credentials.endpoints(),
    }


@router.put("/api/v1/credentials/endpoint")
async def set_endpoint_credential(body: EndpointCredentialInput):
    """Store the key for one OpenAI-compatible endpoint."""
    try:
        base_url = normalize_base_url(body.base_url)
        credentials.set(endpoint_credential_id(base_url), body.api_key or "not-needed")
    except InvalidEndpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_llm_clients()
    return {"base_url": base_url, "configured": True}


@router.delete("/api/v1/credentials/endpoint")
async def delete_endpoint_credential(base_url: str = Query(min_length=1)):
    """Forget the key for one endpoint."""
    try:
        normalized = normalize_base_url(base_url)
        existed = credentials.clear(endpoint_credential_id(normalized))
    except InvalidEndpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_llm_clients()
    return {"base_url": normalized, "configured": False, "cleared": existed}


@router.get("/api/v1/llm/endpoint-models")
async def list_endpoint_models(base_url: str = Query(min_length=1)):
    """Ask an OpenAI-compatible server what models it serves.

    Most implement ``GET /v1/models``; when one does not, the caller falls back
    to a free-text model field rather than blocking the user.
    """
    try:
        normalized = normalize_base_url(base_url)
    except InvalidEndpointError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = credentials.get(endpoint_credential_id(normalized))
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{normalized}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach {normalized}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=(
                f"{normalized}/models returned {response.status_code}. "
                "Type the model name instead."
            ),
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail="Endpoint did not return JSON."
        ) from exc
    rows = payload.get("data") if isinstance(payload, dict) else payload
    models: list[str] = []
    for row in rows or []:
        model_id = row.get("id") if isinstance(row, dict) else row
        if isinstance(model_id, str) and model_id.strip():
            models.append(model_id.strip())
    return {"base_url": normalized, "models": sorted(set(models))}


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
