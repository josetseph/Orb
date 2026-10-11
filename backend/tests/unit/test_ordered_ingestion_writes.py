"""Several notes extract at once, but each writes the graph in the order it was submitted."""

import asyncio

from app.core.config import settings
from app.workflows.ingestion import IngestionWorkflow


def test_writes_follow_submission_order(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_PIPELINE_CONCURRENCY", 3)
    order: list[int] = []

    async def note(wf, ticket, extract_seconds, fail=False):
        async with wf._pipeline_slot(ticket) as write_turn:
            await asyncio.sleep(extract_seconds)  # extraction: the later notes finish first
            if fail:
                raise RuntimeError("unusable reply")  # never writes; must not hold the others back
            await write_turn()
            order.append(ticket)

    async def main():
        wf = IngestionWorkflow()
        jobs = [note(wf, 0, 0.05), note(wf, 1, 0.01, fail=True), note(wf, 2, 0.0), note(wf, 3, 0.02)]
        await asyncio.gather(*jobs, return_exceptions=True)

    asyncio.run(main())
    assert order == [0, 2, 3]


def test_one_at_a_time_is_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "INGESTION_PIPELINE_CONCURRENCY", 1)
    active, peak = 0, 0

    async def note(wf, ticket):
        nonlocal active, peak
        async with wf._pipeline_slot(ticket) as write_turn:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            await write_turn()
            active -= 1

    async def main():
        wf = IngestionWorkflow()
        await asyncio.gather(*(note(wf, t) for t in range(3)))

    asyncio.run(main())
    assert peak == 1


def test_a_note_that_fails_before_its_slot_is_marked_failed(monkeypatch):
    """A status write that fails while queued (SQLite "database is locked") left the note "processing" forever."""
    import app.workflows.ingestion as ingestion
    from app.schemas.extraction import NoteInput

    events: list[str] = []

    class Tracker:
        async def begin_ingestion(self, kb_id):
            events.append("begin")

        async def end_ingestion(self, kb_id):
            events.append("end")

    async def locked(self, note_id, stage, model=None):
        raise RuntimeError("database is locked")

    async def mark_failed(self, note_id, reason=None):
        events.append(f"failed: {reason}")

    monkeypatch.setattr(ingestion, "_tracker", Tracker())
    monkeypatch.setattr(IngestionWorkflow, "_update_note_processing_status", locked)
    monkeypatch.setattr(IngestionWorkflow, "_mark_note_failed", mark_failed)

    async def main():
        wf = IngestionWorkflow()
        try:
            await wf.process_note(NoteInput(content="x"), "n1")
        except RuntimeError:
            pass
        return wf

    wf = asyncio.run(main())
    assert events[0] == "begin" and events[1] == "end" and events[2].startswith("failed")
    assert wf._write_turn == 1  # its ticket is released, so later notes are not held back
