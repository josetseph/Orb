"""Cash accounts are listed, and the scope switch cache re-checks Firefly."""

import pytest

from app.services.firefly_service import FireflyService


def _service() -> FireflyService:
    svc = FireflyService.__new__(FireflyService)
    svc.base_url = "http://firefly"
    svc.runtime_file = ""
    svc._switched_group_id = None
    return svc


@pytest.mark.asyncio
async def test_list_accounts_includes_cash(monkeypatch):
    svc = _service()
    requested: list[str] = []

    async def _request(method, path, **kwargs):
        if path == "/api/v1/accounts":
            t = kwargs["params"]["type"]
            requested.append(t)
            return {"data": [{"id": f"id-{t}", "attributes": {"name": t, "type": t}}]}
        return {"data": []}

    async def _ids(_gid):
        return {"id-asset", "id-cash", "id-expense", "id-revenue", "id-liability"}

    monkeypatch.setattr(svc, "_request", _request)
    monkeypatch.setattr(svc, "_account_ids_for_group", _ids)
    rows = await svc._list_accounts_unlocked(7)
    assert "cash" in requested
    assert {r["account_type"] for r in rows} >= {"asset", "cash"}


def _scoped(monkeypatch, svc, in_use: int | None, *, attr_present=True):
    from app.services import firefly_service as fs

    monkeypatch.setattr(fs.kb_registry, "get_metadata", lambda _kb: {"firefly_group_id": 7})
    switches: list[str] = []
    monkeypatch.setattr(svc, "_run_php", lambda script: switches.append(script) or {})
    groups = [{"id": str(g), "attributes": {"title": f"g{g}"}} for g in (7, 9)]
    if attr_present:
        for g in groups:
            g["attributes"]["in_use"] = int(g["id"]) == in_use

    async def _request(method, path, **_):
        assert path == "/api/v1/user-groups"
        return {"data": groups}

    monkeypatch.setattr(svc, "_request", _request)
    return switches


class _KB:
    kb_id = "research"
    name = "Research"


@pytest.mark.asyncio
async def test_cached_switch_is_trusted_while_firefly_agrees(monkeypatch):
    svc = _service()
    svc._switched_group_id = 7
    switches = _scoped(monkeypatch, svc, in_use=7)
    assert await svc._activate_scope_locked(_KB()) == 7
    assert switches == []


@pytest.mark.asyncio
async def test_reswitches_when_firefly_ui_changed_the_administration(monkeypatch):
    svc = _service()
    svc._switched_group_id = 7
    switches = _scoped(monkeypatch, svc, in_use=9)
    assert await svc._activate_scope_locked(_KB()) == 7
    assert len(switches) == 1 and svc._switched_group_id == 7


@pytest.mark.asyncio
async def test_unknown_in_use_keeps_the_cache(monkeypatch):
    svc = _service()
    svc._switched_group_id = 7
    switches = _scoped(monkeypatch, svc, in_use=None, attr_present=False)
    await svc._activate_scope_locked(_KB())
    assert switches == []
