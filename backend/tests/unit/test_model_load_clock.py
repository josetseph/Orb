"""Unit tests for the model-load clock that splits load time from inference."""

from app.services.local_models import ModelLoadClock


class TestModelLoadClock:
    def test_diff_reports_only_loads_inside_the_window(self):
        clock = ModelLoadClock()
        clock.record("chat", 50.0)
        before = clock.snapshot()
        clock.record("embed", 18.0)
        clock.record("embed", 2.0)
        clock.record("rerank", 10.5)
        delta = ModelLoadClock.diff(before, clock.snapshot())
        assert delta["counts"] == {"embed": 2, "rerank": 1}
        assert delta["seconds"] == {"embed": 20.0, "rerank": 10.5}
        assert delta["total_seconds"] == 30.5

    def test_empty_window(self):
        clock = ModelLoadClock()
        clock.record("chat", 1.0)
        snap = clock.snapshot()
        delta = ModelLoadClock.diff(snap, clock.snapshot())
        assert delta == {"total_seconds": 0.0, "seconds": {}, "counts": {}}
        assert ModelLoadClock.describe(delta) == "none"

    def test_describe_is_stable_and_compact(self):
        delta = {"counts": {"rerank": 2, "chat": 1}}
        assert ModelLoadClock.describe(delta) == "chat×1,rerank×2"

    def test_diff_tolerates_missing_snapshot(self):
        clock = ModelLoadClock()
        clock.record("chat", 3.0)
        delta = ModelLoadClock.diff({}, clock.snapshot())
        assert delta["counts"] == {"chat": 1}
