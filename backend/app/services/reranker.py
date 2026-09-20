"""In-process Qwen3 GGUF reranker (no HTTP / Ollama / LM Studio)."""

from __future__ import annotations

import asyncio
from typing import Optional

class RerankerService:  # pylint: disable=too-few-public-methods
    """Score query/document pairs via the selected on-disk GGUF in-process."""

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: Optional[int] = None,
    ) -> list[dict]:
        if not documents:
            return []
        from app.services.local_models import local_gguf_reranker

        return await asyncio.to_thread(local_gguf_reranker.rerank, query, documents, top_n)


reranker_service = RerankerService()
