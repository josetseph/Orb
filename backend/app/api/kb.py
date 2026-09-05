"""Knowledge-base management endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_kb
from app.core.database import get_db
from app.core.log import get_logger
from app.models.note import Note
from app.models.wikilink import NoteLink
from app.services.firefly_service import firefly_service
from app.services.kb_registry import (
    LLM_PROVIDERS,
    KBContext,
    effective_llm_config,
    kb_registry,
)
from app.services.kb_registry import finance_enabled_for
from app.services.vault import clear_vault_contents, ensure_vault

logger = get_logger("API")
router = APIRouter()


class CreateKBInput(BaseModel):
    """Request body for creating a new knowledge base."""

    name: str
    vault_path: str | None = None


class RenameKBInput(BaseModel):
    """Request body for renaming a knowledge base."""

    name: str


class KBFinanceInput(BaseModel):
    """Turn the finance section on or off for one knowledge base."""

    enabled: bool


class KBLLMInput(BaseModel):
    """Per-KB LLM override. Empty / null / "inherit" fields fall back to Settings."""

    provider: str | None = None
    model: str | None = None
    ingestion_model: str | None = None
    # Only meaningful for provider="openai_compat".
    base_url: str | None = None


def _inherit(value: str | None) -> str | None:
    text = (value or "").strip()
    return None if text.lower() in ("", "inherit", "system", "default") else text


def _with_effective_llm(row: dict) -> dict:
    return {
        **row,
        "effective_llm": effective_llm_config(row),
        "finance_enabled": finance_enabled_for(row),
    }


def _local_chat_models() -> list[dict]:
    """Every chat GGUF on this machine — catalog downloads and user-added files.

    The curated catalog no longer gates the choice; it only contributes labels
    and the download flow. Anything discovered on disk is selectable.
    """
    from app.services.model_catalog import downloaded_chat_models
    from app.services.model_discovery import discover_chat_models, model_ref_for

    rows: list[dict] = []
    seen_paths: set[str] = set()
    for opt in downloaded_chat_models():
        from app.core.paths import resolve_models_dir

        path = resolve_models_dir() / "gguf" / opt.hf_file
        seen_paths.add(str(path.resolve()) if path.exists() else str(path))
        rows.append(
            {
                "id": opt.id,
                "label": opt.label,
                "size_gb": opt.size_gb,
                "source": "catalog",
                "warnings": [],
            }
        )
    for model in discover_chat_models():
        if str(Path(model.path).resolve()) in seen_paths:
            continue  # already listed under its curated catalog name
        rows.append(
            {
                "id": model.ref,
                "label": model.label,
                "size_gb": model.size_gb,
                "source": "discovered",
                "architecture": model.architecture,
                "context_length": model.context_length,
                "warnings": list(model.warnings),
            }
        )
    return rows


def _kb_llm_payload(kb_id: str) -> dict:
    from app.services.credentials import credentials

    meta = kb_registry.get_metadata(kb_id) or {}
    return {
        "kb_id": kb_id,
        "override": {
            "provider": meta.get("llm_provider"),
            "model": meta.get("llm_model"),
            "ingestion_model": meta.get("llm_ingestion_model"),
            "base_url": meta.get("llm_base_url"),
        },
        # Endpoints that already have a stored key, for the picker.
        "endpoints": credentials.endpoints(),
        "effective": effective_llm_config(meta),
        "providers": list(LLM_PROVIDERS),
        # Models already on this machine — pinning never triggers a download.
        "local_models": _local_chat_models(),
    }


@router.get("/api/v1/kb")
async def list_knowledge_bases():
    """List all registered knowledge bases (with their effective LLM)."""
    return {"knowledge_bases": [_with_effective_llm(r) for r in kb_registry.list_kbs()]}


@router.patch("/api/v1/kb/{kb_id}/finance")
async def update_kb_finance(kb_id: str, body: KBFinanceInput):
    """Turn finance on or off for one KB.

    Nothing is deleted either way: a KB switched off keeps its Firefly group,
    so turning it back on restores the accounts and transactions it had.
    """
    if kb_id != "default" and not kb_registry.get_metadata(kb_id):
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
    meta = kb_registry.set_finance_enabled(kb_id, body.enabled)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
    return {"kb_id": kb_id, "finance_enabled": body.enabled}


@router.get("/api/v1/kb/{kb_id}/llm")
async def get_kb_llm(kb_id: str):
    """Current per-KB LLM override, the resolved effective model, and pinnable local GGUFs."""
    if kb_id != "default" and not kb_registry.get_metadata(kb_id):
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
    return _kb_llm_payload(kb_id)


@router.patch("/api/v1/kb/{kb_id}/llm")
async def update_kb_llm(kb_id: str, body: KBLLMInput):
    """Pin (or clear) the chat / ingestion LLM for one knowledge base.

    Validates up front so a KB never points at a model it cannot run: local
    ids must be downloaded, cloud providers need their API key in ``.env``.
    """
    from app.services.ai_gate import provider_is_configured
    from app.services.model_catalog import chat_model_downloaded, get_option
    from app.services.model_discovery import inspect_chat_model, resolve_model_ref

    from app.services.credentials import InvalidEndpointError, normalize_base_url

    provider = _inherit(body.provider)
    model = _inherit(body.model)
    ingestion_model = _inherit(body.ingestion_model)
    base_url = _inherit(body.base_url)
    if provider is not None:
        provider = provider.lower()
        if provider not in LLM_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported provider '{provider}'. Choose one of: {', '.join(LLM_PROVIDERS)}",
            )
        if provider not in ("local", "openai_compat") and not provider_is_configured(
            provider
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No API key configured for {provider} — add one in "
                    "Settings → Cloud API keys first."
                ),
            )
    effective_provider = provider or effective_llm_config({})["provider"]

    if effective_provider == "openai_compat":
        resolved_url = base_url or effective_llm_config({}).get("base_url")
        if not resolved_url:
            raise HTTPException(
                status_code=400,
                detail=(
                    "An OpenAI-compatible provider needs an endpoint URL. "
                    "Add one in Settings, or set it for this knowledge base."
                ),
            )
        try:
            base_url = normalize_base_url(resolved_url) if base_url else base_url
        except InvalidEndpointError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not model:
            raise HTTPException(
                status_code=400,
                detail="Enter the model name this endpoint serves.",
            )
    elif base_url:
        # A stale URL left over from switching provider would be misleading.
        base_url = None

    if effective_provider == "local":
        for label, mid in (("model", model), ("ingestion_model", ingestion_model)):
            if not mid:
                continue
            opt = get_option(mid)
            if opt is not None:
                if opt.role != "chat":
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label} '{mid}' is a {opt.role} model, not a chat model.",
                    )
                if not chat_model_downloaded(opt):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{opt.label} is not downloaded — download it in Setup first.",
                    )
                continue
            # Not a catalog id: accept any GGUF on disk, but prove it can chat.
            path = resolve_model_ref(mid)
            if path is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{label} '{mid}' is neither a known model id nor a path to a "
                        ".gguf file."
                    ),
                )
            _info, error = inspect_chat_model(path)
            if error:
                raise HTTPException(status_code=400, detail=error)
    try:
        meta = kb_registry.set_llm_config(
            kb_id,
            provider=provider,
            model=model,
            ingestion_model=ingestion_model,
            base_url=base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_id}' not found")
    # Build the service now so a bad override surfaces here, not in the next chat.
    ctx = kb_registry.get_kb(kb_id)
    if ctx is not None and ctx.has_llm_override:
        try:
            ctx.llm  # noqa: B018 — construct for validation
        except Exception as exc:  # pylint: disable=broad-exception-caught
            kb_registry.set_llm_config(kb_id, provider=None, model=None, ingestion_model=None)
            raise HTTPException(
                status_code=400, detail=f"Could not initialise that model: {exc}"
            ) from exc
    return _kb_llm_payload(kb_id)


@router.post("/api/v1/kb", status_code=201)
async def create_knowledge_base(body: CreateKBInput):
    """Create a new knowledge base with its own notes vault folder.

    Does not re-run Setup — models / data dirs stay shared. Provision separate
    Kuzu/Qdrant/Meili stores for the new KB.
    """
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=400, detail="Knowledge base name must not be empty"
        )
    vault = (body.vault_path or "").strip()
    if not vault:
        raise HTTPException(
            status_code=400,
            detail="vault_path is required — choose where markdown notes for this KB are saved",
        )
    try:
        ctx = kb_registry.create_kb(body.name.strip(), vault_path=vault)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to create knowledge base '%s'", body.name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create knowledge base: {exc}",
        ) from exc
    return {
        "id": ctx.kb_id,
        "name": ctx.name,
        "vault_path": ctx.vault_path,
        "message": f"Knowledge base '{ctx.name}' created. Use ?kb={ctx.name} to target it.",
    }


async def _purge_kb_sql_notes(db: AsyncSession, kb_id: str) -> int:
    """Delete all SQLite notes and note_links for a KB. Returns note count."""
    result = await db.execute(select(Note.id).where(Note.kb_id == kb_id))
    ids = [row[0] for row in result.all()]
    await db.execute(delete(NoteLink).where(NoteLink.kb_id == kb_id))
    await db.execute(delete(Note).where(Note.kb_id == kb_id))
    await db.commit()
    return len(ids)


@router.post("/api/v1/kb/empty")
async def empty_knowledge_base(
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Full wipe of the current KB while keeping the KB registry row.

    Always deletes notes, vault contents, graph/search indexes, and Firefly admin.
    """
    vault_path = kb.vault_path or ""
    try:
        await firefly_service.destroy_kb_administration(kb)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[empty-kb] Firefly destroy failed: %s", exc)

    notes_removed = await _purge_kb_sql_notes(db, kb.kb_id)

    # Off the event loop: Kuzu holds a process-wide lock and Qdrant/Meili/vault
    # resets are network/disk-bound — inline they'd freeze chat streaming and
    # status polls for the duration.
    try:
        await asyncio.to_thread(kb.graph.wipe_all_nodes)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[empty-kb] Graph wipe failed: %s", exc)
    try:
        await asyncio.to_thread(kb.qdrant.reset_all)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[empty-kb] Qdrant reset failed: %s", exc)
    try:
        await asyncio.to_thread(kb.meili.reset_all)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[empty-kb] Meili reset failed: %s", exc)

    if vault_path:
        try:
            await asyncio.to_thread(clear_vault_contents, vault_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[empty-kb] Vault clear failed: %s", exc)
            try:
                ensure_vault(vault_path)
            except Exception as ensure_exc:  # pylint: disable=broad-exception-caught
                logger.warning("[empty-kb] ensure_vault failed: %s", ensure_exc)

    return {
        "status": "emptied",
        "kb_id": kb.kb_id,
        "name": kb.name,
        "notes_removed": notes_removed,
        "vault_path": vault_path,
        "message": (
            f"Emptied knowledge base '{kb.name}': notes, vault files, indexes, "
            "and Firefly administration removed. The KB itself remains."
        ),
    }


@router.post("/api/v1/kb/delete-non-default")
async def delete_all_non_default_knowledge_bases(
    db: AsyncSession = Depends(get_db),
):
    """Fully wipe and unregister every knowledge base except ``default``."""
    kbs = kb_registry.list_kbs()
    removed: list[dict] = []
    errors: list[dict] = []
    for entry in kbs:
        kid = entry.get("id")
        if not kid or kid == "default":
            continue
        try:
            meta = kb_registry.get_metadata(kid)
            ctx = kb_registry.get_kb(kid)
            if ctx is not None:
                try:
                    await firefly_service.destroy_kb_administration(ctx)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "[delete-non-default] Firefly destroy failed for %s: %s",
                        kid,
                        exc,
                    )
            await _purge_kb_sql_notes(db, kid)
            kb_registry.delete_kb(kid, delete_vault_files=True, wipe_indexes=True)
            removed.append(
                {
                    "id": kid,
                    "name": entry.get("name"),
                    "vault_path": (meta or {}).get("vault_path"),
                }
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append({"id": kid, "error": str(exc)})

    return {
        "removed": removed,
        "errors": errors,
        "removed_count": len(removed),
        "message": f"Deleted {len(removed)} knowledge base(s). Default KB was kept.",
    }


@router.delete("/api/v1/kb/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently delete a non-default knowledge base.

    Always destroys Firefly admin, SQLite notes, vault folder, and indexes.
    """
    if kb_id == "default":
        raise HTTPException(
            status_code=400, detail="The default knowledge base cannot be deleted"
        )
    meta = kb_registry.get_metadata(kb_id)
    if not meta:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_id}' not found"
        )

    ctx = kb_registry.get_kb(kb_id)
    if ctx is not None:
        try:
            await firefly_service.destroy_kb_administration(ctx)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[delete-kb] Firefly destroy failed: %s", exc)

    await _purge_kb_sql_notes(db, kb_id)

    deleted = kb_registry.delete_kb(
        kb_id,
        delete_vault_files=True,
        wipe_indexes=True,
    )
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_id}' not found"
        )


@router.patch("/api/v1/kb/{kb_id}")
async def rename_knowledge_base(kb_id: str, body: RenameKBInput):
    """Rename a knowledge base. The slug and UUID are unchanged; only the display name is updated."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name must not be empty")
    try:
        success = kb_registry.rename_kb(kb_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not success:
        raise HTTPException(
            status_code=404, detail=f"Knowledge base '{kb_id}' not found"
        )
    try:
        await firefly_service.sync_kb_group_title(kb_id, name)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Firefly group title sync failed after KB rename: %s", exc)
    return {"id": kb_id, "name": name}
