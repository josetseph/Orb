"""GGUF embedding service: in-process (llama-cpp-python), or llama-server for EmbeddingGemma 2."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("EmbeddingService")

# EmbeddingGemma's task prompts (model card); notes carry no separate title here, so "none".
_GEMMA_QUERY = "task: search result | query: "
_GEMMA_DOCUMENT = "title: none | text: "


class _ServerEmbeddings:
    """embed_query / embed_documents over the embedding llama-server."""

    def _embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        from app.services.extraction_server import embedding_server
        from app.services.local_models import gguf_paths_if_present

        embed = (gguf_paths_if_present() or {}).get("embed")
        if not embed:
            raise RuntimeError("Local embed GGUF is not downloaded. Open Models to download one.")
        gguf = Path(embed)
        embedding_server.begin()
        try:
            url = embedding_server.url_for(gguf)
            client = OpenAI(base_url=f"{url}/v1", api_key="local", timeout=600.0, max_retries=0)
            out = client.embeddings.create(model=gguf.name, input=texts)
        finally:
            embedding_server.end()
        if len(out.data) != len(texts):
            # Mis-paired vectors would corrupt the index silently: fail instead.
            raise RuntimeError(f"llama-server returned {len(out.data)} embeddings for {len(texts)} texts")
        return [list(d.embedding) for d in sorted(out.data, key=lambda d: d.index)]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts) if texts else []


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

    def _configure_embeddings(self) -> None:
        from app.services.local_models import LocalLlamaEmbeddings, local_llama_runtime
        from app.services.model_catalog import served_embedder

        served = served_embedder(self.embedding_model)
        self.embeddings = _ServerEmbeddings() if served else LocalLlamaEmbeddings(local_llama_runtime)
        model_name_lower = os.path.basename(self.embedding_model or "").lower()
        self.is_qwen3 = "qwen3" in model_name_lower
        self.is_gemma = "embeddinggemma" in model_name_lower
        logger.info(
            "[Embedding] Provider: local (%s) | Model: %s",
            "llama-server" if served else "llama-cpp-python in-process",
            self.embedding_model,
        )

    def reconfigure(self) -> None:
        """Re-bind after local models are loaded or settings change."""
        self.embedding_provider = "local"
        self.embedding_model = settings.EMBEDDING_MODEL
        self._configure_embeddings()

    def embed_query(
        self, text: str, custom_instruction: str | None = None
    ) -> list[float]:
        """Embed a search query with instruction prefix for Qwen3 models."""
        if self.is_qwen3:
            instruction = custom_instruction or self.query_instruction
            text = instruction + text
        elif self.is_gemma:
            text = _GEMMA_QUERY + text
        return self.embeddings.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents/passages; no prefix except EmbeddingGemma's document prompt."""
        if self.is_gemma:
            texts = [_GEMMA_DOCUMENT + t for t in texts]
        return self.embeddings.embed_documents(texts)


embedding_service = EmbeddingService()
