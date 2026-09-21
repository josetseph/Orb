"""Re-ingesting a note replaces what it said before instead of piling on.

Runs ``_write_ontology`` against a real Kuzu file with an in-memory stand-in
for Qdrant, so REFERENCES / SEMANTIC_REL / orphan cleanup is exercised for real.
"""

from unittest.mock import MagicMock

import pytest

from app.schemas.extraction import ExtractedRelationship, Extraction, Node
from app.services.graph import GraphService
from app.workflows import ingestion
from app.workflows.ingestion import IngestionWorkflow, _context_key


class FakeQdrant:
    def __init__(self, rels_ok=True):
        self.cores: dict[str, dict] = {}
        self.rels: dict[str, dict] = {}
        self.deleted_nodes: list[str] = []
        self.rels_ok = rels_ok
        self._last_upsert_error = None

    def find_node_ids_by_names(self, names):
        by_name = {c["name"]: nid for nid, c in self.cores.items()}
        return {n: by_name.get(n) for n in names}

    def upsert_node_cores(self, cores):
        for c in cores:
            self.cores[c["node_id"]] = c
        return True

    def upsert_node_relationships(self, rels):
        if not self.rels_ok:
            return False
        for r in rels:
            self.rels[r["relationship_id"]] = r
        return True

    def delete_relationships(self, ids):
        for i in ids:
            self.rels.pop(i, None)

    def delete_node(self, nid):
        self.cores.pop(nid, None)
        self.deleted_nodes.append(nid)
        self.rels = {
            k: v for k, v in self.rels.items()
            if nid not in (v["source_node_id"], v["target_node_id"])
        }


def _ext(names, rels=()):
    return Extraction(
        title="T",
        nodes=[Node(name=n, type="person", isolated_context=f"{n} ctx") for n in names],
        relationships=[
            ExtractedRelationship(source_name=a, target_name=b, relationship_type="knows", natural_language=f"{a} knows {b}")
            for a, b in rels
        ],
    )


@pytest.fixture
def wf(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ingestion.embedding_service, "embed_documents", lambda texts: [[0.1] * 4 for _ in texts]
    )
    fq = FakeQdrant()
    graph = GraphService(db_path=str(tmp_path / "g.kuzu"), qdrant=fq)
    w = IngestionWorkflow(graph=graph, qdrant=fq, meili=MagicMock(), llm=MagicMock(), kb_id="t")
    yield w
    graph.close()


def _snapshot(wf):
    g = wf._graph
    nodes = {r["id"]: r["kind"] for r in g.execute_query("MATCH (n:Node) RETURN n.id AS id, n.kind AS kind")}
    refs = g.execute_query("MATCH (a:Node)-[:REFERENCES]->(b:Node) RETURN a.id AS a, b.id AS b")
    rels = g.execute_query(
        "MATCH (a:Node)-[r:SEMANTIC_REL]->(b:Node) "
        "RETURN a.id AS a, b.id AS b, r.mention_count AS mc, r.relationship_id AS rid"
    )
    return {
        "nodes": nodes,
        "refs": sorted((r["a"], r["b"]) for r in refs),
        # An edge deleted and re-asserted gets a fresh relationship_id; what
        # must hold is that the same rows exist and the counters do not drift.
        "rels": sorted((r["a"], r["b"], r["mc"]) for r in rels),
        "qdrant_rels": len(wf._qdrant.rels),
        "qdrant_cores": sorted(wf._qdrant.cores),
    }


def _names(wf):
    return {r["name"] for r in wf._graph.execute_query(
        "MATCH (n:Node) WHERE n.kind = 'indexable' RETURN n.name AS name"
    )}


def test_same_extraction_twice_changes_nothing(wf):
    ext = _ext(["Alice", "Bob"], [("Alice", "Bob")])
    wf._write_ontology("n1", "body", ext, "2026-01-01", "T")
    first = _snapshot(wf)
    assert len(first["refs"]) == 2 and len(first["rels"]) == 1 and first["rels"][0][2] == 1
    assert first["qdrant_rels"] == 1

    wf._write_ontology("n1", "body", ext, "2026-01-01", "T")
    assert _snapshot(wf) == first, "ids, edge counts, mention_count and Qdrant points all stable"
    assert wf._qdrant.deleted_nodes == []


def test_changed_extraction_drops_what_only_this_note_said(wf):
    wf._write_ontology("n1", "body", _ext(["Alice", "Bob"], [("Alice", "Bob")]), "2026-01-01", "T")
    wf._write_ontology("n2", "body", _ext(["Bob", "Carol"], [("Bob", "Carol")]), "2026-01-01", "T")
    bob_id = next(nid for nid, c in wf._qdrant.cores.items() if c["name"] == "bob")
    alice_id = next(nid for nid, c in wf._qdrant.cores.items() if c["name"] == "alice")

    wf._write_ontology("n1", "body", _ext(["Dave"]), "2026-01-01", "T")

    assert _names(wf) == {"bob", "carol", "dave"}, "alice is gone, bob survives via n2"
    assert alice_id in wf._qdrant.deleted_nodes and bob_id not in wf._qdrant.deleted_nodes
    wf._meili.delete_node.assert_called_once_with(alice_id)
    snap = _snapshot(wf)
    carol_id = next(nid for nid, c in wf._qdrant.cores.items() if c["name"] == "carol")
    assert [(a, b) for a, b, _ in snap["rels"]] == [(bob_id, carol_id)]
    assert snap["qdrant_rels"] == 1, "alice→bob's relationship point was removed"
    assert ("n1", bob_id) not in snap["refs"] and ("n2", bob_id) in snap["refs"]


def test_shared_edge_is_uncounted_not_deleted(wf):
    ext = _ext(["Alice", "Bob"], [("Alice", "Bob")])
    wf._write_ontology("n1", "body", ext, "2026-01-01", "T")
    wf._write_ontology("n2", "body", ext, "2026-01-01", "T")
    assert _snapshot(wf)["rels"][0][2] == 2
    wf._write_ontology("n1", "body", _ext(["Alice", "Bob"]), "2026-01-01", "T")
    assert _snapshot(wf)["rels"][0][2] == 1, "n2 still asserts it"


def test_relationship_points_fail_closed(wf):
    wf._qdrant.rels_ok = False
    with pytest.raises(RuntimeError, match="node_relationships"):
        wf._write_ontology("n1", "body", _ext(["Alice", "Bob"], [("Alice", "Bob")]), "2026-01-01", "T")


def test_context_key_ignores_the_stored_date_suffix():
    assert _context_key("Alice runs the lab. - 2026-05-01T10:00:00") == _context_key("Alice runs the lab.")
    assert _context_key("Alice runs the lab. - 2026-05-01") == "Alice runs the lab."
    assert _context_key("a - b") == "a - b", "only an ISO date suffix is stripped"
