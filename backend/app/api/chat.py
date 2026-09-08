"""Chat conversation and async job endpoints."""

from __future__ import annotations

import asyncio
import uuid

from collections import OrderedDict
import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_kb
from app.core.log import get_logger
from app.schemas.chat import ChatInput, CreateConversationInput
from app.services.ai_gate import require_ai
from app.services.chat_store import chat_store
from app.services.firefly_service import firefly_service
from app.services.kb_registry import KBContext

logger = get_logger("API")
router = APIRouter()

class _BoundedChatStatus:
    """Bounded, TTL-expiring store for chat job status.

    Prevents memory leaks from indefinite retention of full query/result payloads.
    """

    def __init__(self, max_size: int = 200, ttl_seconds: float = 1800.0):
        self._statuses: OrderedDict[str, dict] = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

    def _cleanup(self, now: float) -> None:
        expired = [
            req_id
            for req_id, ts in self._timestamps.items()
            if now - ts > self._ttl_seconds
        ]
        for req_id in expired:
            self._statuses.pop(req_id, None)
            self._timestamps.pop(req_id, None)

    def _evict_oldest_if_needed(self) -> None:
        while len(self._statuses) > self._max_size:
            oldest_id, _ = self._statuses.popitem(last=False)
            self._timestamps.pop(oldest_id, None)

    def __setitem__(self, key: str, value: dict) -> None:
        now = time.time()
        self._cleanup(now)
        self._statuses[key] = value
        self._statuses.move_to_end(key)
        self._timestamps[key] = now
        self._evict_oldest_if_needed()

    def __getitem__(self, key: str) -> dict:
        now = time.time()
        self._cleanup(now)
        return self._statuses[key]

    def get(self, key: str, default: dict | None = None) -> dict:
        now = time.time()
        self._cleanup(now)
        return self._statuses.get(key, default if default is not None else {})

    def __contains__(self, key: str) -> bool:
        now = time.time()
        self._cleanup(now)
        return key in self._statuses

    def __len__(self) -> int:
        return len(self._statuses)


_chat_status = _BoundedChatStatus()
_chat_job_lock = asyncio.Lock()
_chat_tasks: set[asyncio.Task] = set()


async def _answer_chat_query(
    query: str,
    kb: KBContext,
    history_turns: list,
    progress_callback,
) -> dict:
    """Run finance-aware chat or standard retrieval chat."""
    if kb.finance_enabled and firefly_service.looks_like_finance_query(query):
        progress_callback("Checking finance data and notes")
        note_ctx = await kb.get_chat_workflow().retrieve_for_query(
            query,
            history=history_turns,
            progress_callback=progress_callback,
        )
        result = await firefly_service.answer_finance_question(
            query,
            kb,
            note_docs=note_ctx.get("context") or [],
            rewritten_query=note_ctx.get("rewritten_query"),
        )
        if note_ctx.get("thinking"):
            result["thinking"] = note_ctx.get("thinking")
        return result
    return await kb.get_chat_workflow().chat(
        query,
        history=history_turns,
        progress_callback=progress_callback,
    )


@router.get("/api/v1/chat/conversations")
async def list_chat_conversations(kb: KBContext = Depends(get_kb)):
    """List saved chat conversations for the active knowledge base."""
    return await chat_store.list_conversations(kb.kb_id)


@router.post("/api/v1/chat/conversations")
async def create_chat_conversation(
    body: CreateConversationInput | None = None,
    kb: KBContext = Depends(get_kb),
):
    """Create an empty chat conversation."""
    title = body.title if body else None
    return await chat_store.create_conversation(kb.kb_id, title=title)


@router.get("/api/v1/chat/conversations/{conversation_id}/messages")
async def get_chat_messages(
    conversation_id: str, kb: KBContext = Depends(get_kb)
):
    """Return all messages for a conversation in the active KB."""
    conv = await chat_store.get_conversation(conversation_id, kb_id=kb.kb_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await chat_store.list_messages(conversation_id, kb_id=kb.kb_id)


@router.delete("/api/v1/chat/conversations/{conversation_id}")
async def delete_chat_conversation(
    conversation_id: str, kb: KBContext = Depends(get_kb)
):
    """Soft-delete a chat conversation in the active KB."""
    deleted = await chat_store.delete_conversation(conversation_id, kb_id=kb.kb_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/api/v1/chat")
async def chat(body: ChatInput, kb: KBContext = Depends(get_kb)):
    """Chat: retrieval → rerank → synthesis (or finance path when query matches)."""
    require_ai(kb)

    request_id = body.request_id or str(uuid.uuid4())
    conversation = await chat_store.ensure_conversation(
        body.conversation_id, kb.kb_id
    )
    conversation_id = conversation["id"]
    history_turns = await chat_store.get_recent_history(conversation_id)

    def _progress(stage: str, model: str | None = None) -> None:
        _chat_status[request_id] = {"stage": stage, "model": model}

    _progress("Starting chat request")
    try:
        await chat_store.add_message(conversation_id, "user", body.query)
        await chat_store.maybe_set_title_from_first_message(conversation_id, body.query)
        result = await _answer_chat_query(
            body.query, kb, history_turns, _progress
        )
        assistant = await chat_store.add_message(
            conversation_id,
            "assistant",
            result.get("answer", ""),
            thinking=result.get("thinking"),
            metadata={
                "rewritten_query": result.get("rewritten_query"),
                "context_count": len(result.get("context") or []),
            },
        )
        _progress("Complete")
        result["request_id"] = request_id
        result["conversation_id"] = conversation_id
        result["assistant_message_id"] = assistant["id"]
        return result
    except Exception:
        _progress("Failed")
        raise


async def _run_chat_job(
    request_id: str,
    query: str,
    kb: KBContext,
    conversation_id: str,
    history: list[dict],
) -> None:
    """Run a long chat request after the browser has received a request id."""
    from app.schemas.chat import ChatTurn

    history_turns = [ChatTurn(**t) for t in history]

    def _progress(stage: str, model: str | None = None) -> None:
        current = _chat_status.get(request_id, {})
        _chat_status[request_id] = {
            **current,
            "stage": stage,
            "model": model,
            "done": False,
            "conversation_id": conversation_id,
        }

    _progress("Starting chat request")
    try:
        result = await _answer_chat_query(query, kb, history_turns, _progress)
        assistant = await chat_store.add_message(
            conversation_id,
            "assistant",
            result.get("answer", ""),
            thinking=result.get("thinking"),
            metadata={
                "rewritten_query": result.get("rewritten_query"),
                "context_count": len(result.get("context") or []),
            },
        )
        result["request_id"] = request_id
        result["conversation_id"] = conversation_id
        result["assistant_message_id"] = assistant["id"]
        _chat_status[request_id] = {
            "stage": "Complete",
            "model": None,
            "done": True,
            "conversation_id": conversation_id,
            "result": result,
        }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("[Chat] Async chat job failed")
        _chat_status[request_id] = {
            "stage": "Failed",
            "model": None,
            "done": True,
            "conversation_id": conversation_id,
            "error": str(exc) or exc.__class__.__name__,
        }


async def _run_chat_job_serialized(
    request_id: str,
    query: str,
    kb: KBContext,
    conversation_id: str,
    history: list[dict],
) -> None:
    """Run chat on the app event loop (same loop as AsyncSessionLocal)."""
    if _chat_job_lock.locked():
        current = _chat_status.get(request_id, {})
        _chat_status[request_id] = {
            **current,
            "stage": "Waiting for current chat to finish",
            "model": None,
            "done": False,
            "conversation_id": conversation_id,
        }
    async with _chat_job_lock:
        await _run_chat_job(request_id, query, kb, conversation_id, history)


@router.post("/api/v1/chat/async")
async def start_chat(
    body: ChatInput,
    kb: KBContext = Depends(get_kb),
):
    """Start a chat request and return immediately for polling clients."""
    require_ai(kb)
    request_id = body.request_id or str(uuid.uuid4())
    conversation = await chat_store.ensure_conversation(
        body.conversation_id, kb.kb_id
    )
    conversation_id = conversation["id"]
    history = await chat_store.get_recent_history(conversation_id)
    history_payload = [{"role": t.role, "content": t.content} for t in history]

    await chat_store.add_message(conversation_id, "user", body.query)
    await chat_store.maybe_set_title_from_first_message(conversation_id, body.query)

    _chat_status[request_id] = {
        "stage": "Queued",
        "model": None,
        "done": False,
        "conversation_id": conversation_id,
    }
    task = asyncio.create_task(
        _run_chat_job_serialized(
            request_id,
            body.query,
            kb,
            conversation_id,
            history_payload,
        )
    )
    _chat_tasks.add(task)
    task.add_done_callback(_chat_tasks.discard)
    return {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "stage": "Queued",
        "model": None,
        "done": False,
    }


@router.get("/api/v1/chat/status/{request_id}")
async def get_chat_status(request_id: str):
    """Return current progress for a non-streaming chat request."""
    return {
        "request_id": request_id,
        **_chat_status.get(
            request_id, {"stage": "Waiting", "model": None, "done": False}
        ),
    }
