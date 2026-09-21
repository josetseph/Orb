"""Benchmark-only routes: what configuration is live, and answer from frozen evidence."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_kb
from app.core.config import settings
from app.services.ai_gate import require_ai
from app.services.kb_registry import KBContext
from app.services.local_models import load_manifest

router = APIRouter()

# The knobs an experiment is likely to vary; recorded with every result file.
KNOBS = (
    "BENCHMARK_MODE",
    "MAX_LOOP_ITERATIONS",
    "CHAT_MAX_CONTEXT_DOCS",
    "EMBED_MODEL_ID",
    "RERANK_MODEL_ID",
    "LLAMA_N_CTX",
    "MODEL_IDLE_SECONDS",
    "RERANKER_TOP_K",
    "RERANKER_SCORE_THRESHOLD",
    "VECTOR_PRE_RERANK_THRESHOLD",
    "GRAPH_EXPAND_TOP_NEIGHBORS",
    "GRAPH_EXPAND_SCORE_THRESHOLD",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "MODEL_RERANKER_LOCAL",
)


@router.get("/api/v1/benchmark/config")
async def benchmark_config(kb: KBContext = Depends(get_kb)):
    """The models and retrieval knobs this server is running with."""
    llm = kb.llm
    return {
        "chat_model": llm.get_chat_model(),
        "ingestion_model": llm.get_ingestion_model(),
        # embed / rerank ids decide whether a snapshot's vectors are still valid
        "selection": load_manifest().get("selection") or {},
        "knobs": {k: getattr(settings, k, None) for k in KNOBS},
    }


@router.get("/api/v1/benchmark/idle")
async def benchmark_idle(kb: KBContext = Depends(get_kb)):
    """True once ingestion and any requested graph rebuild have drained: safe to evaluate or snapshot."""
    status = kb.get_ingestion_workflow().get_maintenance_status()
    busy = (
        status["ingestion"]["active"]
        or status["community_detection"]["running"]
        or status["temporal_digests"]["running"]
    )
    return {"idle": not busy, "status": status}


class SynthesizeInput(BaseModel):
    question: str = Field(min_length=1)
    # Context docs exactly as a chat response returned them (``text`` is what the model reads).
    docs: list[dict]


@router.post("/api/v1/benchmark/synthesize")
async def synthesize(body: SynthesizeInput, kb: KBContext = Depends(get_kb)):
    """One reasoning step over fixed evidence: isolates the answering model from retrieval."""
    require_ai(kb)
    started = time.perf_counter()
    result = await kb.llm.iterative_step(
        original_question=body.question,
        accumulated_steps=[],
        search_query=body.question,
        docs=body.docs,
    )
    return {
        "answer": result.get("final_answer") or result.get("full_answer") or "",
        "can_answer": bool(result.get("can_answer")),
        "seconds": round(time.perf_counter() - started, 2),
    }
