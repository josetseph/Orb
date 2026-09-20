"""Settings → Local runtime: saved to runtime_config.json, applied to settings, models unloaded."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.core import config, runtime_config
from app.services import local_models


def test_put_persists_applies_and_unloads(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_config, "_data_path", lambda: tmp_path / "runtime_config.json")
    unloaded = []
    monkeypatch.setattr(local_models.local_llama_runtime, "unload", lambda: unloaded.append("llama"))
    monkeypatch.setattr(local_models.local_gguf_reranker, "unload", lambda: unloaded.append("rerank"))
    for key in runtime_config.LOCAL_RUNTIME_KEYS:  # restore after the test
        monkeypatch.setattr(config.settings, key.upper(), getattr(config.settings, key.upper()))

    app = FastAPI()
    app.include_router(settings_api.router)
    client = TestClient(app)

    body = client.get("/api/v1/settings/local-runtime").json()
    assert body["llama_n_ctx"] == 16384 and body["llama_swa_full"] is True
    body.update(llama_n_ctx=8192, llama_swa_full=False, model_idle_seconds=0, llama_n_gpu_layers=None)
    res = client.put("/api/v1/settings/local-runtime", json=body)
    assert res.status_code == 200 and res.json()["llama_n_ctx"] == 8192

    assert config.settings.LLAMA_SWA_FULL is False
    assert local_models.model_idle_seconds() == 0
    assert local_models._llama_metal_safe_kwargs({})["swa_full"] is False
    assert runtime_config.load()["llama_n_ctx"] == 8192
    assert unloaded == ["llama", "rerank"]

    assert client.put("/api/v1/settings/local-runtime", json={**body, "llama_n_ctx": 10}).status_code == 422
