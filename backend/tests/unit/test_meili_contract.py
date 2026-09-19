"""
Unit tests for MeilisearchService contract.

Regression guard:
  - index_node includes both node_id and name
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_meili_service():
    from app.services.meilisearch_service import MeilisearchService

    svc = MeilisearchService.__new__(MeilisearchService)
    svc._client = MagicMock()
    svc.collection = "test_nodes"
    svc.is_available = MagicMock(return_value=True)
    index = MagicMock()
    task = MagicMock()
    task.task_uid = 1
    index.add_documents.return_value = task
    svc._index = MagicMock(return_value=index)
    svc.client.wait_for_task = MagicMock()
    return svc, index


class TestIndexNode:
    def test_index_node_includes_node_id_and_name(self):
        svc, index = _make_meili_service()

        svc.index_node(
            node_id="xyz-456",
            name="Bob Jones",
            node_type="person",
        )

        docs = index.add_documents.call_args[0][0]
        assert docs[0]["node_id"] == "xyz-456"
        assert docs[0]["name"] == "Bob Jones"
