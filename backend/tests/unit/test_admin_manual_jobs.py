"""Manual admin jobs: the auto-trigger flags gate only the automatic path."""

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


def test_rebuild_communities_runs_with_auto_flag_off(harness, monkeypatch):
    client, calls = harness
    monkeypatch.setattr(settings, "COMMUNITY_DETECTION_ENABLED", False)
    res = client.post("/api/v1/admin/rebuild-communities")
    assert res.status_code == 200 and res.json()["status"] == "started"
    assert calls == ["communities"]


def test_temporal_digests_409_when_disabled(harness, monkeypatch):
    client, calls = harness
    monkeypatch.setattr(settings, "TEMPORAL_DIGESTS_ENABLED", False)
    res = client.post("/api/v1/admin/build-temporal-digests")
    assert res.status_code == 409
    assert "TEMPORAL_DIGESTS_ENABLED" in res.json()["detail"]
    assert calls == []


def test_temporal_digests_409_while_ingesting(harness, monkeypatch):
    from app.services.ingestion_tracker import ingestion_tracker

    client, calls = harness
    monkeypatch.setattr(settings, "TEMPORAL_DIGESTS_ENABLED", True)
    monkeypatch.setattr(ingestion_tracker, "has_active_ingestions", lambda kb_id=None: True)
    res = client.post("/api/v1/admin/build-temporal-digests")
    assert res.status_code == 409 and calls == []


def test_temporal_digests_start_when_enabled(harness, monkeypatch):
    client, calls = harness
    monkeypatch.setattr(settings, "TEMPORAL_DIGESTS_ENABLED", True)
    res = client.post("/api/v1/admin/build-temporal-digests", json={"period": "week"})
    assert res.status_code == 200 and res.json()["status"] == "started"
    assert calls == [("digests", "week")]
