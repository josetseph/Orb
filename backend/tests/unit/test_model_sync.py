"""`sync_embedding_infrastructure` must re-derive `EmbeddingService.is_qwen3`.

The service is built at import with the placeholder `local-embed`; startup
sets the real catalog id afterwards, and only `reconfigure()` re-reads it.
"""

import sys
import types

from app.core import config
from app.services import local_models
from app.services.embedding import embedding_service


def test_is_qwen3_flips_after_sync(monkeypatch):
    stub_qdrant = types.ModuleType("app.services.qdrant_service")
    stub_qdrant.qdrant_service = types.SimpleNamespace(ensure_vector_size=lambda dims: None)
    stub_qdrant.QdrantService = None
    stub_kb = types.ModuleType("app.services.kb_registry")
    stub_kb.kb_registry = types.SimpleNamespace(list_kbs=lambda: [])
    monkeypatch.setitem(sys.modules, "app.services.qdrant_service", stub_qdrant)
    monkeypatch.setitem(sys.modules, "app.services.kb_registry", stub_kb)
    monkeypatch.setattr(local_models, "load_manifest", lambda: {})
    monkeypatch.setattr(local_models, "reranker_gguf_path", lambda: None)

    monkeypatch.setattr(config.settings, "EMBEDDING_MODEL", "local-embed")
    embedding_service.reconfigure()
    assert embedding_service.is_qwen3 is False

    local_models.sync_embedding_infrastructure(dims=1024, embed_id="qwen3-embed-0.6b-q8")
    assert config.settings.EMBEDDING_MODEL == "qwen3-embed-0.6b-q8"
    assert embedding_service.is_qwen3 is True
