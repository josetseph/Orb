"""Boot the real FastAPI app against a temp DATA_DIR with Qdrant/Meilisearch
unreachable, and walk the SQLite + vault contract end to end. No models, no
ingestion. Run in its own pytest process: the app binds DATA_DIR at import."""

import sys

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    if "app.core.config" in sys.modules:
        pytest.skip("app already imported with another DATA_DIR; run tests/integration alone")
    data = tmp_path_factory.mktemp("data")
    mp = pytest.MonkeyPatch()
    for key in ("DATA_DIR", "ORB_DATA_DIR"):
        mp.setenv(key, str(data))
    mp.setenv("ORB_MODELS_DIR", str(data / "models"))
    mp.setenv("ORB_PATHS_FILE", str(data / "no-paths.json"))  # never read ~/Library
    mp.delenv("ORB_DEFAULT_VAULT", raising=False)
    mp.setenv("QDRANT_PORT", "1")  # nothing listens: the stores must stay lazy
    mp.setenv("MEILI_PORT", "1")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def kb(client, tmp_path_factory):
    vault = tmp_path_factory.mktemp("vault")
    r = client.post("/api/v1/kb", json={"name": "smoke", "vault_path": str(vault)})
    assert r.status_code == 201, r.text
    assert (vault / "attachments").is_dir()
    return "smoke", vault


def test_health_and_default_kb(client):
    assert client.get("/health").json() == {"status": "healthy"}
    names = [k["name"] for k in client.get("/api/v1/kb").json()["knowledge_bases"]]
    assert "default" in names


def test_note_round_trips_through_vault(client, kb):
    name, vault = kb
    body = "# Hi\n\nfirst body"
    r = client.post("/api/v1/notes", params={"kb": name}, json={"title": "Hello", "content": body, "folder": "Life"})
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["rel_path"] == "Life/Hello.md"
    assert (vault / "Life" / "Hello.md").read_text() == body

    assert client.get(f"/api/v1/notes/{note['id']}", params={"kb": name}).json()["content"] == body

    r = client.put(f"/api/v1/notes/{note['id']}", params={"kb": name}, json={"content": "changed"})
    assert r.status_code == 200, r.text
    assert (vault / "Life" / "Hello.md").read_text() == "changed"

    r = client.delete(f"/api/v1/notes/{note['id']}", params={"kb": name})
    assert r.status_code == 200, r.text  # graph/index cleanup is best-effort while stores are down
    assert not (vault / "Life" / "Hello.md").exists()
    assert client.get(f"/api/v1/notes/{note['id']}", params={"kb": name}).status_code == 404
