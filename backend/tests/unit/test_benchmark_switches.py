"""Benchmark switches default to today's behaviour and take effect when set."""

from app.core.config import settings
from app.services import model_catalog


def test_embed_and_reranker_follow_ram_unless_named(monkeypatch):
    assert settings.EMBED_MODEL_ID is None and settings.RERANK_MODEL_ID is None
    assert model_catalog.pick_embed_for_budget(26).id == "qwen3-embed-4b-q4"
    assert model_catalog.pick_rerank_for_budget(26).id == "qwen3-rerank-4b-q4"
    monkeypatch.setattr(settings, "EMBED_MODEL_ID", "qwen3-embed-0.6b-q8")
    monkeypatch.setattr(settings, "RERANK_MODEL_ID", "qwen3-rerank-0.6b-q4")
    assert model_catalog.pick_embed_for_budget(26).id == "qwen3-embed-0.6b-q8"
    assert model_catalog.pick_rerank_for_budget(26).id == "qwen3-rerank-0.6b-q4"


def test_context_cap_defaults_to_six():
    assert settings.CHAT_MAX_CONTEXT_DOCS == 6
