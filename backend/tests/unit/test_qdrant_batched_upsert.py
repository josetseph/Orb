"""Large upserts are chunked: one request has a size ceiling.

A point with a 2560-dim vector is ~27 KB as REST JSON, so 713 of them make a
~19 MB request that Qdrant rejects with 400 — losing every point, not just the
overflow, and leaving Kuzu nodes with no Qdrant counterpart.
"""

import pytest

from app.services import qdrant_service as qs
from app.services.qdrant_service import QdrantService


class FakeClient:
    def __init__(self, fail_on_batch=None, max_points=None):
        self.calls = []
        self.fail_on_batch = fail_on_batch
        self.max_points = max_points

    def upsert(self, collection_name, points):
        self.calls.append(len(points))
        if self.max_points and len(points) > self.max_points:
            raise RuntimeError("Unexpected Response: 400 (Bad Request)")
        if self.fail_on_batch == len(self.calls):
            raise RuntimeError("Unexpected Response: 400 (Bad Request)")


@pytest.fixture
def svc(monkeypatch):
    s = QdrantService.__new__(QdrantService)
    s.client = FakeClient()
    s._col_cores = "kb_node_cores"
    s._last_upsert_error = None
    monkeypatch.setattr(qs, "_UPSERT_BATCH_SIZE", 128)
    return s


def _points(n):
    return [f"p{i}" for i in range(n)]


class TestBatching:
    def test_the_reported_size_is_split(self, svc):
        svc._upsert_batched("c", _points(713))
        assert svc.client.calls == [128] * 5 + [73]
        assert sum(svc.client.calls) == 713

    def test_small_batch_is_one_call(self, svc):
        svc._upsert_batched("c", _points(10))
        assert svc.client.calls == [10]

    def test_exact_multiple(self, svc):
        svc._upsert_batched("c", _points(256))
        assert svc.client.calls == [128, 128]

    def test_empty_makes_no_call(self, svc):
        svc._upsert_batched("c", [])
        assert svc.client.calls == []

    def test_a_server_that_rejects_large_requests_now_succeeds(self, svc):
        """The regression: this raised 400 before chunking."""
        svc.client = FakeClient(max_points=200)
        svc._upsert_batched("c", _points(713))
        assert sum(svc.client.calls) == 713


class TestFailureReporting:
    def test_error_names_the_batch_and_progress(self, svc):
        svc.client = FakeClient(fail_on_batch=3)
        with pytest.raises(RuntimeError) as exc:
            svc._upsert_batched("c", _points(713))
        msg = str(exc.value)
        assert "batch 3 of 6" in msg
        assert "128 of 713 points" in msg
        assert "400" in msg

    def test_upsert_node_cores_records_why_it_failed(self, svc, monkeypatch):
        svc.client = FakeClient(fail_on_batch=1)
        monkeypatch.setattr(svc, "is_available", lambda: True)
        monkeypatch.setattr(svc, "_prepare_vector", lambda v: v)
        cores = [
            {"node_id": f"n{i}", "name": f"N{i}", "node_type": "Concept",
             "description_vector": [0.0] * 8}
            for i in range(5)
        ]
        assert svc.upsert_node_cores(cores) is False
        assert "400" in (svc._last_upsert_error or "")

    def test_success_returns_true(self, svc, monkeypatch):
        monkeypatch.setattr(svc, "is_available", lambda: True)
        monkeypatch.setattr(svc, "_prepare_vector", lambda v: v)
        cores = [{"node_id": "n", "name": "N", "node_type": "Concept",
                  "description_vector": [0.0] * 8}]
        assert svc.upsert_node_cores(cores) is True
