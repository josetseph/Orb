"""One endpoint behind the Models page.

Choosing a model used to be spread over three pages — Setup (AI mode, models
directory, GGUF downloads), Settings (provider, cloud model names, API keys,
endpoints) and the Knowledge Bases list (per-KB pin). This module composes all
of that into a single read, so the page renders from one request and the user
sees one place to change one thing.

Writes still go to the endpoints that own each concern (``/settings``,
``/kb/{id}/llm``, ``/credentials``, ``/setup/download-models``) rather than
being duplicated here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.log import get_logger
from app.services.kb_registry import LLM_PROVIDERS, effective_llm_config, kb_registry

logger = get_logger("API")
router = APIRouter()

#: Providers the UI offers. Native cloud SDKs still exist inside LLMService and
#: are chosen automatically for recognised endpoints (see ``llm.py``), but the
#: user only ever picks "on this device" or "an OpenAI-compatible URL".
UI_PROVIDERS = ("local", "openai_compat")


class InspectPathInput(BaseModel):
    """A path the user browsed to or typed."""

    path: str = Field(min_length=1, max_length=4096)


def _media_state() -> dict:
    """Florence, Whisper and Marlin — the models Orb runs on attachments.

    Reported read-only alongside embed/rerank so the page accounts for every
    model on the machine, not just the chat one. Whisper additionally reports
    which engine will serve it, since that differs by platform.
    """
    from app.services import whisper_engine
    from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path

    rows: list[dict] = []
    labels = {
        "florence": ("Florence-2", "Image descriptions and PDF page reading"),
        "whisper": ("Whisper", "Audio and video transcription"),
        "marlin": ("Marlin", "Video understanding"),
    }
    for kind, (label, purpose) in labels.items():
        try:
            path = multimodal_model_path(kind)
        except Exception:  # pylint: disable=broad-exception-caught
            continue
        row = {
            "kind": kind,
            "label": label,
            "purpose": purpose,
            "name": path.name,
            "installed": is_hf_snapshot_ready(path),
        }
        if kind == "whisper":
            try:
                choice = whisper_engine.choose(
                    path.parent, preferred_engine=settings_whisper_engine()
                )
                row["engine"] = choice.engine
                row["engine_note"] = (
                    "GPU via MLX" if choice.engine == whisper_engine.ENGINE_MLX
                    else "CPU/torch"
                )
            except Exception:  # pylint: disable=broad-exception-caught
                row["engine"] = None
        rows.append(row)
    return {"models": rows}


def settings_whisper_engine() -> str:
    from app.core.config import settings

    return settings.WHISPER_ENGINE


def _local_state() -> dict:
    """Installed models, what can be downloaded, and the fixed support models."""
    from app.core.paths import resolve_models_dir
    from app.services.model_catalog import recommend_stack
    from app.services.model_discovery import discover_chat_models

    stack = recommend_stack()
    installed = [m.to_dict() for m in discover_chat_models()]
    installed_refs = {m["ref"] for m in installed}

    downloadable = []
    for row in stack.get("chat_options") or []:
        downloadable.append(
            {
                "id": row["id"],
                "label": row["label"],
                "size_gb": row["size_gb"],
                "params": row.get("params"),
                "family": row.get("family"),
                "fits_budget": row.get("fits_budget", True),
                "downloaded": row.get("downloaded", False),
                "recommended": row.get("recommended", False),
            }
        )

    return {
        "models_dir": str(resolve_models_dir()),
        "installed": installed,
        "installed_refs": sorted(installed_refs),
        "downloadable": downloadable,
        "hardware": stack.get("hardware"),
        "budget_note": stack.get("budget_note"),
        # Chosen automatically from RAM; shown read-only so the page explains
        # what search and media use without offering a footgun.
        "embed": stack.get("embed"),
        "reranker": stack.get("reranker"),
        "media": _media_state()["models"],
    }


def _cloud_state() -> dict:
    from app.services.credentials import credentials

    return {
        "endpoints": credentials.endpoints(),
        "providers": list(UI_PROVIDERS),
        "all_providers": list(LLM_PROVIDERS),
    }


def _global_state() -> dict:
    from app.core.config import settings
    from app.services.ai_gate import ai_is_configured
    from app.services.llm import llm_service

    provider = (settings.LLM_PROVIDER or "local").lower()
    return {
        "provider": provider,
        "mode": "local" if provider in ("local", "ollama", "lm_studio") else "cloud",
        "model": llm_service.get_chat_model() or settings.LLM_MODEL,
        "ingestion_model": llm_service.get_ingestion_model(),
        "base_url": settings.LLM_BASE_URL,
        "configured": ai_is_configured(),
    }


def _kb_state(kb_name: str | None) -> dict | None:
    """The KB the page edits — the caller's active one."""
    ctx = kb_registry.get_kb_by_name(kb_name or "default")
    if ctx is None:
        return None
    meta = kb_registry.get_metadata(ctx.kb_id) or {}
    return {
        "id": ctx.kb_id,
        "name": ctx.name,
        "override": {
            "provider": meta.get("llm_provider"),
            "model": meta.get("llm_model"),
            "ingestion_model": meta.get("llm_ingestion_model"),
            "base_url": meta.get("llm_base_url"),
        },
        "effective": effective_llm_config(meta),
    }


@router.get("/api/v1/models")
async def get_models_page(kb: str = "default"):
    """Everything the Models page renders, in one request."""
    return {
        "global": _global_state(),
        "kb": _kb_state(kb),
        "local": _local_state(),
        "cloud": _cloud_state(),
    }


@router.post("/api/v1/models/inspect")
async def inspect_local_model(body: InspectPathInput):
    """Describe a model the user pointed at, before anything is saved.

    Answers "can Orb run this, and if not, why" so the page can refuse with a
    specific reason instead of failing later inside a loader.
    """
    from app.services.model_discovery import inspect_any_chat_model, model_ref_for

    path = Path(body.path).expanduser()
    described, error = inspect_any_chat_model(path)
    if error:
        raise HTTPException(status_code=400, detail=error)

    fmt = getattr(described, "format", None)
    return {
        "ref": model_ref_for(path),
        "path": str(path),
        "name": getattr(described, "name", None) or getattr(described, "display_name", path.stem),
        "format": fmt.value if hasattr(fmt, "value") else "gguf",
        "size_gb": getattr(described, "size_gb", 0),
        "warnings": list(getattr(described, "warnings", ()) or ()),
    }
