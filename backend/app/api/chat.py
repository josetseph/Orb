"""Chat conversation and async job endpoints."""

from __future__ import annotations

import asyncio
import uuid

from collections import OrderedDict
import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_kb
from app.core.database import AsyncSessionLocal
from app.core.log import get_logger
from app.schemas.chat import ChatInput, CreateConversationInput
from app.services import jobs
from app.services.ai_gate import require_ai
from app.services.chat_store import chat_store
from app.services.firefly_service import firefly_service
from app.services.kb_registry import KBContext

logger = get_logger("API")
router = APIRouter()

# request_id -> (stamped_at, status). Re-stamped on every write, so insertion
# order is age order and pruning only ever pops from the front.
_chat_status: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _prune() -> None:
    now = time.time()
    while _chat_status and (
        len(_chat_status) > 200 or now - next(iter(_chat_status.values()))[0] > 1800
    ):
        _chat_status.popitem(last=False)


def _set_status(request_id: str, value: dict) -> None:
    _chat_status[request_id] = (time.time(), value)
    _chat_status.move_to_end(request_id)
    _prune()


def _get_status(request_id: str, default: dict) -> dict:
    _prune()
    return _chat_status[request_id][1] if request_id in _chat_status else default
_chat_job_lock = asyncio.Lock()


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


async def _run_chat_job(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request_id: str,
    query: str,
    kb: KBContext,
    conversation_id: str,
    history: list[dict],
    resumed: bool = False,
) -> None:
    """Run a long chat request after the browser has received a request id."""
    from app.schemas.chat import ChatTurn

    history_turns = [ChatTurn(**t) for t in history]

    def _progress(stage: str, model: str | None = None) -> None:
        current = _get_status(request_id, {})
        _set_status(request_id, {
            **current,
            "stage": stage,
            "model": model,
            "done": False,
            "conversation_id": conversation_id,
        })

    _progress("Resuming answer" if resumed else "Starting chat request")
    if resumed and await chat_store.has_reply(conversation_id, request_id):
        return  # the reply was saved just before the app stopped
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
                "sources": result.get("sources") or [],
                "request_id": request_id,
            },
        )
        result["request_id"] = request_id
        result["conversation_id"] = conversation_id
        result["assistant_message_id"] = assistant["id"]
        _set_status(request_id, {
            "stage": "Complete",
            "model": None,
            "done": True,
            "conversation_id": conversation_id,
            "result": result,
        })
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("[Chat] Async chat job failed")
        _set_status(request_id, {
            "stage": "Failed",
            "model": None,
            "done": True,
            "conversation_id": conversation_id,
            "error": str(exc) or exc.__class__.__name__,
        })
        raise  # the job records the failure


async def _run_chat_job_serialized(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request_id: str,
    query: str,
    kb: KBContext,
    conversation_id: str,
    history: list[dict],
    resumed: bool = False,
) -> None:
    """Run chat on the app event loop (same loop as AsyncSessionLocal)."""
    if _chat_job_lock.locked():
        current = _get_status(request_id, {})
        _set_status(request_id, {
            **current,
            "stage": "Waiting for current chat to finish",
            "model": None,
            "done": False,
            "conversation_id": conversation_id,
        })
    async with _chat_job_lock:
        await _run_chat_job(request_id, query, kb, conversation_id, history, resumed)
        # Messages that just left the history window get folded into the
        # running summary. After the answer is out, so nobody waits on it.
        try:
            await chat_store.refresh_summary(conversation_id, kb.llm.summarize_conversation)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[Chat] Summary refresh failed: %s", exc)


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
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation_id = conversation["id"]
    history = await chat_store.get_recent_history(conversation_id)
    history_payload = [{"role": t.role, "content": t.content} for t in history]

    await chat_store.add_message(conversation_id, "user", body.query)
    await chat_store.maybe_set_title_from_first_message(conversation_id, body.query)

    _set_status(request_id, {
        "stage": "Queued",
        "model": None,
        "done": False,
        "conversation_id": conversation_id,
    })
    # Written down before any retrieval: a quit or crash mid-answer resumes it.
    async with AsyncSessionLocal() as session:
        await jobs.enqueue(
            session,
            jobs.ANSWER,
            kb.kb_id,
            request_id,
            jobs.input_hash(conversation_id, body.query),
            payload={"conversation_id": conversation_id, "query": body.query, "history": history_payload},
        )
        await session.commit()
    jobs.wake()
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
    if request_id not in _chat_status:
        # Not run by this process (it was restarted): the job row says where it stands.
        job = await jobs.get(jobs.ANSWER, request_id)
        if job is not None:
            cid = (job.payload or {}).get("conversation_id")
            if job.state in jobs.ACTIVE:
                status = {"stage": "Resuming answer", "model": None, "done": False, "conversation_id": cid}
            elif job.state == "done":
                status = {"stage": "Complete", "model": None, "done": True, "conversation_id": cid,
                          "result": {"conversation_id": cid}}
            else:
                status = {"stage": "Failed", "model": None, "done": True, "conversation_id": cid,
                          "error": job.last_error or job.state}
            return {"request_id": request_id, **status}
    return {
        "request_id": request_id,
        **_get_status(request_id, {"stage": "Waiting", "model": None, "done": False}),
    }


@router.get("/api/v1/chat/conversations/{conversation_id}/pending")
async def get_pending_answer(conversation_id: str, kb: KBContext = Depends(get_kb)):
    """The answer still being written in this conversation, if any — to follow after a reopen."""
    job = await jobs.active_for(jobs.ANSWER, kb.kb_id, "conversation_id", conversation_id)
    return {"request_id": job.target_id if job else None}


def prepare_answer(job):
    """The job worker's entry for an answer: its model calls are saved as it goes, so a
    resume replays the finished agent-loop steps instead of paying for them again. If the
    notes changed meanwhile the prompts differ, nothing replays, and it simply starts over."""
    from app.services import ingestion_checkpoint
    from app.services.kb_registry import kb_registry

    kb = kb_registry.get_kb(job.kb_id)
    payload = job.payload or {}
    if kb is None or not payload.get("query"):
        return None
    key = (job.kb_id, jobs.memo_key(job.kind, job.target_id))

    async def run() -> str:
        ingestion_checkpoint.activate(*key)
        try:
            await _run_chat_job_serialized(
                job.target_id,
                payload["query"],
                kb,
                payload["conversation_id"],
                payload.get("history") or [],
                resumed=job.attempts > 1,
            )
        except asyncio.CancelledError:
            raise  # shutdown: the saved calls are what the resume replays
        except BaseException:
            ingestion_checkpoint.clear(*key)  # a retry is a new question
            raise
        ingestion_checkpoint.clear(*key)
        return "done"

    return run
