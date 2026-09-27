"""Retitling a note renames its graph node at once, not on the next ingest."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import notes
from app.api.deps import get_kb
from app.core.database import get_db


class _Graph:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def execute_query(self, query, params):
        if self.fail:
            raise RuntimeError("graph down")
        self.calls.append((query, params))
        return []


def _client(monkeypatch, graph, old_title):
    note = SimpleNamespace(id="n1", title=old_title, created_at=None, updated_at=None,
                           processed=True, processing_stage="Ingestion complete")

    async def _get(*_):
        return note

    def _persist(n, _kb, _content, title=None):
        if title is not None:
            n.title = title

    async def _noop(*_a, **_k):
        return None

    class _DB:
        commit = refresh = staticmethod(_noop)

    monkeypatch.setattr(notes, "_get_note_or_404", _get)
    monkeypatch.setattr(notes, "persist_note_body", _persist)
    monkeypatch.setattr(notes, "refresh_note_links", _noop)
    monkeypatch.setattr(notes, "_note_response", lambda n, _kb: {"id": n.id, "title": n.title})
    monkeypatch.setattr("app.services.vault_ops.rename_note_file_for_title", _noop)
    app = FastAPI()
    app.include_router(notes.router)
    app.dependency_overrides[get_kb] = lambda: SimpleNamespace(kb_id="default", graph=graph)
    app.dependency_overrides[get_db] = lambda: _DB()
    return TestClient(app)


def test_rename_updates_the_graph_note_name(monkeypatch):
    graph = _Graph()
    res = _client(monkeypatch, graph, "Old").put("/api/v1/notes/n1", json={"content": "x", "title": "New name"})
    assert res.status_code == 200
    assert len(graph.calls) == 1
    query, params = graph.calls[0]
    assert "SET n.name" in query and params == {"id": "n1", "name": "New name"}


def test_saving_without_a_rename_leaves_the_graph_alone(monkeypatch):
    graph = _Graph()
    _client(monkeypatch, graph, "Same").put("/api/v1/notes/n1", json={"content": "x", "title": "Same"})
    _client(monkeypatch, graph, "Same").put("/api/v1/notes/n1", json={"content": "y"})
    assert graph.calls == []


def test_a_graph_failure_does_not_fail_the_save(monkeypatch):
    res = _client(monkeypatch, _Graph(fail=True), "Old").put("/api/v1/notes/n1", json={"content": "x", "title": "New"})
    assert res.status_code == 200 and res.json()["title"] == "New"
