"""``delete_kb`` never opens Kuzu just to close it, and never recreates Qdrant collections."""

import threading
from types import SimpleNamespace

import pytest

from app.services import kb_registry as kbr


class _Conn:
    def execute(self, *_):
        return None

    def commit(self):
        return None

    def close(self):
        return None


class _Ctx:
    def __init__(self, graph=None):
        self._graph = graph
        self.opened = False

    @property
    def graph(self):
        self.opened = True
        return self._graph


@pytest.fixture()
def registry(monkeypatch):
    reg = kbr.KBRegistry.__new__(kbr.KBRegistry)
    reg._lock = threading.RLock()
    reg._metadata = {"kb1": {"name": "KB 1", "vault_path": "/nope", "kuzu_path": "/nope/kuzu_graph"}}
    reg._cache = {}
    monkeypatch.setattr(kbr, "_connect", lambda: _Conn())
    monkeypatch.setattr(reg, "_cleanup_stores", lambda meta: None)
    return reg


def test_delete_does_not_open_a_never_opened_graph(registry):
    ctx = _Ctx(graph=None)
    registry._cache["kb1"] = ctx
    assert registry.delete_kb("kb1", delete_vault_files=False) is True
    assert ctx.opened is False


def test_delete_closes_an_open_graph(registry):
    closed = []
    ctx = _Ctx(graph=SimpleNamespace(close=lambda: closed.append(True)))
    registry._cache["kb1"] = ctx
    registry.delete_kb("kb1", delete_vault_files=False)
    assert closed == [True] and ctx.opened is False


def test_cleanup_stores_uses_a_client_without_recreating_collections(monkeypatch, tmp_path):
    reg = kbr.KBRegistry.__new__(kbr.KBRegistry)
    deleted = []

    def _ctor(*_a, **_k):
        raise AssertionError("QdrantService() would recreate the collections")

    monkeypatch.setattr(kbr, "QdrantService", _ctor)
    monkeypatch.setattr(
        kbr,
        "qdrant_service",
        SimpleNamespace(
            is_available=lambda: True,
            client=SimpleNamespace(delete_collection=deleted.append),
        ),
    )
    import app.services.meilisearch_service as ms

    monkeypatch.setattr(ms, "MeilisearchService", lambda index_name: SimpleNamespace(is_available=lambda: False))
    monkeypatch.setattr(kbr, "resolve_data_dir", lambda: tmp_path)
    reg._cleanup_stores(
        {
            "qdrant_col_cores": "c",
            "qdrant_col_rels": "r",
            "qdrant_col_contexts": "x",
            "typesense_collection": "m",
            "kuzu_path": str(tmp_path / "elsewhere" / "kuzu_graph"),
        }
    )
    assert deleted == ["c", "r", "x"]
