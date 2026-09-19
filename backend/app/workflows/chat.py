"""Research-loop chat workflow: iterative retrieval and attribution."""

# pylint: disable=wrong-import-order
import time
from collections.abc import Callable

from app.core.database import AsyncSessionLocal
from app.core.log import get_logger
from app.models.note import Note
from app.schemas.chat import ChatSource, ChatTurn
from app.services.llm import llm_service
from app.services.local_models import ModelLoadClock, model_load_clock
from app.services.retrieval import RetrievalService, retrieval_service
from sqlalchemy import select

logger = get_logger("ChatWorkflow")


def _log_timing(kind: str, total: float, load_before: dict, docs: int) -> None:
    """Split wall time into model loads vs. inference — a slow disk and a slow model look alike otherwise."""
    delta = ModelLoadClock.diff(load_before, model_load_clock.snapshot())
    load = delta["total_seconds"]
    logger.info(
        "[Timing] %s total=%.1fs model_load=%.1fs inference=%.1fs loads=%s docs=%d",
        kind, total, load, max(0.0, total - load), ModelLoadClock.describe(delta), docs,
    )


def _doc_passage(doc: dict) -> str:
    """Extract the cleanest available text from a retrieved doc for LLM prompts."""
    node = doc.get("original_obj", {})
    text = (
        node.get("summary") or node.get("description") or doc.get("text", "")
    ).strip()
    return text


def _dedupe_docs(docs: list[dict]) -> list[dict]:
    """Deduplicate retrieved docs by node name, note id, or text."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for doc in docs:
        doc_id = (
            doc.get("original_obj", {}).get("name")
            or doc.get("note_id")
            or doc.get("text", "")
        )
        if doc_id and doc_id not in seen:
            deduped.append(doc)
            seen.add(doc_id)
    return deduped


def _truncate_context(docs: list[dict], max_docs: int) -> list[dict]:
    """Keep the highest-confidence docs; clear unverified linked_notes."""
    if len(docs) > max_docs:
        docs = sorted(
            docs,
            key=lambda d: d.get("rerank_score", 0.0),
            reverse=True,
        )[:max_docs]
    for doc in docs:
        if "rerank_score" not in doc:
            doc["linked_notes"] = []
    return docs


class ChatWorkflow:  # pylint: disable=too-few-public-methods
    """Iterative research-loop workflow: retrieve, synthesise, and attribute sources."""

    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        llm=None,
    ) -> None:
        self._retrieval = retrieval or retrieval_service
        # Per-KB LLM (chat model override) or the global service.
        self._llm = llm or llm_service

    async def _retrieve_context(
        self,
        user_query: str,
        history: list[ChatTurn] | None,
        progress_callback: Callable[[str, str | None], None] | None,
        max_context_docs: int,
    ) -> tuple[str, str, list[dict], str]:
        """Rewrite → research-loop retrieve → dedupe/truncate.

        Returns ``(rewritten_query, final_answer, unique_docs, thinking)``.
        """
        history = history or []
        history_payload = [{"role": t.role, "content": t.content} for t in history]
        rewritten_query = self._llm.rewrite_follow_up_query(
            history_payload, user_query
        )

        def _progress(stage: str, model: str | None = None) -> None:
            if progress_callback:
                progress_callback(stage, model)

        _progress("Planning retrieval", "Gemma4")
        final_answer, all_docs, thinking = (
            await self._retrieval.retrieve_with_iterative_loop(
                rewritten_query,
                top_k=50,
                progress_callback=progress_callback,
                conversation_history=history_payload,
            )
        )
        _progress("Selecting best evidence")
        unique_docs = _truncate_context(_dedupe_docs(all_docs), max_context_docs)
        return rewritten_query, final_answer or "", unique_docs, thinking

    async def chat(
        self,
        user_query: str,
        history: list[ChatTurn] | None = None,
        progress_callback: Callable[[str, str | None], None] | None = None,
    ) -> dict:
        """Research-style retrieval loop with final answer + note references."""
        start_time = time.perf_counter()
        load_before = model_load_clock.snapshot()
        logger.info(f"\n[Chat] Started processing query: '{user_query}'")
        rewritten_query, final_answer, unique_docs, thinking = (
            await self._retrieve_context(
                user_query, history, progress_callback, max_context_docs=6
            )
        )
        if rewritten_query != user_query:
            logger.info(f"[Chat] Rewritten query: '{rewritten_query}'")
        logger.info(f"[Chat] Unique docs: {len(unique_docs)}")
        if unique_docs:
            logger.debug(
                "Context (%d docs): %s",
                len(unique_docs),
                [
                    f"{i+1}. [{doc.get('original_obj', {}).get('name', '?')}] "
                    f"{_doc_passage(doc)}"
                    for i, doc in enumerate(unique_docs)
                ],
            )

        # Only valid answer synthesis is the pipeline's structured final answer.
        answer = final_answer
        if answer:
            logger.info(
                f"[Chat] Using pipeline answer directly (structured synthesis): "
                f"'{answer}'"
            )
        else:
            answer = (
                "I couldn't find any relevant information in the knowledge base "
                "to answer that."
                if not unique_docs
                else "I couldn't find enough information to answer that."
            )
            logger.info("[Chat] Iterative loop exhausted — no answer produced.")

        sources = await self._extract_references(unique_docs)
        if progress_callback:
            progress_callback("Formatting answer", None)

        total = time.perf_counter() - start_time
        logger.info(f"[Chat] Total pipeline duration: {total:.2f}s\n")
        _log_timing("chat", total, load_before, len(unique_docs))
        return {
            "query": user_query,
            "rewritten_query": rewritten_query,
            "answer": answer,
            "sources": [s.model_dump() for s in sources],
            "context": unique_docs,
            "thinking": thinking,
        }

    async def retrieve_for_query(
        self,
        user_query: str,
        history: list[ChatTurn] | None = None,
        progress_callback: Callable[[str, str | None], None] | None = None,
    ) -> dict:
        """Retrieve note context without synthesizing a final answer."""
        start_time = time.perf_counter()
        load_before = model_load_clock.snapshot()
        rewritten_query, _final_answer, unique_docs, thinking = (
            await self._retrieve_context(
                user_query, history, progress_callback, max_context_docs=12
            )
        )
        _log_timing("retrieve", time.perf_counter() - start_time, load_before, len(unique_docs))
        return {
            "query": user_query,
            "rewritten_query": rewritten_query,
            "context": unique_docs,
            "thinking": thinking,
        }

    async def _extract_references(self, docs: list) -> list[ChatSource]:
        """Unique note sources, preferring SQLite titles over graph titles."""
        seen_refs: set[str] = set()
        id_to_title: dict[str, str | None] = {}

        for d in docs:
            for linked_note in d.get("linked_notes", []):
                lnid = linked_note.get("id")
                if lnid:
                    id_to_title.setdefault(lnid, linked_note.get("title"))

        if id_to_title:
            try:
                async with AsyncSessionLocal() as session:
                    rows = await session.execute(
                        select(Note.id, Note.title).where(Note.id.in_(id_to_title))
                    )
                    for row_id, row_title in rows:
                        if row_title:
                            id_to_title[row_id] = row_title
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug(f"[Chat] Note title lookup failed: {e}")

        references: list[ChatSource] = []
        for d in docs:
            for linked_note in d.get("linked_notes", []):
                lnid = linked_note.get("id")
                if lnid and lnid not in seen_refs:
                    ltitle = id_to_title.get(lnid) or "Untitled Note"
                    references.append(ChatSource(id=lnid, title=ltitle))
                    seen_refs.add(lnid)

        logger.info(f"[Chat] Found {len(references)} references for response")
        return references
