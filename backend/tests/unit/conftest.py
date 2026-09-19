"""
Shared pytest fixtures for unit tests.

Unit tests run without live infrastructure — all service boundaries
(Kuzu, Qdrant, Typesense, Postgres) are replaced with lightweight
stubs or in-memory fakes so tests remain fast and deterministic.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Event loop (required for async tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Minimal settings stub
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Override env-sensitive settings so unit tests never need a .env file."""
    from app.core import config

    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "lm_studio", raising=False)


# ---------------------------------------------------------------------------
# LLM service stub
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm_service():
    """Return a MagicMock that mimics LLMService for unit tests."""
    svc = MagicMock()
    svc.select_relevant_relationships = AsyncMock(return_value=[])
    svc.select_relevant_docs_with_reasoning = AsyncMock(
        return_value={"selected": [], "reasoning": ""}
    )
    svc.generate_node_enrichment_async = AsyncMock(
        return_value={
            "description": "Test description.",
            "title": "Test",
            "facts": [],
            "questions": [],
        }
    )
    return svc


# ---------------------------------------------------------------------------
# Graph service stub
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_graph_service():
    svc = MagicMock()
    svc.get_related_nodes = MagicMock(return_value=[])
    svc.find_paths_between_nodes = MagicMock(return_value=[])
    svc.resolve_node_id = MagicMock(return_value=None)
    svc.execute_query = MagicMock(return_value=[])
    return svc


# ---------------------------------------------------------------------------
# Typesense service stub
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_meili_service():
    svc = MagicMock()
    svc.is_available = MagicMock(return_value=True)
    svc.index_node = MagicMock()
    svc.delete_node = MagicMock()
    return svc


# Backward-compatible alias for older tests
@pytest.fixture()
def mock_typesense_service(mock_meili_service):
    return mock_meili_service


# ---------------------------------------------------------------------------
# Qdrant service stub
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_qdrant_service():
    svc = MagicMock()
    svc.find_node_id_by_name = MagicMock(return_value=None)
    svc.upsert_node = AsyncMock(return_value=True)
    svc.search_node_cores = MagicMock(return_value=[])
    return svc


# ---------------------------------------------------------------------------
# Sample node / relationship data helpers
# ---------------------------------------------------------------------------


def make_node(name: str, kind: str = "indexable", node_id: str | None = None) -> dict:
    return {
        "id": node_id or f"node-{name.lower().replace(' ', '-')}",
        "name": name,
        "kind": kind,
        "description": f"Description of {name}.",
    }


def make_relationship(
    source: str, target: str, rel_type: str = "RELATED_TO", confidence: float = 0.9
) -> dict:
    return {
        "source_name": source,
        "target_name": target,
        "rel_type": rel_type,
        "confidence": confidence,
    }
