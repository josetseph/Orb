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
    async def payload(_db, kb_id, note_id, depth=1):
        return {"nodes": [], "edges": [], "center_id": note_id, "depth": depth}

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


def test_depth_is_passed_through_and_bounded(monkeypatch):
    client = _client("n1", monkeypatch)
    assert client.get("/api/v1/graph/notes/n1/neighbors?depth=2").json()["depth"] == 2
    assert client.get("/api/v1/graph/notes/n1/neighbors?depth=3").status_code == 422


def test_neighborhood_walks_links_both_ways_up_to_depth(monkeypatch):
    import asyncio

    from app.services import wikilinks

    # a -> b -> c -> d, and e -> a (a backlink)
    full = {
        "nodes": [{"id": x, "title": x.upper(), "type": "note"} for x in "abcde"],
        "edges": [
            {"source": "a", "target": "b", "type": "wikilink"},
            {"source": "b", "target": "c", "type": "wikilink"},
            {"source": "c", "target": "d", "type": "wikilink"},
            {"source": "e", "target": "a", "type": "wikilink"},
        ],
    }

    async def fake_full(_db, _kb):
        return full

    monkeypatch.setattr(wikilinks, "notes_graph_payload", fake_full)
    one = asyncio.run(wikilinks.note_neighborhood_payload(None, "kb", "a", 1))
    two = asyncio.run(wikilinks.note_neighborhood_payload(None, "kb", "a", 2))
    assert {n["id"]: n["hop"] for n in one["nodes"]} == {"a": 0, "b": 1, "e": 1}
    assert {n["id"]: n["hop"] for n in two["nodes"]} == {"a": 0, "b": 1, "e": 1, "c": 2}
    assert {(e["source"], e["target"]) for e in two["edges"]} == {("a", "b"), ("b", "c"), ("e", "a")}
