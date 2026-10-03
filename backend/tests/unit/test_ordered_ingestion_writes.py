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
