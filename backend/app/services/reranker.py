"""In-process Qwen3 GGUF reranker (no HTTP / Ollama / LM Studio)."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.core.log import get_logger

logger = get_logger("RerankerService")


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

        try:
            from app.services.local_models import (
                local_gguf_reranker,
                reranker_gguf_path,
            )

            path = reranker_gguf_path()
            if not path:
                logger.warning(
                    "[Reranker] No GGUF selected — download/select a reranker on the Models page"
                )
                return []

            results = await asyncio.to_thread(
                local_gguf_reranker.rerank, query, documents, top_n
            )
            return results or []
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(f"[Reranker] In-process GGUF failed: {exc}")
            return []


reranker_service = RerankerService()
