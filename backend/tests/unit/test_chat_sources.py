"""Chat returns every source it gathered: a cap applied after the answer only hid sources."""

from app.workflows.chat import _clear_unverified_links


def test_all_sources_are_kept_and_unscored_ones_cite_no_notes():
    docs = [{"id": str(i), "rerank_score": 0.9, "linked_notes": ["n"]} for i in range(15)]
    docs.append({"id": "graph-only", "linked_notes": ["n"]})
    out = _clear_unverified_links(docs)
    assert len(out) == 16
    assert out[-1]["linked_notes"] == [] and out[0]["linked_notes"] == ["n"]
