"""EmbeddingGemma 2 as the embedder: catalog default, cutoff, task prompts, and rebuilding old vectors."""

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.core.config import settings
from app.services import model_catalog as mc
from app.services import qdrant_service as qs


def test_default_embedder_cutoff_and_server_route():
    assert mc.pick_embed_for_budget(8).id == mc.pick_embed_for_budget(128).id == "embeddinggemma-2-q8"
    assert mc.vector_threshold("embeddinggemma-2-q8") == 0.69
    assert mc.vector_threshold("qwen3-embed-4b-q4") == settings.VECTOR_PRE_RERANK_THRESHOLD
    assert mc.served_embedder("embeddinggemma-2-q8")
    assert mc.served_embedder("/models/gguf/embeddinggemma-2-Q8_0.gguf")
    assert not mc.served_embedder("qwen3-embed-4b-q4")


def test_gemma_task_prompts(monkeypatch):
    from app.services import embedding as emb

    seen = []

    class Fake:
        def embed_query(self, text):
            seen.append(text)
            return [1.0]

        def embed_documents(self, texts):
            seen.extend(texts)
            return [[1.0]] * len(texts)

    svc = emb.EmbeddingService.__new__(emb.EmbeddingService)
    svc.embeddings, svc.is_qwen3, svc.is_gemma = Fake(), False, True
    svc.embed_query("who?")
    svc.embed_documents(["a note"])
    assert seen == ["task: search result | query: who?", "title: none | text: a note"]


def _service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 2)
    monkeypatch.setattr(qs, "_reembed_dump", lambda name: tmp_path / f"{name}.jsonl")
    svc = qs.QdrantService.__new__(qs.QdrantService)
    svc._col_cores, svc._col_rels, svc._col_contexts = "cores", "rels", "ctx"
    svc._col_passages = "ctx_passages"
    svc._client, svc._retry_at = QdrantClient(":memory:"), 0.0
    for name in ("cores", "rels", "ctx"):
        svc._client.create_collection(name, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    svc._client.upsert("cores", [
        PointStruct(id=1, vector=[1, 0, 0, 0], payload={"name": "Ada", "type": "person", "description": "Ada wrote code."}),
        PointStruct(id=2, vector=[1, 0, 0, 0], payload={"name": "lecture.pdf", "type": "document", "description": "Full text"}),
    ])
    svc._client.upsert("rels", [PointStruct(id=3, vector=[0, 1, 0, 0], payload={"natural_language": "Ada knew Babbage."})])
    svc._client.upsert("ctx", [PointStruct(id=4, vector=[0, 0, 1, 0], payload={"content": "Ada in 1843."})])
    return svc


def test_old_vectors_are_rebuilt_from_stored_text(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return [[float(len(t)), 1.0] for t in texts]

    steps = []
    assert svc.reembed(embed, lambda done, total: steps.append((done, total))) == 4
    assert sorted(embedded) == sorted(["Ada wrote code.", "lecture.pdf", "Ada knew Babbage.", "Ada in 1843."])
    assert steps[-1] == (4, 4)
    for name in ("cores", "rels", "ctx"):
        assert svc._client.get_collection(name).config.params.vectors.size == 2
    point = svc._client.retrieve("cores", [1], with_vectors=True)[0]
    assert point.payload["name"] == "Ada"  # payloads kept as they were
    assert not list(tmp_path.glob("*.jsonl"))
    assert svc.reembed(embed) == 0  # nothing left at the old size


def test_a_rebuild_interrupted_after_the_drop_resumes_from_its_file(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    (tmp_path / "rels.jsonl").write_text('{"id": 3, "payload": {"natural_language": "x"}, "vector": [0.5, 0.5]}\n')
    svc._client.delete_collection("rels")  # the crash happened after the old collection was dropped
    svc.reembed(lambda texts: [[1.0, 0.0] for _ in texts])
    restored = svc._client.retrieve("rels", [3], with_vectors=True)[0]
    # The vector from the file, not a fresh one ([1, 0]); Qdrant normalizes it for cosine.
    assert restored.payload == {"natural_language": "x"}
    assert abs(restored.vector[0] - restored.vector[1]) < 1e-6


def test_an_empty_collection_of_the_old_size_is_recreated(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc._client.create_collection("ctx_passages", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    assert svc.reembed(lambda texts: [[1.0, 0.0] for _ in texts]) == 4
    assert svc._client.get_collection("ctx_passages").config.params.vectors.size == 2
