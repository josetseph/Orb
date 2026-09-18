"""Chat endpoint: one question in, one answer out."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import get_kb
from app.core.log import get_logger
from app.schemas.chat import ChatInput
from app.services.ai_gate import require_ai
from app.services.kb_registry import KBContext

logger = get_logger("API")
router = APIRouter()


@router.post("/api/v1/chat")
async def chat(body: ChatInput, kb: KBContext = Depends(get_kb)):
    """Chat: retrieval → rerank → synthesis. No conversation history is kept."""
    require_ai(kb)
    request_id = body.request_id or str(uuid.uuid4())

    def _progress(stage: str, model: str | None = None) -> None:
        logger.info("[Chat %s] %s%s", request_id[:8], stage, f" ({model})" if model else "")

    result = await kb.get_chat_workflow().chat(
        body.query, history=[], progress_callback=_progress
    )
    result["request_id"] = request_id
    return result
