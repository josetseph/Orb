"""Note passages searched beside the graph, and the evidence order (retrieval study rounds 6-9)."""

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app.core.config import settings
from app.services.llm import edges_order
from app.services.qdrant_service import QdrantService
from app.workflows import ingestion
from app.workflows.ingestion import IngestionWorkflow


def _qdrant(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    svc = QdrantService.__new__(QdrantService)
    svc._col_contexts, svc._col_passages = "ctx", "ctx_passages"
    svc._client, svc._retry_at = QdrantClient(":memory:"), 0.0
    svc._client.create_collection("ctx_passages", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    return svc


def test_passages_replace_per_note_search_and_delete(monkeypatch):
    svc = _qdrant(monkeypatch)
    svc.replace_note_passages("n1", "Old", ["old"], [[0.0, 1.0]])
    svc.replace_note_passages("n1", "Ed Wood", ["p0", "p1"], [[1.0, 0.0], [0.6, 0.8]])
    svc.replace_note_passages("n2", "Other", ["q"], [[0.0, 1.0]])

    hits = svc.search_passages([1.0, 0.0], 10, 0.5)
    assert [(h["note_id"], h["index"], h["content"]) for h in hits] == [("n1", 0, "p0"), ("n1", 1, "p1")]
    assert hits[0]["title"] == "Ed Wood"

    monkeypatch.setattr(svc, "is_available", lambda: True)
    svc.delete_note_passages("n1")
    assert [h["note_id"] for h in svc.search_passages([0.0, 1.0], 10, 0.0)] == ["n2"]


def test_store_passages_titles_each_passage_and_writes_both_indexes(monkeypatch):
    stored = {}

    class Store:
        def __init__(self, key):
            self.key = key

        def replace_note_passages(self, note_id, title, texts, *vectors):
            stored[self.key] = (note_id, title, texts, vectors)

    monkeypatch.setattr(ingestion.embedding_service, "embed_documents", lambda texts: [[1.0, 0.0]] * len(texts))
    wf = IngestionWorkflow.__new__(IngestionWorkflow)
    wf._qdrant, wf._meili = Store("qdrant"), Store("meili")
    wf._store_passages("n1", "Ed Wood", "First paragraph.\n\nSecond paragraph.")

    note_id, title, texts, vectors = stored["qdrant"]
    assert note_id == "n1" and title == "Ed Wood"
    assert texts and all(t.startswith("Ed Wood\n\n") for t in texts)
    assert len(vectors[0]) == len(texts)
    assert stored["meili"][2] == texts


def test_backfill_keeps_going_past_a_failure_and_reports_it(monkeypatch):
    done = []

    def store(note_id, title, content):
        if note_id == "bad":
            raise RuntimeError("qdrant down")
        done.append(note_id)

    wf = IngestionWorkflow.__new__(IngestionWorkflow)
    wf._passage_backfill = None
    monkeypatch.setattr(wf, "_store_passages", store)
    assert wf.backfill_passages([("a", "A", "x"), ("bad", "B", "y"), ("c", "C", "z")]) is False
    assert done == ["a", "c"] and wf._passage_backfill is None
    assert wf.backfill_passages([("a", "A", "x")]) is True


def test_edges_order_puts_the_best_at_both_ends():
    docs = [{"id": i, "rerank_score": s} for i, s in enumerate([0.1, 0.9, 0.5, 0.7, 0.3])]
    assert [d["rerank_score"] for d in edges_order(docs)] == [0.9, 0.5, 0.1, 0.3, 0.7]
    unscored = [{"id": i} for i in range(4)]
    assert [d["id"] for d in edges_order(unscored)] == [0, 2, 3, 1]
