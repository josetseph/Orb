"""Pydantic schemas for chat conversations and messages."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import settings


class ChatTurn(BaseModel):
    """One prior turn used as model context.

    ``role`` is ``user`` or ``assistant``; ``get_recent_history`` prepends one
    ``summary`` turn holding the recap of everything older than the window.
    """

    role: str
    content: str


def render_history(history: list[dict], max_chars: int) -> str:
    """Prompt text for prior turns: the summary recap first, then the last
    ``CHAT_HISTORY_MAX_MESSAGES`` user/assistant turns as ``User:``/``Assistant:``
    lines, each cut at ``max_chars``. Empty when nothing is usable."""
    lines: list[str] = []
    turns: list[tuple[str, str]] = []
    for turn in history:
        role = (turn.get("role") or "").strip().lower()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role == "summary":
            lines.append(f"Earlier in this conversation (summary): {content}")
        elif role in {"user", "assistant"}:
            turns.append(("User" if role == "user" else "Assistant", content))
    for label, content in turns[-settings.CHAT_HISTORY_MAX_MESSAGES :]:
        if len(content) > max_chars:
            content = content[: max_chars - 3].rstrip() + "..."
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


class ChatSource(BaseModel):
    """A note the answer drew on; ``sources`` on the chat response."""

    id: str
    title: str


class CreateConversationInput(BaseModel):
    """Optional body when explicitly creating a conversation."""

    title: str | None = None


class ChatInput(BaseModel):
    """Request body for ``POST /api/v1/chat/async``."""

    query: str = Field(min_length=1)
    request_id: str | None = None
    conversation_id: str | None = None
