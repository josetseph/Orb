"""An unknown/foreign/deleted conversation id is a 404, not a silent new thread."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api import chat as chat_api
from app.schemas.chat import ChatInput
from app.services.chat_store import chat_store


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setattr(chat_store, "get_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_store, "create_conversation", AsyncMock(return_value={"id": "new"}))
    return chat_store


def test_ensure_conversation_creates_only_without_id(store):
    assert asyncio.run(store.ensure_conversation(None, "kb")) == {"id": "new"}
    assert asyncio.run(store.ensure_conversation("gone", "kb")) is None
    store.create_conversation.assert_awaited_once()


def test_start_chat_404s_on_unknown_conversation(store, monkeypatch):
    monkeypatch.setattr(chat_api, "require_ai", lambda kb: None)
    monkeypatch.setattr(store, "add_message", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_api.start_chat(ChatInput(query="hi", conversation_id="gone"), kb=MagicMock(kb_id="kb")))
    assert exc.value.status_code == 404
    store.add_message.assert_not_awaited()
