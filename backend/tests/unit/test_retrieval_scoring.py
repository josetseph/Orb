"""Retrieval is scored by note id against gold groups, not by model-written titles."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
from evaluate import gold_groups, load_note_files, score_retrieval  # noqa: E402


def test_groups_ids_and_ingestion(tmp_path, monkeypatch):
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({"musique": {"a.md": "id-a", "b1.md": "id-b1", "b2.md": "failed", "c.md": "pending:id-c"}}))
    monkeypatch.setenv("ORB_BENCH_PROGRESS", str(progress))
    files = load_note_files("musique")
    assert files == {"id-a": "a.md", "id-b1": "b1.md"}

    case = {"supporting_notes": [["a.md"], ["b1.md", "b2.md"], ["d.md"]], "required_notes": ["ignored.md"]}
    groups = gold_groups(case)
    scored = score_retrieval(["id-b1", "id-x"], groups, files)
    assert scored["retrieval_recall"] == 1 / 3  # one of three groups, through either passage of the article
    assert scored["retrieval_precision"] == 1.0  # id-x is not in the index map, so not counted
    assert scored["gold_ingested"] == 2 / 3  # d.md never made it into the index
    assert gold_groups({"required_notes": ["x.md", "y.md"]}) == [["x.md"], ["y.md"]]


def test_rerank_and_embedding_cache_replays(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.call_cache import cached

    monkeypatch.setattr(settings, "LLM_CALL_CACHE_DIR", str(tmp_path))
    calls = []
    first = cached("rerank", ["model.gguf", "q", ["a", "b"], None], lambda: calls.append(1) or [{"index": 0, "relevance_score": 0.98}])
    again = cached("rerank", ["model.gguf", "q", ["a", "b"], None], lambda: calls.append(1) or [{"index": 0, "relevance_score": 0.97}])
    assert first == again == [{"index": 0, "relevance_score": 0.98}] and calls == [1]
    monkeypatch.setattr(settings, "LLM_CALL_CACHE_DIR", None)
    assert cached("rerank", ["x"], lambda: 5) == 5
