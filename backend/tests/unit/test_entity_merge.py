"""Fold one entity into another: one node, nothing lost, safe to kill anywhere, undoable."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, init_db
from app.models.entity_merge import EntityMerge
from app.models.job import Job
from app.schemas.extraction import NoteInput
from app.services import entity_merge, jobs
from app.workflows.ingestion import IngestionWorkflow
from tests.unit.test_resume import Keywords, Killed, Model, _llm, _note, _stores, _vector, pipeline  # noqa: F401

A, B, X, Y = "node_a", "node_b", "node_x", "node_y"
NOTES = ["n1", "n2", "n3"]
# source, target, type, id, sentence, notes
EDGES = [
    (A, X, "owned_by", "r1", "the denver post is owned by john", ["n1"]),
    (B, X, "owned_by", "r2", "the post is owned by john", ["n2"]),  # A already has this one
    (B, Y, "located_in", "r3", "the post is in colorado", ["n2"]),
    (Y, B, "home_of", "r4", "colorado is home to the post", ["n3"]),
    (A, B, "same_as", "r5", "they are one paper", ["n1"]),  # nothing left of it once merged
]


def _build(tmp_path, name):
    stores = graph, qdrant, keywords = _stores(tmp_path, name)
    wf = IngestionWorkflow(graph=graph, qdrant=qdrant, meili=keywords, llm=_llm(Model()), kb_id="kb")
    for note in NOTES:
        graph.execute_query("CREATE (:Node {id: $id, kind: 'note', name: $id})", {"id": note})
    for nid, ename, etype in ((A, "denver post", "org"), (B, "the denver post", "org"), (X, "john", "person"),
                              (Y, "colorado", "place")):
        graph.execute_query("CREATE (:Node {id: $id, kind: 'indexable', name: $n, type: $t})",
                            {"id": nid, "n": ename, "t": etype})
        qdrant.upsert_node_core(nid, ename, etype, _vector(ename))
    for source, target, rel_type, rid, text, notes in EDGES:
        graph.absorb_edge(source, target, {"rel_type": rel_type, "relationship_id": rid, "note_ids": notes,
                                           "note_id": notes[0], "confidence": 0.5, "ingested_at": "t"})
        qdrant.upsert_node_relationship(rid, text, _vector(text), source, target)
    graph.add_references(["n1"], A)
    graph.add_references(["n1", "n2", "n3"], B)
    for node, note, text in ((A, "n1", "one"), (B, "n1", "one"), (B, "n2", "two"), (B, "n3", "three")):
        qdrant.append_node_item(qdrant._col_contexts, node, text, _vector(text), note_id=note)
    graph.set_aliases(["denver's post"], B)
    asyncio.run(wf._reindex_node(A))
    asyncio.run(wf._reindex_node(B))
    return wf, stores


def _state(graph, qdrant, keywords) -> dict:
    points = {}
    for col in (qdrant._col_cores, qdrant._col_rels, qdrant._col_contexts):
        role = col.rsplit("_", 1)[-1]
        points[role] = sorted(
            json.dumps([str(p.id), p.payload, [round(x, 5) for x in p.vector]], sort_keys=True)
            for p in qdrant.client.scroll(col, limit=1000, with_vectors=True)[0]
        )
    q = graph.execute_query
    return {
        "nodes": sorted((r["i"], r["n"], r["t"]) for r in q(
            "MATCH (n:Node) WHERE n.kind = 'indexable' RETURN n.id AS i, n.name AS n, n.type AS t")),
        "aliases": sorted((r["n"], r["i"]) for r in q("MATCH (a:Alias) RETURN a.name AS n, a.node_id AS i")),
        "references": sorted((r["a"], r["b"]) for r in q(
            "MATCH (a:Node)-[:REFERENCES]->(b:Node) RETURN a.id AS a, b.id AS b")),
        "edges": sorted(
            (r["a"], r["t"], r["b"], r["rid"], tuple(r["ids"]), r["c"], r["conf"]) for r in q(
                "MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node) RETURN a.id AS a, r.rel_type AS t, b.id AS b, "
                "r.relationship_id AS rid, r.note_ids AS ids, r.mention_count AS c, r.confidence AS conf")
        ),
        "points": points,
        "keywords": json.dumps(keywords.docs, sort_keys=True, default=str),
    }


async def _new_merge(keep=A, absorbed=B) -> int:
    await init_db()
    async with AsyncSessionLocal() as s:
        record = EntityMerge(kb_id=f"kb-{uuid.uuid4().hex[:6]}", keep_id=keep, absorbed_id=absorbed)
        s.add(record)
        await s.commit()
        return record.id


def _clean_merge(tmp_path):
    wf, stores = _build(tmp_path, "clean")

    async def main():
        await entity_merge.merge(wf, await _new_merge())

    asyncio.run(main())
    return wf, stores


def test_merge_leaves_one_entity_with_everything_on_it(pipeline):
    _, stores = _clean_merge(pipeline)
    graph, qdrant, keywords = stores
    state = _state(*stores)

    assert [n[0] for n in state["nodes"]] == [A, X, Y]
    assert state["aliases"] == [("denver's post", A), ("the denver post", A)]
    assert set(state["references"]) == {("n1", A), ("n2", A), ("n3", A)}
    edges = {(e[0], e[1], e[2]): e for e in state["edges"]}
    assert set(edges) == {(A, "owned_by", X), (A, "located_in", Y), (Y, "home_of", A)}  # no A-[same_as]->A
    assert edges[(A, "owned_by", X)][3:6] == ("r1", ("n1", "n2"), 2)
    assert edges[(A, "located_in", Y)][3:7] == ("r3", ("n2",), 1, 0.5)

    assert sorted(qdrant.get_node_content_by_id(A)["isolated_contexts"]) == ["one", "three", "two"]
    assert qdrant.get_node_content_by_id(B) is None
    rels = {r["relationship_id"]: r for r in qdrant.get_relationships_for_node_ids([A, X, Y])}
    assert set(rels) == {"r1", "r3", "r4"}
    assert rels["r4"]["target_node_id"] == A
    assert B not in keywords.docs
    assert keywords.docs[A]["aliases"] == ["denver's post", "the denver post"]

    assert graph.find_nodes_by_exact_names(["the denver post"]) == {"the denver post": A}
    assert graph.resolve_node_id("the denver post") == A


KILL_POINTS = [("graph", "absorb_edge"), ("qdrant", "retarget_relationship"), ("graph", "add_references"),
               ("qdrant", "move_context_points"), ("graph", "set_aliases"), ("graph", "delete_entity"),
               ("qdrant", "delete_node")]


def _die_after_first(monkeypatch, store, method):
    real, left = getattr(store, method), [1]

    def write_then_die(*a, **k):
        out = real(*a, **k)
        if left[0]:
            left[0] -= 1
            raise Killed
        return out

    monkeypatch.setattr(store, method, write_then_die)


@pytest.mark.parametrize("kill", KILL_POINTS, ids=lambda k: k[1])
def test_killed_anywhere_resumes_to_the_same_result(pipeline, monkeypatch, kill):
    _, clean = _clean_merge(pipeline)
    wf, stores = _build(pipeline, "killed")
    _die_after_first(monkeypatch, {"graph": stores[0], "qdrant": stores[1]}[kill[0]], kill[1])

    async def main():
        merge_id = await _new_merge()
        with pytest.raises(Killed):
            await entity_merge.merge(wf, merge_id)
        await entity_merge.merge(wf, merge_id)

    asyncio.run(main())
    assert _state(*stores) == _state(*clean)


def test_undo_puts_everything_back_even_when_killed_midway(pipeline, monkeypatch):
    wf, stores = _build(pipeline, "undo")
    before = _state(*stores)

    async def main():
        merge_id = await _new_merge()
        await entity_merge.merge(wf, merge_id)
        _die_after_first(monkeypatch, stores[0], "retract_edge")
        with pytest.raises(Killed):
            await entity_merge.undo(wf, merge_id)
        await entity_merge.undo(wf, merge_id)
        async with AsyncSessionLocal() as s:
            assert (await s.get(EntityMerge, merge_id)).state == "undone"

    asyncio.run(main())
    assert _state(*stores) == before
    assert stores[0].resolve_aliases(["the denver post"]) == {}


@pytest.mark.parametrize("keep,absorbed", [(A, A), (A, "missing"), (A, "n1"), (A, "doc")])
def test_only_two_existing_entities_can_merge(pipeline, keep, absorbed):
    wf, stores = _build(pipeline, "refuse")
    stores[0].execute_query("CREATE (:Node {id: 'doc', kind: 'indexable', name: 'a.pdf', type: 'document'})")
    before = _state(*stores)

    async def main():
        merge_id = await _new_merge(keep, absorbed)
        with pytest.raises(ValueError):
            await entity_merge.merge(wf, merge_id)

    asyncio.run(main())
    assert _state(*stores) == before


def test_undoing_needs_the_entity_it_went_into(pipeline):
    wf, (graph, _, _) = _build(pipeline, "chain")

    async def main():
        first = await _new_merge(A, B)
        await entity_merge.merge(wf, first)
        await entity_merge.merge(wf, await _new_merge(X, A))
        with pytest.raises(ValueError, match="undo that merge first"):
            await entity_merge.undo(wf, first)

    asyncio.run(main())
    # Merged twice: every earlier name now means the survivor.
    assert graph.resolve_aliases(["the denver post", "denver post"]) == {
        "the denver post": {"id": X, "name": "john"}, "denver post": {"id": X, "name": "john"},
    }


class AliasModel(Model):
    def complete(self, messages, **_kw):
        self.made += 1
        return json.dumps({
            "title": "The paper",
            "nodes": [{"name": n, "type": "org", "isolated_context": f"{n} said this"} for n in ("The Denver Post", "John")],
            "contexts": [{"name": n, "isolated_context": f"{n} said this"} for n in ("The Denver Post", "John")],
            "relationships": [
                {"source_name": "The Denver Post", "target_name": "John", "relationship_type": "hired",
                 "natural_language": "The Denver Post hired John"},
                {"source_name": "The Denver Post", "target_name": "Denver Post", "relationship_type": "same_as",
                 "natural_language": "The Denver Post is the Denver Post"},
            ],
        }), {"finish_reason": "stop", "truncated": False}


def test_a_later_note_using_the_merged_name_lands_on_the_entity(pipeline):
    wf, stores = _build(pipeline, "later")
    graph, qdrant, _ = stores
    wf._llm = _llm(AliasModel())

    async def main():
        await entity_merge.merge(wf, await _new_merge())
        note_id = await _note("kb")
        note = NoteInput(content="The Denver Post hired John.", created_at="2026-10-01T09:00:00+00:00")
        assert (await wf.process_note(note, note_id))["status"] == "success"
        return note_id

    note_id = asyncio.run(main())
    names = [n[1] for n in _state(*stores)["nodes"]]
    assert "the denver post" not in names and names.count("denver post") == 1
    assert graph.get_entity(A)["name"] == "denver post"
    assert note_id in graph.note_references(A)
    assert any("said this" in c for c in qdrant.get_node_content_by_id(A)["isolated_contexts"])
    hired = [e for e in graph.entity_edges(A) if note_id in e["note_ids"]]
    assert [(e["source"], e["target"]) for e in hired] == [(A, X)]
    assert not [e for e in graph.entity_edges(A) if e["source"] == e["target"]]


def test_merge_and_undo_run_as_jobs_in_their_turn(pipeline, monkeypatch):
    from app.services.kb_registry import kb_registry

    wf, stores = _build(pipeline, "jobs")
    before = _state(*stores)
    monkeypatch.setattr(kb_registry, "get_kb", lambda _id: type("KB", (), {"get_ingestion_workflow": lambda self: wf})())

    async def drain():
        await jobs._drain()
        await asyncio.gather(*list(jobs._running.values()))

    async def main():
        await init_db()
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Job))
            await s.execute(delete(EntityMerge))
            await s.commit()
        merge_id = await entity_merge.request_merge("kb", A, B)
        assert await entity_merge.request_merge("kb", A, B) == merge_id  # asked twice, one merge
        await drain()
        assert stores[0].get_entity(B) is None
        await entity_merge.request_undo(merge_id)
        await drain()
        async with AsyncSessionLocal() as s:
            return (await s.execute(select(Job.kind, Job.state).order_by(Job.id))).all()

    assert [tuple(r) for r in asyncio.run(main())] == [(jobs.MERGE_ENTITY, "done"), (jobs.UNMERGE_ENTITY, "done")]
    assert _state(*stores) == before
