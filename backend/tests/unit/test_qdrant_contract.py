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


# ── relationship points: written with their id, deleted with their nodes ─────


def _svc_all():
    svc = _make_service()
    svc._col_rels = "kb_node_relationships"
    svc._col_contexts = "kb_node_isolated_contexts"
    svc._last_upsert_error = None
    return svc


class TestRelationshipPoints:
    def test_upsert_returns_true_and_carries_relationship_id(self):
        svc = _svc_all()
        ok = svc.upsert_node_relationships([{
            "relationship_id": "r1", "natural_language": "a knows b",
            "nl_vector": [0.1] * 768, "source_node_id": "a", "target_node_id": "b",
        }])
        assert ok is True
        payload = svc.client.upsert.call_args.kwargs["points"][0].payload
        assert payload["relationship_id"] == "r1"

    def test_upsert_returns_false_on_rejection(self):
        svc = _svc_all()
        svc.client.upsert.side_effect = RuntimeError("400")
        ok = svc.upsert_node_relationships([{
            "relationship_id": "r1", "natural_language": "x",
            "nl_vector": [0.1] * 768, "source_node_id": "a", "target_node_id": "b",
        }])
        assert ok is False and "400" in svc._last_upsert_error
        assert svc.upsert_node_relationships([]) is True

    def test_two_edges_with_the_same_sentence_both_come_back(self):
        svc = _svc_all()
        pts = [
            MagicMock(payload={"relationship_id": rid, "natural_language": "knows",
                               "source_node_id": "a", "target_node_id": "b"})
            for rid in ("r1", "r2")
        ]
        svc.client.scroll.side_effect = [(pts, None), ([], None)]
        rows = svc.get_relationships_for_node_ids(["a"])
        assert [r["relationship_id"] for r in rows] == ["r1", "r2"]

    def test_delete_node_clears_its_relationship_points(self):
        svc = _svc_all()
        svc.delete_node("n1")
        by_col = {c.kwargs["collection_name"]: c.kwargs["points_selector"]
                  for c in svc.client.delete.call_args_list}
        assert set(by_col) == {svc._col_cores, svc._col_contexts, svc._col_rels}
        conds = by_col[svc._col_rels].filter.should
        assert {(c.key, c.match.value) for c in conds} == {
            ("source_node_id", "n1"), ("target_node_id", "n1")}

    def test_delete_relationships_by_id(self):
        import uuid

        svc = _svc_all()
        svc.delete_relationships(["r1"])
        call = svc.client.delete.call_args.kwargs
        assert call["collection_name"] == svc._col_rels
        assert call["points_selector"] == [str(uuid.uuid5(uuid.NAMESPACE_OID, "r1"))]


# ── search_all_collections: a day filter narrows, it does not skip ───────────


class TestDayScopedSearch:
    def test_day_only_still_searches_cores_and_rels(self):
        svc = _svc_all()
        svc.client.query_points.return_value = MagicMock(points=[])
        svc.search_all_collections([0.1] * 768, 5, 0.5, contexts_filter=Filter(must=[]), day_only=True)
        calls = {c.kwargs["collection_name"]: c.kwargs["query_filter"]
                 for c in svc.client.query_points.call_args_list}
        assert set(calls) == {svc._col_cores, svc._col_rels, svc._col_contexts}
        assert calls[svc._col_rels] is None
        excluded = calls[svc._col_cores].must_not[0]
        assert excluded.key == "type" and set(excluded.match.any) == {"community", "temporal_digest"}
        assert calls[svc._col_contexts].must == []

