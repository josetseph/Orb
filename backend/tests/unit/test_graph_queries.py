"""
Unit tests for graph service query paths.

Covers:
  - get_related_nodes depth=1 (fast path, never touches variable-length syntax)
  - get_related_nodes depth>1 (fixed path, no all() predicate in WHERE)
  - Post-filtering logic for confidence
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_graph_service_class():
    """Import GraphService with Kuzu constructors stubbed so unit tests stay isolated."""
    sys.modules.pop("app.services.graph", None)
    with (
        patch("kuzu.Database", return_value=MagicMock()),
        patch("kuzu.Connection", return_value=MagicMock()),
    ):
        graph_module = importlib.import_module("app.services.graph")
    return graph_module.GraphService


def _make_graph_service():
    """Return a GraphService with the Kuzu connection stubbed out."""
    GraphService = _get_graph_service_class()

    svc = GraphService.__new__(GraphService)
    svc._db = MagicMock()
    svc._conn = MagicMock()
    return svc


def _row(
    node_id: str,
    name: str,
    confidence_path: list[float] | None = None,
    depth: int = 1,
) -> dict:
    return {
        "node_id": node_id,
        "name": name,
        "label": "indexable",
        "depth": depth,
        "relationship_path": ["RELATED_TO"] * depth,
        "confidence_path": confidence_path or [0.9] * depth,
        "context_path": [None] * depth,
        "natural_language_path": [None] * depth,
    }


# ---------------------------------------------------------------------------
# get_related_nodes — depth=1 fast path
# ---------------------------------------------------------------------------


class TestGetRelatedNodesDepth1:
    def test_fast_path_returns_execute_query_results(self):
        """depth=1 should use the fast path and return raw results."""
        svc = _make_graph_service()
        expected = [_row("n1", "Alice")]

        with (
            patch.object(svc, "resolve_node_id", return_value="node-alice"),
            patch.object(svc, "execute_query", return_value=expected) as mock_eq,
        ):
            result = svc.get_related_nodes("Alice", max_depth=1)

        assert result == expected
        # Ensure the query sent does NOT contain variable-length syntax
        query_arg = mock_eq.call_args[0][0]
        assert (
            "*1.." not in query_arg
        ), "depth=1 path must not use variable-length MATCH"
        assert (
            "all(" not in query_arg.lower()
        ), "depth=1 path must not use all() predicate"

    def test_fast_path_returns_empty_when_node_not_found(self):
        svc = _make_graph_service()
        with patch.object(svc, "resolve_node_id", return_value=None):
            result = svc.get_related_nodes("Nonexistent", max_depth=1)
        assert result == []


# ---------------------------------------------------------------------------
# get_related_nodes — depth>1 fixed path (no all() in WHERE)
# ---------------------------------------------------------------------------


class TestGetRelatedNodesDepthGt1:
    def test_depth2_query_does_not_use_all_predicate_in_where(self):
        """The depth>1 query must not have all(...WHERE...) — that triggers KU_UNREACHABLE."""
        svc = _make_graph_service()

        with (
            patch.object(svc, "resolve_node_id", return_value="node-alice"),
            patch.object(svc, "execute_query", return_value=[]) as mock_eq,
        ):
            svc.get_related_nodes("Alice", max_depth=2)

        query_arg = mock_eq.call_args[0][0]
        # The WHERE clause must not contain all() applied to relationships(path)
        import re

        assert not re.search(
            r"WHERE\s+all\s*\(", query_arg, re.IGNORECASE
        ), "depth>1 query must not filter with all() in WHERE — triggers KU_UNREACHABLE"
