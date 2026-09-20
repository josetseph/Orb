"""``GET /graph/notes/{id}/neighbors`` is a 404 for a note that does not exist."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api  # noqa: F401  # registers routers first; api_desktop alone is a circular import
from app import api_desktop
from app.api.deps import get_kb
from app.core.database import get_db


class _DB:
    def __init__(self, found):
        self.found = found

    async def execute(self, *_):
        return SimpleNamespace(scalar_one_or_none=lambda: self.found)


def _client(found, monkeypatch):
    async def payload(_db, kb_id, note_id):
        return {"nodes": [], "edges": [], "center_id": note_id}

    monkeypatch.setattr(api_desktop, "note_neighborhood_payload", payload)
    app = FastAPI()
    app.include_router(api_desktop.router)
    app.dependency_overrides[get_kb] = lambda: SimpleNamespace(kb_id="default")
    app.dependency_overrides[get_db] = lambda: _DB(found)
    return TestClient(app)


def test_unknown_note_is_404(monkeypatch):
    res = _client(None, monkeypatch).get("/api/v1/graph/notes/nope/neighbors")
    assert res.status_code == 404
    assert res.json()["detail"] == "Note not found"


def test_known_note_returns_its_neighborhood(monkeypatch):
    res = _client("n1", monkeypatch).get("/api/v1/graph/notes/n1/neighbors")
    assert res.status_code == 200
    assert res.json()["center_id"] == "n1"
