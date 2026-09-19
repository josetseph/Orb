"""In-process GGUF embedding service (llama-cpp-python)."""

from __future__ import annotations

import os

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("EmbeddingService")


class EmbeddingService:
    """Local GGUF embeddings only — no HTTP model sidecars."""

    def __init__(self):
        configured = settings.EMBEDDING_PROVIDER.lower().strip()
        if configured in ("ollama", "lm_studio"):
            logger.warning(
                "EMBEDDING_PROVIDER=%s is deprecated; using in-process local",
                configured,
            )
            configured = "local"
        if configured not in ("local", "auto", ""):
            raise ValueError(
                f"Unsupported EMBEDDING_PROVIDER: '{settings.EMBEDDING_PROVIDER}'. "
                "Orb uses in-process GGUF embeddings only (set 'local' or 'auto')."
            )
        self.embedding_provider = "local"
        self.embedding_model = settings.EMBEDDING_MODEL
        self.query_instruction = (
            "Instruct: Given a question, retrieve relevant context.\nQuery: "
        )
        self._configure_embeddings()
        model_name_lower = os.path.basename(self.embedding_model or "").lower()
        self.is_qwen3 = "qwen3" in model_name_lower

    def _configure_embeddings(self) -> None:
        from app.services.local_models import LocalLlamaEmbeddings, local_llama_runtime

        self.embeddings = LocalLlamaEmbeddings(local_llama_runtime)
        logger.info(
            "[Embedding] Provider: local (llama-cpp-python in-process) | Model: %s",
            self.embedding_model,
        )

    def reconfigure(self) -> None:
        """Re-bind after local models are loaded or settings change."""
        self.embedding_provider = "local"
        self.embedding_model = settings.EMBEDDING_MODEL
        self._configure_embeddings()
        model_name_lower = os.path.basename(self.embedding_model or "").lower()
        self.is_qwen3 = "qwen3" in model_name_lower

    def embed_query(
        self, text: str, custom_instruction: str | None = None
    ) -> list[float]:
        """Embed a search query with instruction prefix for Qwen3 models."""
        if self.is_qwen3:
            instruction = custom_instruction or self.query_instruction
            text = instruction + text
        return self.embeddings.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents/passages without instruction prefix."""
        return self.embeddings.embed_documents(texts)


embedding_service = EmbeddingService()
