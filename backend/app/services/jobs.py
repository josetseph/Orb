"""Durable jobs: long work is written down before it starts and resumes after a crash.

A job is a row in the app database, added in the same commit as the status change
that asks for it. One worker drains the table in order. A job still ``running`` at
the next start was interrupted (crash, quit, power cut): it is queued again and its
saved steps replay (ingestion_checkpoint), so nothing finished is done twice.
See Docs/plan-resumable-work.md.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.log import get_logger
from app.models.job import Job

logger = get_logger("Jobs")

ACTIVE = ("queued", "running")
INGEST = "ingest_note"
ANSWER = "answer"
ATTACHMENT = "attachment"
#: A job interrupted this many times may be what takes the app down: it fails instead.
MAX_ATTEMPTS = 3
#: Finished rows are kept this long, then dropped at start.
KEEP_FINISHED = timedelta(days=30)

_wake: asyncio.Event | None = None
_tasks: set[asyncio.Task] = set()
#: Running job tasks by job id, so a cancel can stop one of any kind.
_running: dict[int, asyncio.Task] = {}


def input_hash(*parts: str | None) -> str:
    return hashlib.sha256("\0".join(p or "" for p in parts).encode("utf-8")).hexdigest()


def memo_key(kind: str, target_id: str) -> str:
    """The folder name a job's saved steps live under (ingestion_checkpoint)."""
    return target_id if kind == INGEST else f"{kind}-{input_hash(target_id)[:16]}"


async def enqueue(
    session: AsyncSession, kind: str, kb_id: str, target_id: str, digest: str, payload: dict | None = None
) -> None:
    """Add a job in the caller's transaction; call ``wake()`` after the commit.

    An active job for the same target with the same input is kept; one with another
    input is superseded (cancelled) by the new one.
    """
    active = (
        await session.execute(
            select(Job).where(
                Job.kind == kind, Job.kb_id == kb_id, Job.target_id == target_id, Job.state.in_(ACTIVE)
            )
        )
    ).scalars().all()
    if any(j.input_hash == digest for j in active):
        return
    for job in active:
        job.state = "cancelled"
        job.last_error = "superseded"
        if job.kind == INGEST:
            from app.workflows.ingestion import cancel_ingestion

            cancel_ingestion(kb_id, target_id)
    session.add(Job(kind=kind, kb_id=kb_id, target_id=target_id, input_hash=digest, payload=payload))


async def enqueue_ingest(session: AsyncSession, kb_id: str, note_id: str, content: str) -> None:
    await enqueue(session, INGEST, kb_id, note_id, input_hash(content))


async def cancel(kb_id: str, target_id: str, kind: str = INGEST) -> bool:
    """Cancel the target's active job (queued or running). False when there was none."""
    where = (Job.kind == kind, Job.kb_id == kb_id, Job.target_id == target_id, Job.state.in_(ACTIVE))
    async with AsyncSessionLocal() as session:
        ids = (await session.execute(select(Job.id).where(*where))).scalars().all()
        result = await session.execute(
            update(Job).where(*where).values(state="cancelled", last_error="cancelled by the user")
        )
        await session.commit()
    if kind != INGEST:
        for job_id in ids:
            task = _running.get(job_id)
            if task is not None:
                task.cancel()
        from app.services import ingestion_checkpoint

        ingestion_checkpoint.clear(kb_id, memo_key(kind, target_id))
    if kind == INGEST:
        from app.services import ingestion_checkpoint
        from app.workflows.ingestion import cancel_ingestion

        # The pipeline stops at its next await, before it could save another call.
        cancel_ingestion(kb_id, target_id)
        ingestion_checkpoint.clear(kb_id, target_id)
    return bool(result.rowcount)


def wake() -> None:
    if _wake is not None:
        _wake.set()


async def recover() -> list[str]:
    """At start nothing is running: queue interrupted jobs again, fail the ones interrupted
    ``MAX_ATTEMPTS`` times, drop old finished rows. Returns the note ids given up on."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        stuck = (await session.execute(select(Job).where(Job.state == "running"))).scalars().all()
        from app.models.note import Note

        waiting = set(
            (
                await session.execute(
                    select(Note.id).where(
                        Note.id.in_([j.target_id for j in stuck if j.kind == INGEST]),
                        Note.processing_stage.like("Queued%") | Note.processing_stage.like("Waiting%"),
                    )
                )
            ).scalars()
        )
        given_up = []
        for job in stuck:
            if job.target_id in waiting:
                job.attempts -= 1  # it was still waiting for a slot: it never ran
            if job.attempts >= MAX_ATTEMPTS:
                job.state = "failed"
                job.last_error = f"interrupted {job.attempts} times"
                if job.kind == INGEST:
                    given_up.append(job.target_id)
            else:
                job.state = "queued"
        await session.execute(
            delete(Job)
            .where(Job.state.not_in(ACTIVE), Job.updated_at < now - KEEP_FINISHED)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    if stuck:
        logger.info("Resuming %d interrupted job(s); %d given up", len(stuck) - len(given_up), len(given_up))
    return given_up


def start() -> None:
    """Start the worker on the running loop; it drains whatever is queued now."""
    global _wake  # pylint: disable=global-statement
    _wake = asyncio.Event()
    _wake.set()
    _spawn(_drain_forever())


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _drain_forever() -> None:
    while True:
        await _wake.wait()
        _wake.clear()
        try:
            await _drain()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Job worker: %s", exc)


async def _drain() -> None:
    """Start every queued job, oldest first. Ingestion keeps its own slots and write order,
    so starting them all at once is how the in-memory queue behaved too."""
    async with AsyncSessionLocal() as session:
        jobs = (
            await session.execute(select(Job).where(Job.state == "queued").order_by(Job.id))
        ).scalars().all()
        for job in jobs:
            job.state = "running"
            job.attempts += 1
        await session.commit()
    for job in jobs:
        # Prepared one by one, started in that order: a pipeline takes its write turn
        # when it starts, so the graph is written in queue order whatever finishes first.
        try:
            run = await _prepare(job)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await _finish(job.id, "failed", str(exc))
            continue
        _running[job.id] = asyncio.create_task(_run(job.id, run))
        _running[job.id].add_done_callback(lambda _t, i=job.id: _running.pop(i, None))


async def _finish(job_id: int, state: str, error: str | None = None) -> bool:
    """Record the outcome unless the job was cancelled meanwhile. True when written."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Job).where(Job.id == job_id, Job.state == "running").values(state=state, last_error=error)
        )
        await session.commit()
    return bool(result.rowcount)


async def _run(job_id: int, run) -> None:
    # A shutdown cancels this task: the row stays "running" and resumes at the next start.
    from app.workflows.ingestion import _failure_reason

    try:
        state = await run() if run else "cancelled"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        await _finish(job_id, "failed", _failure_reason(exc))
        return
    await _finish(job_id, state)


async def _prepare(job: Job):
    """Everything a job needs before it starts; returns the coroutine function that runs it,
    or None when its target is gone."""
    if job.kind == ANSWER:
        from app.api.chat import prepare_answer

        return prepare_answer(job)
    if job.kind == ATTACHMENT:
        from app.api.notes import prepare_attachment

        return prepare_attachment(job)
    if job.kind != INGEST:
        raise ValueError(f"unknown job kind {job.kind}")
    from app.models.note import Note
    from app.schemas.extraction import NoteInput
    from app.services.kb_registry import kb_registry
    from app.services.note_files import note_body

    kb = kb_registry.get_kb(job.kb_id)
    async with AsyncSessionLocal() as session:
        note = await session.get(Note, job.target_id)
    if kb is None or note is None:
        return None  # deleted since it was queued
    payload = NoteInput(
        content=await asyncio.to_thread(note_body, note, kb),
        created_at=note.created_at.isoformat() if note.created_at else None,
        title=(note.title or "").strip() or None,
    )
    pipeline = kb.get_ingestion_workflow().process_note(payload, job.target_id, resumed=job.attempts > 1)

    async def run() -> str:
        result = await pipeline
        return "cancelled" if result.get("status") == "cancelled" else "done"

    return run


async def get(kind: str, target_id: str) -> Job | None:
    """The newest job for a target, whatever its state."""
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(Job).where(Job.kind == kind, Job.target_id == target_id).order_by(Job.id.desc()).limit(1)
            )
        ).scalar_one_or_none()


async def for_kind(kind: str, kb_id: str) -> list[Job]:
    """Every job of ``kind`` in a KB, oldest first."""
    async with AsyncSessionLocal() as session:
        return list(
            (await session.execute(select(Job).where(Job.kind == kind, Job.kb_id == kb_id).order_by(Job.id))).scalars()
        )


async def active_for(kind: str, kb_id: str, key: str, value: str) -> Job | None:
    """The active job of ``kind`` whose payload has ``key == value`` (e.g. a conversation's answer)."""
    async with AsyncSessionLocal() as session:
        jobs = (
            await session.execute(
                select(Job).where(Job.kind == kind, Job.kb_id == kb_id, Job.state.in_(ACTIVE)).order_by(Job.id)
            )
        ).scalars()
        return next((j for j in jobs if (j.payload or {}).get(key) == value), None)


def has_active_sync(sync_session, kb_id: str, target_id: str, kind: str = INGEST) -> bool:
    """For sync callers (the vault watcher): is work queued or running for this target?"""
    return (
        sync_session.execute(
            select(Job.id).where(
                Job.kind == kind, Job.kb_id == kb_id, Job.target_id == target_id, Job.state.in_(ACTIVE)
            )
        ).first()
        is not None
    )
