"""The reranker is mandatory and its failure surfaces; expansion notes stay citable; runtime errors surface."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services.retrieval import RetrievalService


def _cand(name: str, notes=None) -> dict:
    return {
        "text": name, "_rerank_text": name, "type": "entity_match",
        "original_obj": {"name": name}, "linked_notes": notes or [],
    }


@pytest.fixture()
def svc() -> RetrievalService:
    return RetrievalService.__new__(RetrievalService)


def _rank(svc, cands, rerank_result):
    with patch("app.services.reranker.reranker_service.rerank", AsyncMock(return_value=rerank_result)):
        return asyncio.run(svc._apply_reranker_logging("q", cands, top_n=2, score_threshold=0.05))


class TestRerankerMandatory:
    def test_no_scores_raises(self, svc):
        with pytest.raises(RuntimeError, match="no scores"):
            _rank(svc, [_cand("a"), _cand("b")], [])

    def test_missing_gguf_raises_through_service(self, svc, monkeypatch):
        from app.services import local_models

        monkeypatch.setattr(local_models, "reranker_gguf_path", lambda: None)
        with pytest.raises(RuntimeError, match="No reranker GGUF"):
            asyncio.run(svc._apply_reranker_logging("q", [_cand("a")], top_n=1))

    def test_model_scores_rank_and_threshold(self, svc):
        scores = [{"index": 0, "relevance_score": 0.01}, {"index": 1, "relevance_score": 0.9}, {"index": 2, "relevance_score": 0.5}]
        got = _rank(svc, [_cand("a"), _cand("b"), _cand("c")], scores)
        assert [c["text"] for c in got] == ["b", "c"]


def _run_loop(svc, expansion, step):
    llm = type("L", (), {})()
    llm.analyze_query = lambda q: {}
    llm.iterative_step = AsyncMock(side_effect=step)
    svc._llm_override = llm
    with patch.object(svc, "hybrid_search", AsyncMock(return_value=[_cand("origin", [{"id": "n1", "title": "T1"}])])), \
         patch.object(svc, "_expand_relevant_neighbors", AsyncMock(return_value=expansion)), \
         patch.object(svc, "_apply_reranker_logging", AsyncMock(side_effect=lambda q, c, **kw: c)), \
         patch.object(settings, "MAX_LOOP_ITERATIONS", 2):
        return asyncio.run(svc.retrieve_with_iterative_loop("q?"))


class TestIterativeLoop:
    def test_expansion_notes_fold_into_origin_doc(self, svc):
        expansion = [{**_cand("origin", [{"id": "n2", "title": "T2"}, {"id": "n1", "title": "T1"}]), "type": "graph_expansion"}]
        steps = [
            {"can_answer": False, "final_answer": None, "next_query": "sub", "reasoning": "", "full_answer": ""},
            {"can_answer": True, "final_answer": "42", "next_query": None, "reasoning": "r", "full_answer": "f", "thinking": None},
        ]
        answer, docs, _ = _run_loop(svc, expansion, steps)
        assert answer == "42" and len(docs) == 1
        assert [n["id"] for n in docs[0]["linked_notes"]] == ["n1", "n2"]

    def test_runtime_error_propagates(self, svc):
        with pytest.raises(RuntimeError, match="context window"):
            _run_loop(svc, [], RuntimeError("Prompt is ~9k tokens; context window is 8k"))
