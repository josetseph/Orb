"""A note whose "Queued for ingestion" write fails must not stay "processing" forever."""

import asyncio

from app.workflows.ingestion import IngestionWorkflow


def test_a_note_that_fails_before_its_slot_is_marked_failed(monkeypatch):
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
        try:
            await IngestionWorkflow().process_note(NoteInput(content="x"), "n1")
        except RuntimeError:
            pass

    asyncio.run(main())
    assert events[:2] == ["begin", "end"] and events[2].startswith("failed")
