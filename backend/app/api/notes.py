"""Notes CRUD, ingest, and batch-delete endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_kb
from app.core.database import get_db
from app.core.log import get_logger
from app.models.note import Note
from app.models.wikilink import NoteLink
from app.schemas.extraction import NoteInput
from pydantic import BaseModel
from app.schemas.note import BatchDeleteNotesInput, CreateNoteInput, MoveNoteInput
from app.services.ai_gate import require_ai
from app.services.kb_registry import KBContext
from app.services.local_storage import remove_upload, vault_rel_from_url
from app.services.note_files import note_body, persist_note_body
from app.services.vault import delete_note_file
from app.services.wikilinks import refresh_note_links

logger = get_logger("API")
router = APIRouter()


def _parse_date_str(s: str) -> datetime:
    """Parse a date string, trying ISO format first then dateparser.

    Always returns a timezone-aware datetime. Falls back to ``datetime.now(UTC)``
    when every parse attempt fails so callers never receive a bare None.
    """
    from dateutil import parser as dateutil_parser

    try:
        dt = dateutil_parser.isoparse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    try:
        import dateparser

        dt = dateparser.parse(s)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return datetime.now(timezone.utc)


def _note_response(note: Note, kb: KBContext) -> dict:
    """Serialize note with vault-backed content."""
    return {
        "id": note.id,
        "content": note_body(note, kb),
        "title": note.title,
        "rel_path": note.rel_path,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "processed": note.processed,
        "failed": note.failed,
        "processing_stage": note.processing_stage,
        "processing_model": note.processing_model,
        "kb_id": note.kb_id,
    }


@router.post("/api/v1/notes")
async def create_note(
    note_input: CreateNoteInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Create a note as a vault .md file + metadata row (no ingest)."""
    note_id = str(uuid.uuid4())
    c_at = (
        _parse_date_str(note_input.created_at)
        if note_input.created_at
        else datetime.now(timezone.utc)
    )

    new_note = Note(
        id=note_id,
        content="",
        created_at=c_at,
        processed=False,
        processing_stage="Saved",
        title=(note_input.title or "").strip() or None,
        kb_id=kb.kb_id,
    )
    persist_note_body(
        new_note,
        kb,
        note_input.content or "",
        title=(note_input.title or "").strip() or None,
        folder=(note_input.folder or "").strip() or None,
    )
    db.add(new_note)
    await db.flush()
    await refresh_note_links(db, kb.kb_id, note_id, note_input.content or "")
    await db.commit()
    await db.refresh(new_note)

    return _note_response(new_note, kb)


@router.post("/api/v1/notes/{note_id}/move")
async def move_note(
    note_id: str,
    body: MoveNoteInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Move a note into a vault folder (empty folder = vault root)."""
    from app.services.vault_ops import move_note_to_folder

    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note or note.kb_id != kb.kb_id:
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        moved = await move_note_to_folder(db, kb, note, body.folder or "")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.refresh(note)
    return {**moved, "note": _note_response(note, kb)}


@router.post("/api/v1/notes/{note_id}/dismiss-failure")
async def dismiss_note_failure(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Clear a failed ingestion flag without re-running the pipeline.

    A note that will never be ingested should not keep showing as failed.
    Leaves processed alone — the note is simply not ingested, which is a
    legitimate state, not an error.
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    note.failed = False
    note.processing_stage = "Saved"
    note.processing_model = None
    await db.commit()
    return {"id": note_id, "failed": False}


@router.post("/api/v1/notes/{note_id}/ingest")
async def ingest_existing_note(
    note_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Trigger (or re-trigger) ingestion for an existing note.
    Always force-reingests — resets processed/failed flags so the pipeline runs
    regardless of prior ingestion status.
    """
    require_ai(kb)
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Reset flags so the pipeline treats this as a fresh ingestion.
    note.processed = False
    note.failed = False
    note.processing_stage = "Queued for ingestion"
    note.processing_model = None
    await db.commit()

    note_data = NoteInput(
        content=note_body(note, kb),
        created_at=note.created_at.isoformat() if note.created_at else None,
        title=(note.title or "").strip() or None,
    )

    background_tasks.add_task(
        kb.get_ingestion_workflow().process_note, note_data, note_id
    )

    return {
        "note_id": note_id,
        "status": "processing_started",
        "message": "Note ingestion has been queued",
    }


@router.post("/api/v1/notes/{note_id}/ingest/cancel")
async def cancel_note_ingestion(note_id: str, kb: KBContext = Depends(get_kb)):
    """Stop this note's ingestion; the note goes back to plain "Saved"."""
    from app.workflows.ingestion import cancel_ingestion

    stopped = cancel_ingestion(kb.kb_id, note_id)
    return {"note_id": note_id, "status": "cancelling" if stopped else "not_running"}


class ProcessAttachmentInput(BaseModel):
    """One attachment to run through its extractor, outside a full ingest."""

    url: str
    force: bool = False


# ponytail: unbounded per-process dict; entries are tiny and a restart clears
# it. Move to SQLite if a per-note history ever matters.
_attachment_jobs: dict[tuple[str, str, str], dict] = {}


async def _run_attachment_job(kb: KBContext, note_id: str, url: str) -> None:
    """Extract one attachment, replace its block in the vault .md, unload models."""
    from app.core.database import AsyncSessionLocal
    from app.workflows.agents.ingestion_agent import (
        classify_attachment,
        extract_attachment,
        multimedia_concurrency_limit,
        parse_attachments,
        place_extraction,
        remove_extraction,
    )
    from app.services.multimedia import multimedia_service

    key = (kb.kb_id, note_id, url)
    wf = kb.get_ingestion_workflow()

    async def _noop_status(_stage: str, _model: str | None = None) -> None:
        return None

    kind = None
    try:
        async with multimedia_concurrency_limit:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
                )
                note = result.scalar_one_or_none()
                if not note:
                    raise ValueError("Note not found")
                body = note_body(note, kb)
            from app.workflows.agents.ingestion_agent import attachment_key

            item = next(
                (a for a in parse_attachments(body) if a["lower_url"] == attachment_key(url)),
                None,
            )
            if not item:
                raise ValueError("Attachment is no longer linked from this note")
            kind = classify_attachment(item)
            if not kind:
                raise ValueError(f"Orb cannot read {item['filename']}")
            section = await extract_attachment(kind, item, _noop_status, wf._llm)  # pylint: disable=protected-access
            if not section.strip():
                raise ValueError("Extraction produced no text")
            content = place_extraction(remove_extraction(body, url), item["url"], section)
            await wf._persist_note_body(note_id, content)  # pylint: disable=protected-access
        _attachment_jobs[key] = {"status": "done", "error": None}
    except asyncio.CancelledError:
        # The model call already in a thread runs to completion; its output is dropped.
        logger.info(f"Attachment processing cancelled for {url}")
        _attachment_jobs[key] = {"status": "cancelled", "error": None}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"Attachment processing failed for {url}: {exc}")
        _attachment_jobs[key] = {"status": "failed", "error": str(exc)}
    finally:
        try:
            if kind in ("audio", "video"):
                await asyncio.to_thread(multimedia_service.unload_local_models, "asr")
            if kind == "video":
                await asyncio.to_thread(multimedia_service.unload_marlin)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Model unload after attachment job failed: {exc}")


@router.post("/api/v1/notes/{note_id}/attachments/process")
async def process_note_attachment(
    note_id: str,
    payload: ProcessAttachmentInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Transcribe / describe / extract one attachment without a full ingest.

    Ingestion skips attachments that already carry a block, so this is how a
    recording gets transcribed once and never again.
    """
    from app.workflows.agents.ingestion_agent import (
        attachment_key,
        extraction_srcs,
        parse_attachments,
    )

    require_ai(kb)
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    body = note_body(note, kb)
    key = attachment_key(payload.url)
    if not any(a["lower_url"] == key for a in parse_attachments(body)):
        raise HTTPException(status_code=404, detail="Attachment not found in this note")
    if key in extraction_srcs(body) and not payload.force:
        return {"note_id": note_id, "url": payload.url, "status": "already_processed"}
    job_key = (kb.kb_id, note_id, payload.url)
    if _attachment_jobs.get(job_key, {}).get("status") == "running":
        return {"note_id": note_id, "url": payload.url, "status": "running"}
    # A real task (not a BackgroundTask) so it can be cancelled by url.
    _attachment_jobs[job_key] = {
        "status": "running",
        "error": None,
        "task": asyncio.create_task(_run_attachment_job(kb, note_id, payload.url)),
    }
    return {"note_id": note_id, "url": payload.url, "status": "running"}


@router.post("/api/v1/notes/{note_id}/attachments/cancel")
async def cancel_note_attachment(
    note_id: str, payload: ProcessAttachmentInput, kb: KBContext = Depends(get_kb)
):
    """Stop one attachment's transcribe / describe / extract job."""
    job = _attachment_jobs.get((kb.kb_id, note_id, payload.url))
    task = job.get("task") if job else None
    if job is None or job.get("status") != "running" or task is None or task.done():
        return {"note_id": note_id, "url": payload.url, "status": "not_running"}
    task.cancel()
    return {"note_id": note_id, "url": payload.url, "status": "cancelling"}


@router.get("/api/v1/notes/{note_id}/attachments/jobs")
async def note_attachment_jobs(note_id: str, kb: KBContext = Depends(get_kb)):
    """Status of every per-attachment job started for this note this session."""
    return {
        "jobs": {
            url: {"status": job["status"], "error": job.get("error")}
            for (kb_id, nid, url), job in _attachment_jobs.items()
            if kb_id == kb.kb_id and nid == note_id
        }
    }


@router.post("/api/v1/ingest")
async def ingest_note(
    note_data: NoteInput,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Create and ingest a new note (legacy combined endpoint for batch scripts).
    For manual note creation, prefer POST /api/v1/notes then POST /api/v1/notes/{id}/ingest.
    """
    if not note_data.skip_ingestion:
        require_ai(kb)
    note_id = str(uuid.uuid4())
    c_at = (
        _parse_date_str(note_data.created_at)
        if note_data.created_at
        else datetime.now(timezone.utc)
    )

    new_note = Note(
        id=note_id,
        content="",
        created_at=c_at,
        processed=False,
        processing_stage=(
            "Queued for ingestion" if not note_data.skip_ingestion else "Saved"
        ),
        kb_id=kb.kb_id,
    )
    persist_note_body(new_note, kb, note_data.content or "")
    db.add(new_note)
    await db.flush()
    await refresh_note_links(db, kb.kb_id, note_id, note_data.content or "")
    await db.commit()

    if not note_data.skip_ingestion:
        if note_data.created_at is None:
            note_data.created_at = c_at.isoformat()
        background_tasks.add_task(
            kb.get_ingestion_workflow().process_note, note_data, note_id
        )
        status = "processing_started"
    else:
        status = "saved_without_ingestion"

    return {
        "note_id": note_id,
        "status": status,
        "content": note_data.content,
        "created_at": c_at.isoformat(),
        "processed": False,
    }


@router.get("/api/v1/notes")
async def get_notes(
    search: str | None = None,
    processed: bool | None = None,
    failed: bool | None = None,
    sync_vault: bool = True,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Get notes for the active KB, sorted by creation date (newest first).
    Optionally filter by processed/failed status.
    """
    if sync_vault:
        from app.services.vault_sync import sync_vault_notes

        try:
            await sync_vault_notes(db, kb)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[get_notes] vault sync skipped: {exc}")

    base_query = select(Note)

    filters = [Note.kb_id == kb.kb_id]
    if search:
        term = f"%{search}%"
        # Bodies live in vault files — search title + rel_path metadata only
        filters.append((Note.title.ilike(term)) | (Note.rel_path.ilike(term)))

    if processed is not None:
        filters.append(Note.processed == processed)

    if failed is not None:
        filters.append(Note.failed == failed)

    base_query = base_query.where(*filters)
    query = base_query.order_by(Note.created_at.desc())
    result = await db.execute(query)
    notes = result.scalars().all()
    # _note_response reads each note body from the vault (one file read per
    # note) — run the whole batch off the event loop so a large vault on
    # OneDrive/NAS doesn't stall every other request.
    return await asyncio.to_thread(lambda: [_note_response(n, kb) for n in notes])


@router.get("/api/v1/notes/{note_id}")
async def get_note(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Get a specific note by ID with vault-backed content."""
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return _note_response(note, kb)


@router.get("/api/v1/notes/{note_id}/status")
async def get_note_ingestion_status(
    note_id: str,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Return the ingestion status of a note without fetching its full content.
    Useful for polling after triggering background ingestion.

    Returns:
      - processed: true once ingestion completes successfully
      - failed: true if the ingestion pipeline encountered a permanent error
      - status: "completed" | "failed" | "processing"
      - processing_stage: user-facing current stage
      - processing_model: in-process model name currently in use, if any
    """
    try:
        result = await db.execute(
            select(
                Note.id,
                Note.processed,
                Note.failed,
                Note.processing_stage,
                Note.processing_model,
            ).where(Note.id == note_id, Note.kb_id == kb.kb_id)
        )
        row = result.one_or_none()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="Database temporarily unavailable, retry shortly"
        ) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")

    note_id, processed, failed, processing_stage, processing_model = row
    if processed:
        status = "completed"
    elif failed:
        status = "failed"
    else:
        status = "processing"

    return {
        "id": note_id,
        "processed": processed,
        "failed": failed,
        "status": status,
        "processing_stage": processing_stage,
        "processing_model": processing_model,
    }


@router.put("/api/v1/notes/{note_id}")
async def update_note(
    note_id: str,
    note_input: CreateNoteInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Update an existing note's vault file content.
    Does NOT trigger re-ingestion or change processed status.
    Use POST /api/v1/notes/{id}/ingest to re-ingest after updating.
    """
    result = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    existing_note = result.scalar_one_or_none()
    if not existing_note:
        raise HTTPException(status_code=404, detail="Note not found")

    persist_note_body(
        existing_note,
        kb,
        note_input.content or "",
        title=(note_input.title or "").strip() or None,
    )

    # Retitling renames the vault .md to match (Obsidian-style), rewriting
    # markdown refs and wikilinks that pointed at the old filename.
    if note_input.title is not None:
        from app.services.vault_ops import rename_note_file_for_title

        await rename_note_file_for_title(db, kb, existing_note, note_input.title)

    if note_input.created_at:
        existing_note.created_at = _parse_date_str(note_input.created_at)

    existing_note.updated_at = datetime.now(timezone.utc)
    # Autosave must never start ingestion. Clear watcher false-positives while
    # leaving a user-queued ingest stage alone.
    stage = existing_note.processing_stage or ""
    if not existing_note.processed and not (
        stage.startswith("Queued") or stage.startswith("Starting")
    ):
        if (
            "pending" in stage.lower()
            or stage.startswith("External")
            or stage.startswith("Changed on disk")
            or not stage
        ):
            existing_note.processing_stage = "Saved"

    await refresh_note_links(db, kb.kb_id, note_id, note_input.content or "")

    await db.commit()
    await db.refresh(existing_note)

    return _note_response(existing_note, kb)


@router.delete("/api/v1/notes/{note_id}")
async def delete_note(
    note_id: str, db: AsyncSession = Depends(get_db), kb: KBContext = Depends(get_kb)
):
    """
    Delete a note from SQLite + vault .md, then best-effort graph/index cleanup.

    Vault file + DB row are removed first so a graph failure cannot leave an
    orphan markdown file or a broken UI still pointing at a deleted note.
    Graph / Qdrant / Meili cleanup is best-effort after commit (logged on failure).
    """
    return await _delete_note_impl(note_id, db, kb)


@router.post("/api/v1/notes/batch-delete")
async def batch_delete_notes(
    body: BatchDeleteNotesInput,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Delete many notes in the current KB (vault + SQLite + graph cleanup)."""
    ids = [i.strip() for i in (body.ids or []) if i and i.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="ids must not be empty")
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="At most 100 notes per batch")

    deleted: list[str] = []
    failed: list[dict] = []
    for note_id in ids:
        try:
            await _delete_note_impl(note_id, db, kb)
            deleted.append(note_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[batch-delete] Failed for %s: %s", note_id, exc)
            failed.append({"id": note_id, "error": str(exc)})

    return {
        "deleted": deleted,
        "failed": failed,
        "deleted_count": len(deleted),
        "failed_count": len(failed),
    }


def _attachment_rels_from_note_body(body: str) -> list[str]:
    """Extract vault-relative attachment paths from markdown links/images."""
    import re as _re

    rels: list[str] = []
    seen: set[str] = set()
    # Balanced parentheses are legal in a markdown URL ("Report (2026).pdf");
    # matching [^)\s]+ truncated those and left the file orphaned on disk.
    _link = r"!?\[[^\]]*\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\)"
    for match in _re.finditer(_link, body or ""):
        raw = match.group(1).rstrip("/")
        rel = vault_rel_from_url(raw)
        if not rel and raw.startswith("attachments/"):
            rel = raw
        if rel and rel not in seen and ".." not in rel.split("/"):
            seen.add(rel)
            rels.append(rel)
    return rels


def _best_effort_delete_index_node(kb: KBContext, node_id: str, label: str) -> None:
    try:
        kb.qdrant.delete_node(node_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[delete_note] Qdrant %s delete failed (%s): %s", label, node_id, exc)
    try:
        kb.meili.delete_node(node_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[delete_note] Meili %s delete failed (%s): %s", label, node_id, exc)


async def _delete_note_impl(
    note_id: str, db: AsyncSession, kb: KBContext
) -> dict:
    """Shared single-note delete used by DELETE and batch-delete."""
    from pathlib import Path as _Path

    note_row = await db.execute(
        select(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id)
    )
    note_obj = note_row.scalar_one_or_none()
    if not note_obj:
        # Idempotent — already gone from DB for this KB.
        return {
            "status": "deleted",
            "id": note_id,
            "orphans_removed": 0,
            "already_gone": True,
        }

    body = await asyncio.to_thread(note_body, note_obj, kb)
    rel_path = note_obj.rel_path
    attached_rels = _attachment_rels_from_note_body(body)

    vault = _Path(kb.vault_path).expanduser().resolve() if kb.vault_path else None
    if vault and rel_path:
        try:
            await asyncio.to_thread(delete_note_file, vault, rel_path)
            logger.info("[delete_note] Removed vault file %s", rel_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Do not fall back to raw path joins — that undoes safe_vault_join.
            logger.warning("[delete_note] Vault file delete failed (%s): %s", rel_path, exc)

    await db.execute(
        delete(NoteLink).where(
            NoteLink.kb_id == kb.kb_id,
            or_(
                NoteLink.source_note_id == note_id,
                NoteLink.target_note_id == note_id,
            ),
        )
    )
    await db.execute(delete(Note).where(Note.id == note_id, Note.kb_id == kb.kb_id))
    await db.commit()

    orphan_ids: list[str] = []
    try:
        rows = await asyncio.to_thread(
            kb.graph.execute_query,
            """
            MATCH (note:Node {id: $note_id, kind: 'note'})-[:REFERENCES]->(entity:Node)
            WHERE entity.kind <> 'note'
              AND NOT EXISTS {
                MATCH (other:Node {kind: 'note'})-[:REFERENCES]->(entity)
                WHERE other.id <> $note_id
              }
            RETURN entity.id AS entity_id
            """,
            {"note_id": note_id},
        )
        orphan_ids = [r["entity_id"] for r in (rows or []) if r.get("entity_id")]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[delete_note] Graph orphan query failed: %s", exc)

    try:
        await asyncio.to_thread(
            kb.graph.execute_query,
            "MATCH (n:Node {id: $id}) WHERE n.kind = 'note' DETACH DELETE n",
            {"id": note_id},
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[delete_note] Graph note delete failed: %s", exc)

    await asyncio.to_thread(_best_effort_delete_index_node, kb, note_id, "note")

    for entity_id in orphan_ids:
        try:
            await asyncio.to_thread(
                kb.graph.execute_query,
                "MATCH (n:Node {id: $id}) DETACH DELETE n",
                {"id": entity_id},
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "[delete_note] Graph orphan delete failed (%s): %s", entity_id, exc
            )
        await asyncio.to_thread(_best_effort_delete_index_node, kb, entity_id, "orphan")

    logger.info(
        "[delete_note] Deleted note %s; removed %s orphaned entity nodes.",
        note_id,
        len(orphan_ids),
    )

    for rel in attached_rels:
        try:
            if vault:
                await remove_upload(vault, rel)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[delete_note] Attachment delete failed (%s): %s", rel, exc)
    if attached_rels:
        logger.info(
            "[delete_note] Deleted %s attached file(s) for note %s.",
            len(attached_rels),
            note_id,
        )

    return {"status": "deleted", "id": note_id, "orphans_removed": len(orphan_ids)}
