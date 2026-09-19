"""Unit tests for app/services/qdrant_service.py — payload contract.

Tests verify the exact shape of upsert payloads and filter construction
without requiring a live Qdrant instance.
"""

import pytest
from unittest.mock import MagicMock

from qdrant_client.models import FieldCondition, Filter

from app.services.qdrant_service import QdrantService


@pytest.fixture(autouse=True)
def _vector_dims(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMBEDDING_DIMENSIONS", 768)


def _make_service() -> QdrantService:
    """Return a QdrantService with a mocked client, bypassing __init__ I/O."""
    svc = QdrantService.__new__(QdrantService)
    svc._client = MagicMock()
    svc._retry_at = float("inf")  # never try to reconnect in tests
    svc._col_cores = "kb_node_cores"
    return svc


# ── upsert_node_core payload shape ────────────────────────────────────────────


class TestUpsertNodeCorePayload:
    """Verify that the payload dict passed to client.upsert is constructed correctly."""

    def _get_payload(self, svc: QdrantService, **kwargs) -> dict:
        """Call upsert_node_core and return the payload from the PointStruct."""
        svc.upsert_node_core(**kwargs)
        call_kwargs = svc.client.upsert.call_args.kwargs
        point = call_kwargs["points"][0]
        return point.payload

    def test_required_fields_always_present(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
        )
        assert payload["node_id"] == "n1"
        assert payload["name"] == "Alice"
        assert payload["type"] == "person"

    def test_description_omitted_when_falsy(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
        )
        assert "description" not in payload

    def test_description_included_when_truthy(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="A brave explorer",
            description_vector=[0.1] * 768,
        )
        assert payload["description"] == "A brave explorer"

    def test_community_level_omitted_when_none(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
            community_level=None,
        )
        assert "community_level" not in payload

    def test_community_level_included_when_provided(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
            community_level=2,
        )
        assert payload["community_level"] == 2

    def test_extra_payload_merged(self):
        svc = _make_service()
        payload = self._get_payload(
            svc,
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
            extra_payload={"custom_key": "custom_value"},
        )
        assert payload["custom_key"] == "custom_value"

    def test_disabled_service_does_not_call_client(self):
        svc = _make_service()
        svc._client = None
        svc.upsert_node_core(
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
        )

    def test_null_client_does_not_raise(self):
        svc = _make_service()
        svc._client = None
        # Should return early without error
        svc.upsert_node_core(
            node_id="n1",
            name="Alice",
            node_type="person",
            description="",
            description_vector=[0.1] * 768,
        )


# ── search_node_cores filter construction ─────────────────────────────────────


class TestSearchNodeCoresFilter:
    """Verify that the qdrant Filter is built correctly based on arguments."""

    def _query_filter(self, svc: QdrantService, **kwargs) -> Filter | None:
        """Call search_node_cores and return the query_filter that was passed."""
        svc.search_node_cores(**kwargs)
        call_kwargs = svc.client.query_points.call_args.kwargs
        return call_kwargs.get("query_filter")

    def _default_args(self, **overrides):
        args = {
            "query_vector": [0.1] * 768,
            "limit": 5,
            "min_score": 0.5,
            "node_type": None,
            "community_level": None,
        }
        args.update(overrides)
        return args

    def test_no_filters_when_both_none(self):
        svc = _make_service()
        qf = self._query_filter(svc, **self._default_args())
        assert qf is None

    def test_node_type_filter_added_when_provided(self):
        svc = _make_service()
        qf = self._query_filter(svc, **self._default_args(node_type="person"))
        assert isinstance(qf, Filter)
        assert len(qf.must) == 1
        condition = qf.must[0]
        assert isinstance(condition, FieldCondition)
        assert condition.key == "type"

    def test_community_level_filter_added_when_provided(self):
        svc = _make_service()
        qf = self._query_filter(svc, **self._default_args(community_level=2))
        assert isinstance(qf, Filter)
        assert len(qf.must) == 1
        condition = qf.must[0]
        assert isinstance(condition, FieldCondition)
        assert condition.key == "community_level"

    def test_both_filters_combined_in_must(self):
        svc = _make_service()
        qf = self._query_filter(
            svc, **self._default_args(node_type="person", community_level=2)
        )
        assert isinstance(qf, Filter)
        assert len(qf.must) == 2
        keys = {c.key for c in qf.must}
        assert keys == {"type", "community_level"}

    def test_disabled_service_returns_empty_list(self):
        svc = _make_service()
        svc._client = None
        result = svc.search_node_cores(
            query_vector=[0.1] * 768,
            limit=5,
            min_score=0.5,
        )
        assert result == []
