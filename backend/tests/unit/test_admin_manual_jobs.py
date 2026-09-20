"""Manual admin jobs start their background task, or 409 while ingesting."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import admin
from app.api.deps import get_kb
from app.core.config import settings


@pytest.fixture()
def harness(monkeypatch):
    calls: list = []
    wf = SimpleNamespace(
        rebuild_leiden_communities=lambda: calls.append("communities"),
        build_temporal_digests=lambda period: calls.append(("digests", period)),
    )
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_kb] = lambda: SimpleNamespace(
        kb_id="default", get_ingestion_workflow=lambda: wf
    )
    monkeypatch.setattr(settings, "TEMPORAL_DIGEST_PERIOD", "month", raising=False)
    return TestClient(app), calls


def test_rebuild_communities_starts(harness):
    client, calls = harness
    res = client.post("/api/v1/admin/rebuild-communities")
    assert res.status_code == 200 and res.json()["status"] == "started"
    assert calls == ["communities"]


def test_temporal_digests_409_while_ingesting(harness, monkeypatch):
    from app.services.ingestion_tracker import ingestion_tracker

    client, calls = harness
    monkeypatch.setattr(ingestion_tracker, "has_active_ingestions", lambda kb_id=None: True)
    res = client.post("/api/v1/admin/build-temporal-digests")
    assert res.status_code == 409 and calls == []


def test_temporal_digests_start(harness):
    client, calls = harness
    res = client.post("/api/v1/admin/build-temporal-digests", json={"period": "week"})
    assert res.status_code == 200 and res.json()["status"] == "started"
    assert calls == [("digests", "week")]
