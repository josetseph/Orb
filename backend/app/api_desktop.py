"""Notes graph + desktop setup routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_kb
from app.core.database import get_db
from app.core.paths import (
    resolve_data_dir,
    resolve_models_dir,
    save_paths_file,
)
from app.models.note import Note
from app.schemas.extraction import NoteInput
from app.services.kb_registry import KBContext, kb_registry
from app.services.note_files import note_body
from app.services.vault import ensure_vault
from app.services.wikilinks import (
    note_neighborhood_payload,
    notes_graph_payload,
    rebuild_kb_note_links,
)

router = APIRouter()


# ── Setup / paths ─────────────────────────────────────────────────────────────


class PathsInput(BaseModel):
    data_dir: str
    models_dir: str
    default_vault_path: str | None = None
    ai_setup_mode: str | None = None


@router.get("/api/v1/setup/status")
async def setup_status():
    from app.core.config import settings
    from app.core.paths import (
        paths_json_location,
        resolve_default_vault_path,
    )
    from app.services.ai_gate import ai_is_configured, derived_setup_mode
    from app.services.local_models import gguf_paths_if_present
    from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path
    gguf = gguf_paths_if_present()
    local_models_ready = gguf is not None
    multimodal_ready = all(
        is_hf_snapshot_ready(multimodal_model_path(k))
        for k in ("asr", "marlin")
    )
    mode = derived_setup_mode()
    vault = resolve_default_vault_path()
    default_kb = kb_registry.get_kb_by_name("default")
    return {
        "data_dir": str(resolve_data_dir()),
        "models_dir": str(resolve_models_dir()),
        "paths_json": str(paths_json_location()),
        "default_vault_path": str(vault) if vault else "",
        "active_vault_path": (default_kb.vault_path if default_kb else "") or "",
        # Derived from what is actually configured (see ai_gate), not from a
        # mode the user picks — Setup no longer asks.
        "ai_setup_mode": mode,
        "ai_configured": ai_is_configured(),
        "local_models_ready": local_models_ready,
        "multimodal_ready": multimodal_ready,
        "needs_model_download": mode == "local" and not local_models_ready,
        "database_backend": settings.DATABASE_BACKEND,
        "llm_provider": settings.LLM_PROVIDER,
    }


class DownloadModelsInput(BaseModel):
    include_multimodal: bool = True
    chat_id: str | None = None
    # When True, skip GGUF ensure (used for background Qwen3-ASR/Marlin + projector).
    multimodal_only: bool = False


@router.get("/api/v1/setup/model-catalog")
async def model_catalog(chat_id: str | None = None):
    """Hardware profile + filtered chat options + auto embed/reranker picks."""
    from app.services.model_catalog import recommend_stack

    return recommend_stack(chat_id)


@router.post("/api/v1/setup/download-models")
async def download_models(body: DownloadModelsInput | None = None):
    """Download chat/embed/rerank GGUFs (+ Qwen3-ASR/Marlin weights and the vision projector).

    Does not install multimodal Python deps — that is a separate step
    (``start-multimodal-services`` prepares the in-process runtime). Keeping
    this endpoint to file downloads avoids long pip installs hanging Setup.
    """
    from app.services.local_models import ensure_chat_and_embed_models, gguf_paths_if_present
    from app.services.multimodal_models import ensure_multimodal_models

    chat_id = body.chat_id if body else None
    include_mm = body.include_multimodal if body else True
    multimodal_only = body.multimodal_only if body else False
    progress: list[dict] = []

    def on_progress(label: str, pct: int) -> None:
        progress.append({"model": label, "percent": pct})

    paths: dict = {}
    if multimodal_only:
        present = gguf_paths_if_present() or {}
        paths = {k: v for k, v in present.items()}
    else:
        try:
            paths = await asyncio.to_thread(
                ensure_chat_and_embed_models, on_progress, chat_id=chat_id
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise HTTPException(
                status_code=500, detail=f"GGUF download failed: {exc}"
            ) from exc

    multimodal: dict = {}
    multimodal_error: str | None = None
    if include_mm or multimodal_only:
        try:
            mm_paths = await asyncio.to_thread(
                lambda: ensure_multimodal_models(
                    include_marlin=True, on_progress=on_progress
                )
            )
            multimodal = {k: str(v) for k, v in mm_paths.items()}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            multimodal_error = str(exc)
        # The vision projector belongs to the selected chat GGUF; the full
        # path above fetches it with the model, this one covers "media only".
        if multimodal_only:
            try:
                from app.services.local_models import ensure_mmproj, resolve_selected_hf_paths

                hf_chat = resolve_selected_hf_paths(chat_id)["chat"]
                proj = await asyncio.to_thread(
                    ensure_mmproj, hf_chat, lambda pct: on_progress("vision", pct)
                )
                if proj:
                    multimodal["vision"] = str(proj)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                multimodal_error = multimodal_error or f"vision projector: {exc}"
    return {
        "status": "ok",
        "chat": str(paths.get("chat", "")),
        "embed": str(paths.get("embed", "")),
        "reranker": str(paths.get("reranker", "")),
        "multimodal": multimodal,
        # Compat key — historically named "services"; runtime is in-process.
        "multimodal_services": {
            "started": False,
            "deferred": True,
            "mode": "in_process",
            "hint": (
                "Call /setup/start-multimodal-services?install_deps=true "
                "to prepare the in-process Qwen3-ASR/Marlin runtime"
            ),
        },
        "multimodal_error": multimodal_error,
        "progress": progress[-40:],
        "warning": (
            (
                "Chat/embed/rerank downloaded, but multimedia models had an issue "
                f"({multimodal_error})."
            )
            if multimodal_error
            else None
        ),
    }


@router.post("/api/v1/setup/select-chat-model")
async def select_chat_model(body: DownloadModelsInput | None = None):
    """Persist chat selection (+ auto embed/rerank ids) without downloading.

    Also resizes Qdrant collections to match the embed model's dimensions.
    """
    from app.services.local_models import resolve_selected_hf_paths, save_selection

    chat_id = body.chat_id if body else None
    if not chat_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="chat_id required")
    resolved = resolve_selected_hf_paths(chat_id)
    infra = save_selection(
        resolved["chat_id"],
        resolved["embed_id"],
        resolved["reranker_id"],
        embedding_dims=int(resolved["embedding_dims"]),
    )
    return {
        "status": "ok",
        "selection": {
            "chat_id": resolved["chat_id"],
            "embed_id": resolved["embed_id"],
            "reranker_id": resolved["reranker_id"],
            "embedding_dims": resolved["embedding_dims"],
        },
        "infrastructure": infra if isinstance(infra, dict) else None,
    }


@router.post("/api/v1/setup/start-multimodal-services")
async def start_multimodal_services(install_deps: bool = Query(True)):
    """Prepare Qwen3-ASR/Marlin for in-process load (no HTTP sidecars)."""
    from app.services.multimodal_services import ensure_multimodal_services

    try:
        return await asyncio.to_thread(
            lambda: ensure_multimodal_services(
                install_deps=install_deps, start_marlin=True
            )
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"started": False, "mode": "in_process", "error": str(exc)}


@router.get("/api/v1/setup/multimodal-status")
async def multimodal_status():
    from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path
    from app.services.multimodal_services import services_ready

    return {
        "mode": "in_process",
        "models": {
            "asr": is_hf_snapshot_ready(multimodal_model_path("asr")),
            "marlin": is_hf_snapshot_ready(multimodal_model_path("marlin")),
        },
        "services": services_ready(),
    }


@router.post("/api/v1/setup/start-local-llm")
async def start_local_llm(body: DownloadModelsInput | None = None):
    """Download (if needed) and load selected GGUFs in-process."""
    from app.services.embedding import embedding_service
    from app.services.llm import llm_service
    from app.services.local_models import (
        detect_llama_backend,
        ensure_chat_and_embed_models,
        local_gguf_reranker,
        local_llama_runtime,
    )

    chat_id = body.chat_id if body else None
    paths = await asyncio.to_thread(ensure_chat_and_embed_models, None, chat_id)

    def _load() -> dict:
        loaded = local_llama_runtime.load(paths["chat"], paths["embed"])
        try:
            local_gguf_reranker.ensure_loaded()
            loaded["reranker"] = str(paths.get("reranker", ""))
            loaded["reranker_loaded"] = local_gguf_reranker.loaded
        except Exception as exc:  # pylint: disable=broad-exception-caught
            loaded["reranker_loaded"] = False
            loaded["reranker_error"] = str(exc)
        return loaded

    try:
        result = await asyncio.to_thread(_load)
    except RuntimeError as exc:
        return {
            "started": False,
            "loaded": False,
            "reason": str(exc),
            "accel": detect_llama_backend(),
        }
    llm_service.provider = "local"
    llm_service.init_clients()
    embedding_service.reconfigure()
    return result


@router.post("/api/v1/setup/paths")
async def setup_paths(body: PathsInput):
    from app.core import runtime_config
    from app.core.config import settings
    from app.core.log import reconfigure_logging
    from app.core.paths import sync_settings_paths
    from app.services.kb_registry import DEFAULT_KB_ID, kb_registry

    save_paths_file(
        body.data_dir,
        body.models_dir,
        body.default_vault_path,
        ai_setup_mode=body.ai_setup_mode,
    )
    sync_settings_paths(settings)
    reconfigure_logging()
    vault_out = ""
    if body.default_vault_path:
        ensure_vault(body.default_vault_path)
        updated = kb_registry.set_vault_path(DEFAULT_KB_ID, body.default_vault_path)
        vault_out = updated.vault_path if updated else body.default_vault_path
    if body.ai_setup_mode:
        settings.AI_SETUP_MODE = body.ai_setup_mode
        overrides = runtime_config.load()
        overrides["ai_setup_mode"] = body.ai_setup_mode
        runtime_config.save(overrides)
        runtime_config.apply_to_settings(overrides)
    return {
        "status": "ok",
        "data_dir": str(Path(body.data_dir).expanduser().resolve()),
        "models_dir": str(Path(body.models_dir).expanduser().resolve()),
        "default_vault_path": vault_out,
        "ai_setup_mode": settings.AI_SETUP_MODE,
    }


# ── Notes graph (wikilinks) ───────────────────────────────────────────────────


@router.get("/api/v1/graph/notes")
async def notes_graph(
    rebuild: bool = True,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    # Vault-imported notes often never hit the create/update API, so rebuild
    # from disk before serving so [[wikilinks]] show up as edges.
    if rebuild:
        await rebuild_kb_note_links(db, kb)
    return await notes_graph_payload(db, kb.kb_id)


@router.get("/api/v1/graph/notes/{note_id}/neighbors")
async def notes_graph_neighbors(
    note_id: str,
    rebuild: bool = False,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    # Default False: this endpoint is hit from the notes editor (connected
    # panel) — a full vault re-parse per call is wasteful. The vault watcher
    # keeps note_links current; pass rebuild=true or POST /graph/notes/rebuild
    # to force a full re-resolve.
    if rebuild:
        await rebuild_kb_note_links(db, kb)
    return await note_neighborhood_payload(db, kb.kb_id, note_id)


@router.post("/api/v1/graph/notes/rebuild")
async def rebuild_notes_graph(
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    return await rebuild_kb_note_links(db, kb)


@router.post("/api/v1/notes/reingest-vault")
async def reingest_vault(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    from app.services.ai_gate import require_ai

    require_ai(kb)
    result = await db.execute(select(Note).where(Note.kb_id == kb.kb_id))
    notes = list(result.scalars().all())
    for n in notes:
        n.processed = False
        n.failed = False
        n.processing_stage = "Queued for vault re-ingest"
        n.processing_model = None
    await db.commit()

    # Read all bodies off the event loop — one sync file read per note inline
    # would stall every other request for the duration of a large vault scan.
    bodies = await asyncio.to_thread(lambda: [note_body(n, kb) for n in notes])
    for n, body in zip(notes, bodies):
        payload = NoteInput(
            content=body,
            created_at=n.created_at.isoformat() if n.created_at else None,
            title=n.title,
        )
        background_tasks.add_task(
            kb.get_ingestion_workflow().process_note, payload, n.id
        )
    return {"status": "queued", "count": len(notes)}


# ── Chat export ───────────────────────────────────────────────────────────────


@router.get("/api/v1/chat/conversations/{conversation_id}/export")
async def export_chat(
    conversation_id: str,
    format: str = Query(default="markdown"),
    db: AsyncSession = Depends(get_db),
):
    from app.services.chat_store import chat_store

    messages = await chat_store.get_messages(db, conversation_id)
    if format == "json":
        return [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
    lines = [f"## {m.role}\n\n{m.content}\n" for m in messages]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")
