"""Local model setup routes: catalogue, download, select, load."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ChatModelInput(BaseModel):
    chat_id: str | None = None


@router.get("/api/v1/setup/model-catalog")
async def model_catalog(chat_id: str | None = None):
    """Hardware profile + filtered chat options + auto embed/reranker picks."""
    from app.services.model_catalog import recommend_stack

    return recommend_stack(chat_id)


@router.post("/api/v1/setup/download-models")
async def download_models(body: ChatModelInput | None = None):
    """Download the chat/embed/rerank GGUFs for the selected chat model."""
    from app.services.local_models import ensure_chat_and_embed_models

    chat_id = body.chat_id if body else None
    progress: list[dict] = []

    def on_progress(label: str, pct: int) -> None:
        progress.append({"model": label, "percent": pct})

    try:
        paths = await asyncio.to_thread(ensure_chat_and_embed_models, on_progress, chat_id=chat_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise HTTPException(status_code=500, detail=f"GGUF download failed: {exc}") from exc
    return {
        "status": "ok",
        "chat": str(paths.get("chat", "")),
        "embed": str(paths.get("embed", "")),
        "reranker": str(paths.get("reranker", "")),
        "progress": progress[-40:],
    }


@router.post("/api/v1/setup/select-chat-model")
async def select_chat_model(body: ChatModelInput | None = None):
    """Persist chat selection (+ auto embed/rerank ids) without downloading.

    Also resizes Qdrant collections to match the embed model's dimensions.
    """
    from app.services.local_models import resolve_selected_hf_paths, save_selection

    chat_id = body.chat_id if body else None
    if not chat_id:
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


@router.post("/api/v1/setup/start-local-llm")
async def start_local_llm(body: ChatModelInput | None = None):
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
        return {"started": False, "loaded": False, "reason": str(exc), "accel": detect_llama_backend()}
    llm_service.provider = "local"
    llm_service.init_clients()
    embedding_service.reconfigure()
    return result
