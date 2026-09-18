"""Pydantic schemas for chat conversations and messages."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    """One prior turn used as model context."""

    role: str
    content: str


class ChatInput(BaseModel):
    """Request body for the chat endpoint."""

    query: str = Field(min_length=1)
    request_id: str | None = None
