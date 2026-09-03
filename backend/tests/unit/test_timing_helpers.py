"""Unit tests for the shared per-turn timing helpers."""

import logging

from app.services import timing
from app.services.local_models import ModelLoadClock


class _CapturingLogger(logging.Logger):
    def __init__(self):
        super().__init__("test-timing")
        self.lines: list[str] = []

    def info(self, msg, *args, **kwargs):  # noqa: D102
        self.lines.append(msg % args if args else msg)


class TestLogStageTiming:
    def test_splits_load_from_inference(self, monkeypatch):
        clock = ModelLoadClock()
        monkeypatch.setattr(timing, "load_delta", lambda before: ModelLoadClock.diff(before, clock.snapshot()))
        before = clock.snapshot()
        clock.record("chat", 57.0)
        log = _CapturingLogger()

        timing.log_stage_timing(log, "chat", 100.0, before, docs=6)

        line = log.lines[0]
        assert "total=100.0s" in line
        assert "model_load=57.0s" in line
        assert "inference=43.0s" in line
        assert "loads=chat×1" in line
        assert "docs=6" in line

    def test_no_loads_reports_all_inference(self, monkeypatch):
        monkeypatch.setattr(
            timing, "load_delta", lambda before: {"total_seconds": 0.0, "counts": {}}
        )
        log = _CapturingLogger()
        timing.log_stage_timing(log, "finance_chat", 12.0, {}, kb="Work")
        assert "model_load=0.0s" in log.lines[0]
        assert "inference=12.0s" in log.lines[0]
        assert "loads=none" in log.lines[0]

    def test_never_reports_negative_inference(self, monkeypatch):
        # Clock drift / a load started before the window must not go negative.
        monkeypatch.setattr(
            timing, "load_delta", lambda before: {"total_seconds": 30.0, "counts": {"chat": 1}}
        )
        log = _CapturingLogger()
        timing.log_stage_timing(log, "chat", 5.0, {})
        assert "inference=0.0s" in log.lines[0]

    def test_helpers_degrade_when_clock_unavailable(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name == "app.services.local_models":
                raise ImportError("no local models")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _boom)
        assert timing.load_snapshot() == {}
        assert timing.load_delta({})["total_seconds"] == 0.0
        assert timing.describe_loads({}) == "none"
