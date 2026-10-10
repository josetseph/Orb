"""INDEX_RAW_PASSAGES: notes split into passages, stored per note (replaced on re-ingest), found by vector."""
from qdrant_client import QdrantClient

from app.core.config import settings
from app.services.qdrant_service import QdrantService
from app.workflows.ingestion import split_passages


def test_split_packs_paragraphs_up_to_the_limit():
    assert split_passages("a\n\nbb\n\n\n\ncc", 6) == ["a\n\nbb", "cc"]
    assert split_passages("x" * 10 + "\n\ny", 5) == ["x" * 10, "y"]  # an oversize paragraph stays whole
    assert split_passages("  \n\n ", 100) == []


def test_passages_replace_per_note_and_search(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    svc = QdrantService.__new__(QdrantService)
    svc._col_contexts, svc._col_passages = "ctx", "ctx_passages"
    svc._client, svc._retry_at = QdrantClient(":memory:"), 0.0
    svc._prepare_vector = lambda v: v

    assert svc.search_passages([1.0, 0.0], 10, 0.0) == []  # nothing stored yet
    svc.replace_note_passages("n1", "Old", ["old"], [[0.0, 1.0]])
    svc.replace_note_passages("n1", "Ed Wood", ["p0", "p1"], [[1.0, 0.0], [0.6, 0.8]])
    svc.replace_note_passages("n2", "Other", ["q"], [[0.0, 1.0]])

    hits = svc.search_passages([1.0, 0.0], 10, 0.5)
    assert [(h["note_id"], h["index"], h["content"]) for h in hits] == [("n1", 0, "p0"), ("n1", 1, "p1")]
    assert hits[0]["title"] == "Ed Wood"
