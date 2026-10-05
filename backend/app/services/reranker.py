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
        from app.services.call_cache import cached
        from app.services.local_models import local_gguf_reranker, reranker_gguf_path

        def score() -> list[dict]:
            # The file name, not its path: each lane reaches the same file through its own folder.
            path = reranker_gguf_path()
            return cached("rerank", [path.name if path else None, query, documents, top_n],
                          lambda: local_gguf_reranker.rerank(query, documents, top_n))

        return await asyncio.to_thread(score)


reranker_service = RerankerService()
