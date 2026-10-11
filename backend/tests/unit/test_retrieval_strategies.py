"""Round-7 retrieval strategies: the PageRank walk, evidence order, and the oracle's evidence choice."""

import sys
from pathlib import Path

from app.services.llm import edges_order
from app.services.pagerank import personalized_pagerank
from app.services.retrieval import RetrievalService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
from oracle import evidence  # noqa: E402


def test_pagerank_decays_with_distance_and_keeps_mass():
    ranks = personalized_pagerank([("a", "b"), ("b", "c"), ("x", "y")], {"a": 1.0})
    assert ranks["a"] > ranks["b"] > ranks["c"] > 0
    assert "x" not in ranks  # unreachable from the seed
    assert abs(sum(ranks.values()) - 1.0) < 1e-9


def test_pagerank_follows_seed_weights():
    ranks = personalized_pagerank([("a", "n1"), ("b", "n2")], {"a": 3.0, "b": 1.0})
    assert ranks["n1"] > ranks["n2"]
    assert personalized_pagerank([("a", "b")], {}) == {}


def test_edges_order_puts_the_best_at_both_ends():
    docs = [{"id": i, "rerank_score": s} for i, s in enumerate([0.1, 0.9, 0.5, 0.7, 0.3])]
    assert [d["rerank_score"] for d in edges_order(docs)] == [0.9, 0.5, 0.1, 0.3, 0.7]
    unscored = [{"id": i} for i in range(4)]
    assert [d["id"] for d in edges_order(unscored)] == [0, 2, 3, 1]


def test_ppr_passages_adds_unseen_notes_reached_from_the_seeds():
    class Graph:
        def walk_edges(self):
            # note1 mentions e1 (a search hit); e1 relates to e2, which note2 mentions; note3 is far away.
            edges = [("note1", "e1"), ("e1", "e2"), ("note2", "e2"), ("note3", "e9")]
            return edges, {"note1", "note2", "note3"}

    class Qdrant:
        def get_note_passages(self, note_ids):
            return [{"note_id": n, "title": n.upper(), "index": 0, "content": f"text of {n}"} for n in note_ids]

    svc = RetrievalService.__new__(RetrievalService)
    svc._graph, svc._qdrant = Graph(), Qdrant()
    selected = [{"original_obj": {"name": "E1", "node_id": "e1"}, "rerank_score": 0.8},
                {"original_obj": {"name": "NOTE1 (passage 1)", "node_id": "note1"}, "rerank_score": 0.6}]
    docs = svc._ppr_passages(selected, surfaced_names=set())
    assert [d["linked_notes"][0]["id"] for d in docs] == ["note2"]  # note1 is a seed; note3 is unreachable
    assert docs[0]["type"] == "ppr_passage" and docs[0]["text"] == "text of note2"
    assert svc._ppr_passages(selected, surfaced_names={"NOTE2 (passage 1)"}) == []


def test_oracle_reads_one_file_per_gold_group_or_every_note():
    tc = {"supporting_notes": [["a.md", "a2.md"], ["b.md"]], "notes": ["a.md", "b.md", "x.md"]}
    assert evidence(tc, "gold") == ["a.md", "b.md"]
    assert evidence(tc, "all") == ["a.md", "b.md", "x.md"]
    hotpot = {"required_notes": ["p.md", "q.md"], "all_notes": ["p.md", "q.md", "r.md"]}
    assert evidence(hotpot, "gold") == ["p.md", "q.md"]
    assert evidence(hotpot, "all") == ["p.md", "q.md", "r.md"]
