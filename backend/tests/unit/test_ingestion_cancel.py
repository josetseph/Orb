"""Cancel stops a queued or running pipeline; nothing running is a no-op."""

import asyncio

from app.workflows import ingestion


def test_cancel_stops_a_registered_pipeline():
    async def run():
        started = asyncio.Event()

        async def pipeline():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(pipeline())
        ingestion._running_ingestions[("kb", "n1")] = task
        await started.wait()
        assert ingestion.cancel_ingestion("kb", "n1") is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        assert ingestion.cancel_ingestion("kb", "n1") is False  # done tasks are not running
        assert ingestion.cancel_ingestion("kb", "nope") is False

    asyncio.run(run())


def test_failure_reason_is_actionable():
    err = RuntimeError("Ingestion Agent Failed: [\"Extraction failed after 3 attempts: Error code: 503 - "
                       "[{'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}]\"]")
    assert "503" in ingestion._failure_reason(err) and "retry" in ingestion._failure_reason(err)
    failed = RuntimeError("Ingestion Agent Failed: ['boom']")
    failed.reason = "boom"
    assert ingestion._failure_reason(failed) == "boom"
    long = ingestion._failure_reason(RuntimeError("x" * 500))
    assert len(long) <= 160 and long.endswith("…")
