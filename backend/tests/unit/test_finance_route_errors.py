"""Every finance route maps Firefly failures through ``_finance_error``."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api_desktop
from app.api.deps import get_finance_kb


def test_get_list_route_returns_json_502_on_firefly_error(monkeypatch):
    async def _boom(_kb):
        raise RuntimeError("Firefly GET /api/v1/accounts failed (500): nope")

    monkeypatch.setattr(api_desktop.firefly_service, "list_accounts", _boom)
    app = FastAPI()
    app.include_router(api_desktop.router)
    app.dependency_overrides[get_finance_kb] = lambda: SimpleNamespace(
        kb_id="default", name="Default", finance_enabled=True
    )
    res = TestClient(app).get("/api/v1/finance/accounts")
    assert res.status_code == 502
    assert "nope" in res.json()["detail"]


def test_every_finance_route_is_wrapped():
    unwrapped = [
        r.path
        for r in api_desktop.router.routes
        if r.path.startswith("/api/v1/finance") and not hasattr(r.endpoint, "__wrapped__")
    ]
    assert not unwrapped
