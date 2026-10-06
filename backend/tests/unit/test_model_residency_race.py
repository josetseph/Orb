"""A model unloaded between "is it loaded?" and "use it" is loaded again, not called as None."""

from app.services.local_models import LocalGgufReranker


def test_reranker_reloads_when_unloaded_before_scoring(monkeypatch):
    reranker = LocalGgufReranker()
    loads = []

    def fake_ensure_loaded():
        # First time: another request unloads the model right after it was "loaded". Second time: it stays.
        loads.append(1)
        reranker._model = None if len(loads) == 1 else object()

    monkeypatch.setattr(reranker, "ensure_loaded", fake_ensure_loaded)
    monkeypatch.setattr(reranker, "_score_one", lambda query, doc: float(len(doc)))
    out = reranker.rerank("q", ["a", "bbb"])
    assert len(loads) == 2
    assert [r["document"] for r in out] == ["bbb", "a"]
