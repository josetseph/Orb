"""Every lever a sweep may vary, declared once.

``stage`` is the earliest pipeline stage a lever affects. It decides what has to be rebuilt when
the lever changes: an ``extract`` or ``index`` lever needs its own index (with the model-call cache
an ``index`` lever reuses every extraction, so that rebuild takes minutes); a ``retrieve``, ``loop``
or ``answer`` lever runs against an index that already exists.

``how`` says how experiment.py applies it: ``set`` is a server setting (``--set NAME=value``),
``flag`` is one of its own options. ``default`` is the app's behaviour. ``values`` are suggestions.

Not levers yet: the prompts. They are the largest lever of all, but they live inline in
``llm.py`` and ``ingestion_agent.py``; varying them from a spec needs them in named files first.
"""

from __future__ import annotations

from dataclasses import dataclass

STAGES = ("extract", "index", "retrieve", "loop", "answer")
LOCAL_CHAT = ("gemma4-e2b-q4", "gemma4-e4b-q4", "gemma4-12b-q4", "qwen35-0.8b-q4", "qwen35-2b-q4", "qwen35-4b-q4", "qwen35-9b-q4")


@dataclass(frozen=True)
class Lever:
    name: str
    stage: str
    how: str
    default: object
    values: tuple
    note: str


LEVERS = (
    Lever("ingestion_model", "extract", "flag", None, LOCAL_CHAT, "model that reads each note; the costliest stage"),
    Lever("EXTRACTION_ATTEMPTS", "extract", "set", 1, (1, 2, 3), "times an unusable extraction reply may be asked again"),
    Lever("EXTRACTION_CHUNK_TOKENS", "extract", "set", None, (1000, 2000, 4000), "note size above which extraction splits; unset = learned per model"),
    Lever("LLAMA_N_CTX", "extract", "set", 16384, (8192, 16384, 32768), "local context window: memory against how much fits in one call"),
    Lever("EMBED_MODEL_ID", "index", "set", None, ("qwen3-embed-0.6b-q8", "qwen3-embed-4b-q4"), "embedding model; changes vector dimensions"),
    Lever("communities", "index", "flag", False, (False, True), "build community summaries (one model call per cluster)"),
    Lever("RERANK_MODEL_ID", "retrieve", "set", None, ("qwen3-rerank-0.6b-q4", "qwen3-rerank-4b-q4"), "reranker loaded on every search"),
    Lever("RERANKER_TOP_K", "retrieve", "set", 10, (5, 10, 20), "candidates kept after reranking"),
    Lever("RERANKER_SCORE_THRESHOLD", "retrieve", "set", 0.05, (0.0, 0.05, 0.2), "rerank score below which a search result is dropped"),
    Lever("VECTOR_PRE_RERANK_THRESHOLD", "retrieve", "set", 0.45, (0.3, 0.45, 0.6), "vector similarity needed to become a candidate"),
    Lever("GRAPH_EXPAND_TOP_NEIGHBORS", "retrieve", "set", 10, (0, 5, 10, 20), "graph neighbours pulled in per result; 0 turns expansion off"),
    Lever("GRAPH_EXPAND_SCORE_THRESHOLD", "retrieve", "set", 0.0, (0.0, 0.1, 0.3), "score a result needs before its neighbours are expanded"),
    Lever("CHAT_MAX_CONTEXT_DOCS", "retrieve", "set", 6, (4, 6, 10), "evidence documents returned with the answer"),
    Lever("MAX_LOOP_ITERATIONS", "loop", "set", 3, (3, 5, 8), "model steps per question; searches = steps - 1"),
    Lever("chat_model", "loop", "flag", None, LOCAL_CHAT, "model that plans the searches and writes the answer"),
    Lever("provider", "loop", "flag", "local", ("local", "gemini", "openai", "anthropic", "openai_compat"), "where chat_model and ingestion_model run"),
)
BY_NAME = {lever.name: lever for lever in LEVERS}
