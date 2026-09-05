"""Per-KB finance switch: default-on, persistence, and the route gate."""

import pytest
from fastapi import HTTPException

from app.services.kb_registry import KBContext, finance_enabled_for


class TestFinanceEnabledFor:
    """Rows predating the column must not read as 'off'."""

    def test_absent_column_means_enabled(self):
        assert finance_enabled_for({}) is True

    def test_null_column_means_enabled(self):
        # SQLite backfills existing rows with NULL when the column is added
        # without a default; those KBs already have Firefly data.
        assert finance_enabled_for({"finance_enabled": None}) is True

    @pytest.mark.parametrize("stored,expected", [(1, True), (0, False), (True, True), (False, False)])
    def test_reads_stored_flag(self, stored, expected):
        assert finance_enabled_for({"finance_enabled": stored}) is expected


class TestKBContextDefault:
    def test_context_defaults_to_enabled(self):
        ctx = KBContext.__new__(KBContext)
        assert KBContext.finance_enabled is True
        assert ctx  # dataclass default is declared, not per-instance state


class TestFinanceGate:
    """``get_finance_kb`` refuses a KB whose finance section is off."""

    def _kb(self, enabled: bool) -> KBContext:
        kb = KBContext.__new__(KBContext)
        kb.name = "research"
        kb.finance_enabled = enabled
        return kb

    def test_passes_through_when_enabled(self):
        from app.api.deps import get_finance_kb

        kb = self._kb(True)
        assert get_finance_kb(kb) is kb

    def test_refuses_when_disabled(self):
        from app.api.deps import get_finance_kb

        with pytest.raises(HTTPException) as exc:
            get_finance_kb(self._kb(False))
        assert exc.value.status_code == 403
        assert "research" in exc.value.detail

    def test_every_finance_route_is_gated(self):
        """A new finance route must not silently bypass the switch."""
        from app.api.deps import get_finance_kb, get_kb
        from app.api_desktop import router

        ungated = []
        for route in router.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/v1/finance"):
                continue
            deps = {d.call for d in getattr(route, "dependant", None).dependencies} if getattr(route, "dependant", None) else set()
            if get_finance_kb in deps:
                continue
            # The workspace endpoint reports the off state instead of refusing.
            if path == "/api/v1/finance/workspace" and "GET" in (route.methods or set()):
                assert get_kb in deps
                continue
            ungated.append(f"{sorted(route.methods or [])} {path}")
        assert not ungated, f"finance routes missing the gate: {ungated}"


class TestWorkspaceReportsTheOffState:
    """The workspace route explains the switch rather than refusing."""

    @staticmethod
    def _kb(enabled: bool):
        kb = KBContext.__new__(KBContext)
        kb.kb_id, kb.name, kb.finance_enabled = "abc", "Research", enabled
        return kb

    @pytest.mark.asyncio
    async def test_disabled_kb_gets_its_own_status(self):
        from app.api_desktop import get_finance_workspace

        ws = await get_finance_workspace(self._kb(False))
        # Not "disabled": that already means FIREFLY_BASE_URL is unset for the
        # whole install, which no per-KB switch can fix.
        assert ws["status"] == "kb_disabled"
        assert ws["ready"] is False and ws["exists"] is False
        assert "Research" in ws["detail"]
        assert ws["kb_id"] == "abc" and ws["scope"] == "kb"

    @pytest.mark.asyncio
    async def test_enabled_kb_reaches_firefly(self, monkeypatch):
        from app.api_desktop import get_finance_workspace
        from app.services.firefly_service import firefly_service

        async def fake(kb):
            return {"ready": True, "status": "ready"}

        monkeypatch.setattr(firefly_service, "get_workspace", fake)
        assert (await get_finance_workspace(self._kb(True)))["status"] == "ready"
