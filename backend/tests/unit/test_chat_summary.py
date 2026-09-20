"""Messages that fall out of the history window are folded into a rolling summary."""

import asyncio

import pytest

from app.core import config
from app.core.database import init_db
from app.schemas.chat import render_history
from app.services.chat_store import chat_store


@pytest.fixture()
def conv(monkeypatch):
    monkeypatch.setattr(config.settings, "CHAT_HISTORY_MAX_MESSAGES", 4)
    asyncio.run(init_db())
    return asyncio.run(chat_store.create_conversation("kb-summary"))["id"]


def _say(cid, start, stop):
    for i in range(start, stop):
        role = "user" if i % 2 == 0 else "assistant"
        asyncio.run(chat_store.add_message(cid, role, f"m{i}"))


def test_summary_covers_only_what_left_the_window(conv):
    calls = []

    def summarize(existing, turns):
        calls.append((existing, [t["content"] for t in turns]))
        return f"S{len(calls)}"

    _say(conv, 0, 4)
    assert asyncio.run(chat_store.refresh_summary(conv, summarize)) is False  # fits the window

    _say(conv, 4, 6)
    assert asyncio.run(chat_store.refresh_summary(conv, summarize)) is True
    assert calls == [(None, ["m0", "m1"])]
    assert asyncio.run(chat_store.refresh_summary(conv, summarize)) is False  # nothing new

    _say(conv, 6, 8)
    assert asyncio.run(chat_store.refresh_summary(conv, summarize)) is True
    assert calls[-1] == ("S1", ["m2", "m3"])

    history = asyncio.run(chat_store.get_recent_history(conv))
    assert [t.role for t in history] == ["summary", "user", "assistant", "user", "assistant"]
    assert history[0].content == "S2" and history[1].content == "m4"


def test_render_history_puts_summary_first_and_truncates(monkeypatch):
    monkeypatch.setattr(config.settings, "CHAT_HISTORY_MAX_MESSAGES", 2)
    text = render_history(
        [
            {"role": "summary", "content": "we discussed X"},
            {"role": "user", "content": "old"},
            {"role": "user", "content": "a" * 20},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "b"},
        ],
        10,
    )
    assert text == "Earlier in this conversation (summary): we discussed X\nUser: aaaaaaa...\nAssistant: b"
