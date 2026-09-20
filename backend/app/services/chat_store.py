"""Persistence helpers for chat conversations and messages."""

# pylint: disable=wrong-import-order
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.log import get_logger
from app.models.chat import ChatConversation, ChatMessage
from app.schemas.chat import ChatTurn
from sqlalchemy import delete, select, update

logger = get_logger("ChatStore")

DEFAULT_TITLE = "New Chat"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _conversation_to_dict(conv: ChatConversation) -> dict[str, Any]:
    return {
        "id": conv.id,
        "kb_id": conv.kb_id,
        "title": conv.title,
        "created_at": _iso(conv.created_at),
        "updated_at": _iso(conv.updated_at),
    }


def _message_to_dict(msg: ChatMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "content": msg.content,
        "thinking": msg.thinking,
        "sources": (msg.metadata_json or {}).get("sources", []),
        "created_at": _iso(msg.created_at),
    }


class ChatStore:
    """Async CRUD for chat conversations scoped by knowledge base."""

    async def list_conversations(self, kb_id: str, limit: int = 50) -> list[dict]:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(ChatConversation)
                .where(
                    ChatConversation.kb_id == kb_id,
                    ChatConversation.deleted_at.is_(None),
                )
                .order_by(ChatConversation.updated_at.desc())
                .limit(limit)
            )
            return [_conversation_to_dict(c) for c in rows.scalars().all()]

    async def create_conversation(
        self, kb_id: str, title: str | None = None
    ) -> dict:
        conv_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        conv = ChatConversation(
            id=conv_id,
            kb_id=kb_id,
            title=title or DEFAULT_TITLE,
            created_at=now,
            updated_at=now,
        )
        async with AsyncSessionLocal() as session:
            session.add(conv)
            await session.commit()
            await session.refresh(conv)
            return _conversation_to_dict(conv)

    async def get_conversation(
        self, conversation_id: str, kb_id: str | None = None
    ) -> dict | None:
        async with AsyncSessionLocal() as session:
            clauses = [
                ChatConversation.id == conversation_id,
                ChatConversation.deleted_at.is_(None),
            ]
            if kb_id is not None:
                clauses.append(ChatConversation.kb_id == kb_id)
            row = await session.execute(select(ChatConversation).where(*clauses))
            conv = row.scalar_one_or_none()
            return _conversation_to_dict(conv) if conv else None

    async def delete_conversation(
        self, conversation_id: str, kb_id: str | None = None
    ) -> bool:
        async with AsyncSessionLocal() as session:
            clauses = [ChatConversation.id == conversation_id]
            if kb_id is not None:
                clauses.append(ChatConversation.kb_id == kb_id)
            result = await session.execute(
                update(ChatConversation)
                .where(*clauses)
                .values(deleted_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return result.rowcount > 0

    async def list_messages(
        self, conversation_id: str, kb_id: str | None = None
    ) -> list[dict]:
        async with AsyncSessionLocal() as session:
            if kb_id is not None:
                owned = await session.execute(
                    select(ChatConversation.id).where(
                        ChatConversation.id == conversation_id,
                        ChatConversation.kb_id == kb_id,
                        ChatConversation.deleted_at.is_(None),
                    )
                )
                if owned.scalar_one_or_none() is None:
                    return []
            rows = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.asc())
            )
            return [_message_to_dict(m) for m in rows.scalars().all()]

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        msg = ChatMessage(
            id=msg_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            thinking=thinking,
            metadata_json=metadata,
            created_at=now,
        )
        async with AsyncSessionLocal() as session:
            session.add(msg)
            await session.execute(
                update(ChatConversation)
                .where(ChatConversation.id == conversation_id)
                .values(updated_at=now)
            )
            await session.commit()
            await session.refresh(msg)
            return _message_to_dict(msg)

    async def get_recent_history(
        self, conversation_id: str, limit: int | None = None
    ) -> list[ChatTurn]:
        max_messages = limit or settings.CHAT_HISTORY_MAX_MESSAGES
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(max_messages)
            )
            messages = list(reversed(rows.scalars().all()))
            return [
                ChatTurn(role=m.role, content=m.content)
                for m in messages
                if m.content and m.role in ("user", "assistant")
            ]

    async def ensure_conversation(
        self, conversation_id: str | None, kb_id: str
    ) -> dict | None:
        """The KB's live conversation for ``conversation_id`` (None if unknown,
        deleted or another KB's); a new one when no id is given."""
        if conversation_id:
            return await self.get_conversation(conversation_id, kb_id=kb_id)
        return await self.create_conversation(kb_id)

    async def maybe_set_title_from_first_message(
        self, conversation_id: str, user_text: str
    ) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            )
            conv = row.scalar_one_or_none()
            if not conv or conv.title not in (DEFAULT_TITLE, "", None):
                return
            snippet = " ".join(user_text.strip().split())
            if len(snippet) > 72:
                snippet = snippet[:69].rstrip() + "..."
            if snippet:
                conv.title = snippet
                conv.updated_at = datetime.now(timezone.utc)
                await session.commit()


chat_store = ChatStore()
