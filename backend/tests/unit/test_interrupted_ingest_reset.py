"""A restart mid-ingest must not leave a note permanently 'ingesting'.

Nothing is ingesting at boot, so a note still carrying a pipeline stage was
interrupted. Left alone the UI treats it as running and refuses to re-ingest.
"""

import pytest

IDLE = ("Saved", "Ingestion complete", "Ingestion failed")


def _should_reset(stage: str | None, processed=False, failed=False) -> bool:
    """Mirrors the WHERE clause in main.startup_event."""
    if processed or failed or stage is None:
        return False
    if stage in IDLE or "pending" in stage or stage.startswith("Changed on disk"):
        return False
    return True


@pytest.mark.parametrize(
    "stage",
    ["Indexing entity contexts", "Reading PDF pages and images", "Transcribing audio"],
)
def test_mid_pipeline_stages_are_reset(stage):
    assert _should_reset(stage) is True


@pytest.mark.parametrize(
    "stage",
    ["Saved", "Ingestion complete", "Ingestion failed", "Changed on disk — pending", None],
)
def test_idle_and_watcher_stages_are_left_alone(stage):
    assert _should_reset(stage) is False


def test_finished_notes_are_untouched():
    assert _should_reset("Indexing entity contexts", processed=True) is False
    assert _should_reset("Indexing entity contexts", failed=True) is False
