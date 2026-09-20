"""GET /chat/conversations/{id}/export reads through ChatStore.list_messages (dict rows)."""

import asyncio

import pytest


@pytest.fixture()
def rows(monkeypatch):
    from app.services import chat_store as cs

    data = [
        {"id": "m1", "role": "user", "content": "hi", "created_at": "2026-09-20T10:00:00", "sources": []},
        {"id": "m2", "role": "assistant", "content": "hello", "created_at": "2026-09-20T10:00:01", "sources": []},
    ]

    async def list_messages(conversation_id, kb_id=None):
        return data

    monkeypatch.setattr(cs.chat_store, "list_messages", list_messages)
    return data


def test_json_export_is_role_content_created_at(rows):
    from app.api_desktop import export_chat

    out = asyncio.run(export_chat("c1", format="json"))
    assert out == [{k: r[k] for k in ("role", "content", "created_at")} for r in rows]


def test_markdown_export_one_heading_per_message(rows):
    from app.api_desktop import export_chat

    resp = asyncio.run(export_chat("c1", format="markdown"))
    assert resp.media_type == "text/markdown"
    assert resp.body.decode() == "## You\n\nhi\n\n## Orb\n\nhello\n"
