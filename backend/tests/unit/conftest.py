"""
Shared pytest fixtures for unit tests.

Unit tests run without live infrastructure — each module stubs the boundary
it crosses (Kuzu, Qdrant, Meilisearch, the LLM) itself; the only shared
fixture pins the LLM provider so no ``.env`` is needed.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Minimal settings stub
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Override env-sensitive settings so unit tests never need a .env file."""
    from app.core import config

    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)
