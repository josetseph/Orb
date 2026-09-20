"""``GET /notes/{id}/status`` tells "never ingested" apart from "in progress"."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import notes
from app.api.deps import get_kb
from app.api.notes import note_status
from app.core.database import get_db


@pytest.mark.parametrize(
    "processed,failed,stage,expected",
    [
        (True, False, "Ingestion complete", "completed"),
        (False, True, "Ingestion failed: boom", "failed"),
        (False, False, "Saved", "not_ingested"),
        (False, False, None, "not_ingested"),
        (False, False, "", "not_ingested"),
        (False, False, "Changed on disk — re-ingest when ready", "not_ingested"),
        (False, False, "External delete detected — review in Orb", "not_ingested"),
        (False, False, "Ingestion complete", "not_ingested"),  # flags reset by admin
        (False, False, "Queued for ingestion", "queued"),
        (False, False, "Queued for vault re-ingest", "queued"),
        (False, False, "Extracting entities", "processing"),
        (False, False, "Transcribing audio", "processing"),
    ],
)
def test_status_vocabulary(processed, failed, stage, expected):
    assert note_status(processed, failed, stage) == expected


def _client(row):
    class _DB:
        async def execute(self, *_):
            return SimpleNamespace(one_or_none=lambda: row, scalar_one_or_none=lambda: row)

    app = FastAPI()
    app.include_router(notes.router)
    app.dependency_overrides[get_kb] = lambda: SimpleNamespace(kb_id="default")
    app.dependency_overrides[get_db] = lambda: _DB()
    return TestClient(app)


def test_route_reports_not_ingested_for_a_saved_note():
    res = _client(("n1", False, False, "Saved", None)).get("/api/v1/notes/n1/status")
    assert res.status_code == 200
    assert res.json()["status"] == "not_ingested"


def test_route_reports_processing_mid_pipeline():
    res = _client(("n1", False, False, "Extracting entities", "gemma")).get(
        "/api/v1/notes/n1/status"
    )
    assert res.json()["status"] == "processing"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/notes/nope/status"),
        ("get", "/api/v1/notes/nope/attachments/jobs"),
        ("post", "/api/v1/notes/nope/ingest/cancel"),
    ],
)
def test_unknown_note_is_404_not_empty(method, path):
    res = getattr(_client(None), method)(path)
    assert res.status_code == 404
