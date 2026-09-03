"""Admin / maintenance endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_kb
from app.core.database import get_db
from app.core.log import get_logger
from app.models.note import Note
from app.schemas.extraction import NoteInput
from app.services.ai_gate import require_ai
from app.services.kb_registry import KBContext
from app.services.note_files import note_body

logger = get_logger("API")
router = APIRouter()


@router.get("/api/v1/admin/maintenance-status")
async def get_maintenance_status(kb: KBContext = Depends(get_kb)):
    """Return the running state of background maintenance jobs."""
    return kb.get_ingestion_workflow().get_maintenance_status()


@router.post("/api/v1/admin/rebuild-communities")
async def rebuild_communities(
    background_tasks: BackgroundTasks, kb: KBContext = Depends(get_kb)
):
    """
    Trigger a full Leiden community detection pass in the background.

    Useful when community detection was cancelled or never ran after ingestion.
    The job runs asynchronously; poll the server logs for progress.
    COMMUNITY_DETECTION_ENABLED only controls the automatic post-ingestion trigger;
    this manual endpoint is always available.
    """
    background_tasks.add_task(kb.get_ingestion_workflow().rebuild_leiden_communities)
    return {
        "status": "started",
        "message": "Leiden community recompute triggered. Check server logs for progress.",
    }


class TemporalDigestInput(BaseModel):
    """Optional request body for the temporal digest build endpoint."""

    period: str | None = (
        None  # "month" | "week" | "year" — defaults to TEMPORAL_DIGEST_PERIOD
    )


@router.post("/api/v1/admin/build-temporal-digests")
async def build_temporal_digests(
    background_tasks: BackgroundTasks,
    body: TemporalDigestInput = TemporalDigestInput(),
    kb: KBContext = Depends(get_kb),
):
    """
    Trigger a temporal digest build in the background.

    Groups all ``isolated_context`` chunks that carry a ``note_created_at`` date
    by the requested time period, summarises each bucket with the LLM, and stores
    the results as ``temporal_digest`` nodes in the knowledge graph.
    TEMPORAL_DIGESTS_ENABLED only controls the automatic post-ingestion trigger;
    this manual endpoint is always available.
    """
    from app.core.config import settings as _settings

    _period = body.period or _settings.TEMPORAL_DIGEST_PERIOD
    background_tasks.add_task(
        kb.get_ingestion_workflow().build_temporal_digests, _period
    )
    return {
        "status": "started",
        "message": f"Temporal digest build triggered (period={_period}). Check server logs for progress.",
    }


@router.post("/api/v1/admin/reset-ingestion-data")
async def reset_ingestion_data(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """
    Wipe all ingestion data for the current KB and mark every note as unprocessed.

    Clears the Kuzu graph, Qdrant vector collections, and Meilisearch index
    for this KB, then resets ``processed`` and ``failed`` flags on all notes so
    they are eligible for re-ingestion.  The store wipes run as a background task;
    the SQLite flag reset is committed before the response is returned.
    """
    # Reset note flags immediately so the caller sees the correct state right away.
    await db.execute(
        update(Note).where(Note.kb_id == kb.kb_id).values(processed=False, failed=False)
    )
    await db.commit()

    # Wipe vector/graph/search stores in the background (sync service calls).
    def _wipe_stores() -> None:
        node_count = kb.graph.wipe_all_nodes()
        logger.info(f"[Admin] reset-ingestion-data: wiped {node_count} graph nodes")
        kb.qdrant.reset_all()
        kb.meili.reset_all()
        logger.info("[Admin] reset-ingestion-data: all stores cleared")

    background_tasks.add_task(_wipe_stores)
    return {
        "status": "started",
        "message": "Ingestion data cleared. Notes marked as unprocessed. Store wipes running in background.",
    }


@router.post("/api/v1/admin/reingest-all")
async def reingest_all(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    kb: KBContext = Depends(get_kb),
):
    """Queue notes in the current KB for ingestion (reads vault .md bodies)."""
    require_ai(kb)
    result = await db.execute(
        select(Note)
        .where(Note.kb_id == kb.kb_id)
        .where(
            (Note.processed == False) | (Note.failed == True)  # noqa: E712
        )
    )
    notes = result.scalars().all()

    wf = kb.get_ingestion_workflow()
    for note in notes:
        note_data = NoteInput(
            content=note_body(note, kb),
            created_at=note.created_at.isoformat() if note.created_at else None,
            title=note.title,
        )
        background_tasks.add_task(wf.process_note, note_data, note.id)

    logger.info(f"[Admin] reingest-all: queued {len(notes)} notes for KB '{kb.kb_id}'")
    return {
        "status": "queued",
        "notes_queued": len(notes),
        "message": f"Queued {len(notes)} notes for ingestion.",
    }
