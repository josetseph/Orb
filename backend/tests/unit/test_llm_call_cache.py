"""The experiment cache replays a model call only when nothing that decides its reply changed."""

import json

import pytest

from app.core.config import settings
from app.services.llm import LLMService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    service = LLMService.__new__(LLMService)
    service.provider = service.ingestion_provider = "local"
    service.get_base_url = lambda: None
    service.get_chat_model = lambda: "chat-model"
    service.get_ingestion_model = lambda: "ingest-model"
    service.calls = []

    def provider(messages, **kwargs):
        service.calls.append((messages, kwargs))
        return f"reply {len(service.calls)}", {"finish_reason": "stop", "truncated": False, "thinking": None}

    service._chat_provider = provider
    monkeypatch.setattr(settings, "LLM_CALL_CACHE_DIR", str(tmp_path))
    return service


def ask(svc, text="hello", **kwargs):
    return svc._chat([{"role": "user", "content": text}], **kwargs)[0]


def test_an_identical_call_is_replayed_as_written(svc, tmp_path):
    assert ask(svc) == ask(svc) == "reply 1"
    assert len(svc.calls) == 1
    stored = json.loads(next(tmp_path.rglob("*.json")).read_text())
    assert stored["text"] == "reply 1" and stored["model"] == "chat-model"


@pytest.mark.parametrize(
    "change",
    [dict(text="hello!"), dict(temperature=0.2), dict(max_tokens=64), dict(json_mode=True), dict(ingestion=True), dict(model="other")],
    ids=["prompt", "temperature", "max-tokens", "json-mode", "ingestion-model", "explicit-model"],
)
def test_anything_that_decides_the_reply_misses(svc, change):
    ask(svc)
    ask(svc, **change)
    assert len(svc.calls) == 2


def test_the_endpoint_is_part_of_the_key(svc):
    ask(svc)
    svc.get_base_url = lambda: "http://127.0.0.1:1234/v1"
    ask(svc)
    assert len(svc.calls) == 2


def test_off_unless_configured(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LLM_CALL_CACHE_DIR", None)
    assert [ask(svc), ask(svc)] == ["reply 1", "reply 2"]
    assert not list(tmp_path.rglob("*.json"))


def test_a_failed_call_stores_nothing(svc, tmp_path):
    def boom(messages, **kwargs):
        raise RuntimeError("endpoint fell over")

    svc._chat_provider = boom
    with pytest.raises(RuntimeError):
        ask(svc)
    assert not list(tmp_path.rglob("*.json"))
