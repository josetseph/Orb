"""Pydantic schemas for chat conversations and messages."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    """One prior turn used as model context."""

    role: str
    content: str


class ChatSource(BaseModel):
    """A note the answer drew on; ``sources`` on the chat response."""

    id: str
    title: str


class CreateConversationInput(BaseModel):
    """Optional body when explicitly creating a conversation."""

    title: str | None = None


class ChatInput(BaseModel):
    """Request body for sync and async chat endpoints."""

    query: str = Field(min_length=1)
    request_id: str | None = None
    conversation_id: str | None = None
