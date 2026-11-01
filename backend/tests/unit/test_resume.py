"""Kill an ingestion anywhere, resume it: the stores end up exactly as after one clean run,
and no model call is paid for twice (Docs/plan-resumable-work.md).

Runs the real pipeline over a real Kuzu graph and an in-memory Qdrant; the keyword index
is a dict and the model a script, so it is fast and free.
"""

import asyncio
import hashlib
import json
import uuid

import pytest
from qdrant_client import QdrantClient

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.models.note import Note
from app.schemas.extraction import NoteInput
from app.services import ingestion_checkpoint
from app.services.embedding import embedding_service
from app.services.graph import GraphService
from app.services.llm import LLMService
from app.services.qdrant_service import QdrantService
from app.workflows.ingestion import IngestionWorkflow

DIMS = 8
NOTE = (
    "Alice and Bob build the Orb project together.\n\n"
    "Alice leads the design of Orb. Bob works on its search index.\n\n"
    "Carol reviews their work every Friday."
)
PEOPLE = ["Alice", "Bob", "Carol", "Orb"]
REPLY = json.dumps(
    {
        "title": "The Orb team",
        "nodes": [{"name": n, "type": "person", "isolated_context": f"{n} is on the Orb team"} for n in PEOPLE],
        "contexts": [{"name": n, "isolated_context": f"{n} is on the Orb team"} for n in PEOPLE],
        "relationships": [
            {"source_name": "Alice", "target_name": "Bob", "relationship_type": "works_with",
             "natural_language": "Alice works with Bob"},
            {"source_name": "Alice", "target_name": "Orb", "relationship_type": "leads",
             "natural_language": "Alice leads Orb"},
        ],
    }
)


class Killed(BaseException):
    """The process dies here: no retry, no failure handler runs."""


class Model:
    """A scripted model that dies on call ``die_at`` (counting calls made, 1-based)."""

    def __init__(self, die_at=None):
        self.made, self.die_at = 0, die_at

    def complete(self, messages, **_kw):
        if self.made + 1 == self.die_at:
            raise Killed
        self.made += 1
        return REPLY, {"finish_reason": "stop", "truncated": False}


class Keywords:
    """The keyword index as a dict of documents."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    def index_node(self, node_id, **doc):
        self.docs[node_id] = doc

    def get_node(self, node_id):
        return self.docs.get(node_id)

    def delete_node(self, node_id):
        self.docs.pop(node_id, None)

    def replace_note_passages(self, note_id, title, passages):
        self.docs[f"passages:{note_id}"] = {"title": title, "passages": passages}

    def __getattr__(self, name):  # anything else the pipeline asks of it is a no-op
        return lambda *a, **k: None


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 + 0.01 for b in digest[:DIMS]]


def _stores(tmp_path, name):
    qdrant = QdrantService(col_cores=f"{name}_cores", col_relationships=f"{name}_rels",
                           col_contexts=f"{name}_ctx")
    qdrant._client = QdrantClient(":memory:")
    qdrant._ensure_collections()
    graph = GraphService(db_path=str(tmp_path / f"{name}.kuzu"), qdrant=qdrant)
    return graph, qdrant, Keywords()


def _snapshot(note_id, graph, qdrant, keywords) -> dict:
    """The stores' content with every generated id replaced by the name it stands for."""
    names = {r["id"]: r["name"] or r["id"] for r in graph.execute_query("MATCH (n:Node) RETURN n.id AS id, n.name AS name")}
    names[note_id] = "NOTE"
    unid = lambda v: names.get(v, v) if isinstance(v, str) else v  # noqa: E731
    skip = ("relationship_id", "ingested_at", "last_updated")

    def scrub(v):
        if isinstance(v, dict):
            return {scrub(k): scrub(x) for k, x in v.items() if k not in skip}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, str):
            return unid(v.replace(note_id, "NOTE"))
        return v

    def points(col):
        out = []
        for p in qdrant.client.scroll(col, limit=10_000, with_vectors=True)[0]:
            payload = scrub(p.payload)
            out.append(json.dumps([payload, [round(x, 5) for x in p.vector]], sort_keys=True))
        return sorted(out)

    return {
        "nodes": sorted(
            (r["k"], r["n"], r["t"])
            for r in graph.execute_query("MATCH (n:Node) RETURN n.kind AS k, n.name AS n, n.type AS t")
        ),
        "references": sorted(
            (unid(r["a"]), unid(r["b"]))
            for r in graph.execute_query("MATCH (a:Node)-[:REFERENCES]->(b:Node) RETURN a.id AS a, b.id AS b")
        ),
        "edges": sorted(
            (unid(r["a"]), r["t"], unid(r["b"]), tuple(sorted(map(unid, r["ids"] or []))), r["c"])
            for r in graph.execute_query(
                "MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node) "
                "RETURN a.id AS a, r.rel_type AS t, b.id AS b, r.note_ids AS ids, r.mention_count AS c"
            )
        ),
        "qdrant": {role: points(c) for role, c in (("cores", qdrant._col_cores), ("rels", qdrant._col_rels),
                                                    ("contexts", qdrant._col_contexts), ("passages", qdrant._col_passages))},
        "keywords": sorted(json.dumps(scrub({k: v}), sort_keys=True, default=str) for k, v in keywords.docs.items()),
    }


def _llm(model: Model) -> LLMService:
    svc = LLMService.__new__(LLMService)
    svc.provider = svc.ingestion_provider = "openai"
    svc.get_ingestion_model = svc.get_chat_model = lambda: "scripted"
    svc._complete = model.complete
    return svc


async def _ingest(stores, model: Model, note_id: str, kb_id: str):
    graph, qdrant, keywords = stores
    wf = IngestionWorkflow(graph=graph, qdrant=qdrant, meili=keywords, llm=_llm(model), kb_id=kb_id)
    return await wf.process_note(NoteInput(content=NOTE, created_at="2026-10-01T09:00:00+00:00"), note_id)


async def _note(kb_id) -> str:
    note_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as s:
        s.add(Note(id=note_id, kb_id=kb_id, title=None, processing_stage="Queued for ingestion"))
        await s.commit()
    return note_id


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion_checkpoint, "resolve_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", DIMS)
    monkeypatch.setattr(embedding_service, "embed_documents", lambda texts: [_vector(t) for t in texts])
    monkeypatch.setattr(embedding_service, "embed_query", _vector)
    return tmp_path


def _clean_run(tmp_path):
    async def main():
        await init_db()
        stores = _stores(tmp_path, "clean")
        model = Model()
        kb = f"kb-{uuid.uuid4().hex[:6]}"
        note_id = await _note(kb)
        result = await _ingest(stores, model, note_id, kb)
        assert result["status"] == "success"
        return _snapshot(note_id, *stores), model.made, note_id

    return asyncio.run(main())


#: Where the process dies: before a model reply, or just after a store write.
KILL_POINTS = ["model call", ("graph", "create_or_update_relationship"), ("qdrant", "append_node_item"),
               ("qdrant", "upsert_node_relationships")]


@pytest.mark.parametrize("die_at", KILL_POINTS, ids=lambda k: k if isinstance(k, str) else k[1])
def test_killed_anywhere_resumes_to_the_same_stores_without_paying_twice(pipeline, monkeypatch, die_at):
    clean, calls, _ = _clean_run(pipeline)

    async def main():
        stores = _stores(pipeline, "killed")
        kb = f"kb-{uuid.uuid4().hex[:6]}"
        note_id = await _note(kb)
        first = Model(die_at=1 if die_at == "model call" else None)
        if die_at != "model call":
            store = {"graph": stores[0], "qdrant": stores[1]}[die_at[0]]
            real = getattr(store, die_at[1])
            left = [1]

            def write_then_die(*a, **k):
                out = real(*a, **k)
                if left[0]:
                    left[0] -= 1
                    raise Killed
                return out

            monkeypatch.setattr(store, die_at[1], write_then_die)
        with pytest.raises(Killed):
            await _ingest(stores, first, note_id, kb)
        second = Model()
        assert (await _ingest(stores, second, note_id, kb))["status"] == "success"
        return _snapshot(note_id, *stores), first.made + second.made

    resumed, paid = asyncio.run(main())
    assert resumed == clean
    assert paid == calls

