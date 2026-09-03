"""Finance API + notes graph + desktop setup routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
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
from app.services.firefly_service import firefly_service
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
    from app.services.ai_gate import ai_is_configured
    from app.services.local_models import gguf_paths_if_present
    from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path
    gguf = gguf_paths_if_present()
    local_models_ready = gguf is not None
    multimodal_ready = all(
        is_hf_snapshot_ready(multimodal_model_path(k))
        for k in ("florence", "whisper", "marlin")
    )
    mode = (settings.AI_SETUP_MODE or "none").lower().strip()
    vault = resolve_default_vault_path()
    default_kb = kb_registry.get_kb_by_name("default")
    return {
        "data_dir": str(resolve_data_dir()),
        "models_dir": str(resolve_models_dir()),
        "paths_json": str(paths_json_location()),
        "default_vault_path": str(vault) if vault else "",
        "active_vault_path": (default_kb.vault_path if default_kb else "") or "",
        "ai_setup_mode": settings.AI_SETUP_MODE,
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
    # When True, skip GGUF ensure (used for background Florence/Whisper/Marlin).
    multimodal_only: bool = False


@router.get("/api/v1/setup/model-catalog")
async def model_catalog(chat_id: str | None = None):
    """Hardware profile + filtered chat options + auto embed/reranker picks."""
    from app.services.model_catalog import recommend_stack

    return recommend_stack(chat_id)


@router.post("/api/v1/setup/download-models")
async def download_models(body: DownloadModelsInput | None = None):
    """Download chat/embed/rerank GGUFs (+ Florence/Whisper/Marlin weights).

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
                "to prepare the in-process Florence/Whisper/Marlin runtime"
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
    """Prepare Florence/Whisper/Marlin for in-process load (no HTTP sidecars)."""
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
            "florence": is_hf_snapshot_ready(multimodal_model_path("florence")),
            "whisper": is_hf_snapshot_ready(multimodal_model_path("whisper")),
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


# ── Finance ───────────────────────────────────────────────────────────────────


class CreateWorkspaceInput(BaseModel):
    currency: str = Field(default="USD", min_length=3, max_length=3)


class CreateAccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    account_type: str = Field(default="asset")
    opening_balance: float = 0.0
    currency: str | None = None


class CreateTransactionInput(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    amount: float = Field(gt=0)
    account_id: str = Field(min_length=1)
    type: str = Field(default="withdrawal")
    date: str | None = None
    counterparty_name: str | None = None
    transfer_account_id: str | None = None
    category: str | None = None
    budget_id: str | None = None
    currency: str | None = None


class CreateBudgetInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = None


class CreateCategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    notes: str | None = None


class CreateBillInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    amount: float = Field(gt=0)
    repeat_freq: str = "monthly"
    date: str | None = None
    currency: str | None = None


class CreatePiggyInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    account_id: str = Field(min_length=1)
    target_amount: float = Field(gt=0)
    current_amount: float = 0.0
    start_date: str | None = None
    target_date: str | None = None


class CreateTagInput(BaseModel):
    tag: str = Field(min_length=1, max_length=255)
    description: str | None = None


class CreateRecurrenceInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    amount: float = Field(gt=0)
    type: str = "withdrawal"
    source_id: str = Field(min_length=1)
    destination_id: str = Field(min_length=1)
    description: str | None = None
    first_date: str | None = None
    repeat_freq: str = "monthly"


def _finance_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/api/v1/finance/workspace")
async def get_finance_workspace(kb: KBContext = Depends(get_kb)):
    return await firefly_service.get_workspace(kb)


@router.post("/api/v1/finance/workspace")
async def create_finance_workspace(
    body: CreateWorkspaceInput,
    kb: KBContext = Depends(get_kb),
):
    try:
        return await firefly_service.set_primary_currency(kb, body.currency)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.post("/api/v1/finance/reset-administration")
async def reset_finance_administration(kb: KBContext = Depends(get_kb)):
    """Destroy this KB's Firefly administration (ledger + UserGroup)."""
    try:
        result = await firefly_service.destroy_kb_administration(kb)
        return {
            "status": "reset",
            "kb_id": kb.kb_id,
            "result": result,
            "message": (
                "Finance data for this knowledge base was cleared. "
                "Opening Finance again will create a fresh administration."
            ),
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/accounts")
async def list_accounts(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_accounts(kb)


@router.post("/api/v1/finance/accounts")
async def create_account(body: CreateAccountInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_account(
            kb,
            name=body.name,
            account_type=body.account_type,
            opening_balance=body.opening_balance,
            currency_code=body.currency,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/transactions")
async def list_transactions(
    kb: KBContext = Depends(get_kb),
    account_id: str | None = None,
):
    return await firefly_service.list_recent_transactions(kb, account_id=account_id)


@router.post("/api/v1/finance/transactions")
async def create_transaction(
    body: CreateTransactionInput,
    kb: KBContext = Depends(get_kb),
):
    try:
        return await firefly_service.create_transaction(
            kb,
            description=body.description,
            amount=body.amount,
            tx_type=body.type,
            account_id=body.account_id,
            date_value=body.date,
            counterparty_name=body.counterparty_name,
            transfer_account_id=body.transfer_account_id,
            category=body.category,
            budget_id=body.budget_id,
            currency_code=body.currency,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/transactions/{transaction_id}")
async def delete_transaction(transaction_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_transaction(kb, transaction_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/budgets")
async def list_budgets(
    kb: KBContext = Depends(get_kb),
    days: int = Query(default=30, ge=1, le=365),
):
    return await firefly_service.list_budgets(kb, days=days)


@router.post("/api/v1/finance/budgets")
async def create_budget(body: CreateBudgetInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_budget(
            kb,
            name=body.name,
            amount=body.amount,
            currency_code=body.currency,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/categories")
async def list_categories(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_categories(kb)


@router.post("/api/v1/finance/categories")
async def create_category(body: CreateCategoryInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_category(kb, name=body.name, notes=body.notes)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/categories/{category_id}")
async def delete_category(category_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_category(kb, category_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/bills")
async def list_bills(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_bills(kb)


@router.post("/api/v1/finance/bills")
async def create_bill(body: CreateBillInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_bill(
            kb,
            name=body.name,
            amount=body.amount,
            repeat_freq=body.repeat_freq,
            date_value=body.date,
            currency_code=body.currency,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/bills/{bill_id}")
async def delete_bill(bill_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_bill(kb, bill_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/piggy-banks")
async def list_piggy_banks(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_piggy_banks(kb)


@router.post("/api/v1/finance/piggy-banks")
async def create_piggy_bank(body: CreatePiggyInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_piggy_bank(
            kb,
            name=body.name,
            account_id=body.account_id,
            target_amount=body.target_amount,
            current_amount=body.current_amount,
            start_date=body.start_date,
            target_date=body.target_date,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/piggy-banks/{piggy_id}")
async def delete_piggy_bank(piggy_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_piggy_bank(kb, piggy_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/tags")
async def list_tags(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_tags(kb)


@router.post("/api/v1/finance/tags")
async def create_tag(body: CreateTagInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_tag(kb, tag=body.tag, description=body.description)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/tags/{tag_id}")
async def delete_tag(tag_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_tag(kb, tag_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/recurrences")
async def list_recurrences(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_recurrences(kb)


@router.post("/api/v1/finance/recurrences")
async def create_recurrence(body: CreateRecurrenceInput, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_recurrence(
            kb,
            title=body.title,
            amount=body.amount,
            tx_type=body.type,
            source_id=body.source_id,
            destination_id=body.destination_id,
            description=body.description,
            first_date=body.first_date,
            repeat_freq=body.repeat_freq,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/recurrences/{recurrence_id}")
async def delete_recurrence(recurrence_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_recurrence(kb, recurrence_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/rule-groups")
async def list_rule_groups(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_rule_groups(kb)


@router.post("/api/v1/finance/rule-groups")
async def create_rule_group(body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_rule_group(
            kb,
            title=str(body.get("title") or ""),
            description=body.get("description"),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/rule-groups/{rule_group_id}")
async def delete_rule_group(rule_group_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_rule_group(kb, rule_group_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/rules")
async def list_rules(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_rules(kb)


@router.post("/api/v1/finance/rules")
async def create_rule(body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_rule(
            kb,
            title=str(body.get("title") or ""),
            rule_group_id=str(body.get("rule_group_id") or ""),
            trigger_type=str(body.get("trigger_type") or "description_contains"),
            trigger_value=str(body.get("trigger_value") or ""),
            action_type=str(body.get("action_type") or "add_tag"),
            action_value=str(body.get("action_value") or ""),
            trigger=str(body.get("trigger") or "store-journal"),
            description=body.get("description"),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/rules/{rule_id}")
async def delete_rule(rule_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_rule(kb, rule_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/webhooks")
async def list_webhooks(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_webhooks(kb)


@router.post("/api/v1/finance/webhooks")
async def create_webhook(body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_webhook(
            kb,
            title=str(body.get("title") or ""),
            url=str(body.get("url") or ""),
            trigger=str(body.get("trigger") or "STORE_TRANSACTION"),
            response=str(body.get("response") or "TRANSACTIONS"),
            delivery=str(body.get("delivery") or "JSON"),
            active=bool(body.get("active", True)),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_webhook(kb, webhook_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/object-groups")
async def list_object_groups(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_object_groups(kb)


@router.post("/api/v1/finance/object-groups")
async def create_object_group(body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_object_group(kb, title=str(body.get("title") or ""))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.put("/api/v1/finance/object-groups/{group_id}")
async def update_object_group(group_id: str, body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.update_object_group(
            kb, group_id, title=str(body.get("title") or "")
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/object-groups/{group_id}")
async def delete_object_group(group_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_object_group(kb, group_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/exchange-rates")
async def list_exchange_rates(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_exchange_rates(kb)


@router.post("/api/v1/finance/exchange-rates")
async def create_exchange_rate(body: dict, kb: KBContext = Depends(get_kb)):
    try:
        return await firefly_service.create_exchange_rate(
            kb,
            date_value=str(body.get("date") or ""),
            from_code=str(body.get("from") or ""),
            to_code=str(body.get("to") or ""),
            rate=float(body.get("rate") or 0),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/exchange-rates/{rate_id}")
async def delete_exchange_rate(rate_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_exchange_rate(kb, rate_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/attachments")
async def list_attachments(kb: KBContext = Depends(get_kb)):
    return await firefly_service.list_attachments(kb)


@router.post("/api/v1/finance/attachments")
async def create_attachment(
    kb: KBContext = Depends(get_kb),
    filename: str = Form(...),
    attachable_type: str = Form(...),
    attachable_id: str = Form(...),
    title: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
):
    try:
        file_bytes = await file.read() if file is not None else None
        return await firefly_service.create_attachment(
            kb,
            filename=filename or (file.filename if file else "file"),
            attachable_type=attachable_type,
            attachable_id=attachable_id,
            title=title,
            notes=notes,
            file_bytes=file_bytes,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: str, kb: KBContext = Depends(get_kb)):
    try:
        content, filename = await firefly_service.download_attachment(kb, attachment_id)
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.delete("/api/v1/finance/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str, kb: KBContext = Depends(get_kb)):
    try:
        await firefly_service.delete_attachment(kb, attachment_id)
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/search")
async def finance_search(
    kb: KBContext = Depends(get_kb),
    query: str = Query(...),
    kind: str = Query(default="transactions"),
):
    try:
        return await firefly_service.search(kb, query=query, kind=kind)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.get("/api/v1/finance/summary")
async def get_finance_summary(
    kb: KBContext = Depends(get_kb),
    days: int = Query(default=30, ge=1, le=365),
):
    return await firefly_service.summary(kb, days=days)


@router.get("/api/v1/finance/report")
async def get_finance_report(
    kb: KBContext = Depends(get_kb),
    start: str | None = None,
    end: str | None = None,
):
    try:
        return await firefly_service.report(kb, start=start, end=end)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _finance_error(exc) from exc


@router.post("/api/v1/finance/open")
async def open_finance_workspace(kb: KBContext = Depends(get_kb)):
    return await firefly_service.prepare_open(kb)


@router.get("/vault-files/{kb_id}/{file_path:path}")
async def serve_vault_file(kb_id: str, file_path: str):
    from app.services.vault_ops import safe_vault_join

    ctx = kb_registry.get_kb(kb_id) or kb_registry.get_kb_by_name(kb_id)
    if not ctx or not ctx.vault_path:
        raise HTTPException(status_code=404, detail="KB not found")
    vault = Path(ctx.vault_path).resolve()
    try:
        full = safe_vault_join(vault, file_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if not full.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full)
