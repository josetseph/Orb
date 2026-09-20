"""
Shared pytest fixtures for unit tests.

Unit tests run without live infrastructure — each module stubs the boundary
it crosses (Kuzu, Qdrant, Meilisearch, the LLM) itself; the only shared
fixture pins the LLM provider regardless of the shell environment.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Minimal settings stub
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Pin env-sensitive settings so the shell environment cannot steer unit tests."""
    from app.core import config

    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)
