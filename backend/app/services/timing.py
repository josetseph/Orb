"""Per-turn timing that separates model *load* time from inference.

A bare stage timer cannot tell a slow disk from a slow model: on a cold GGUF
most of "extraction took 187s" is a multi-GB read. These helpers snapshot the
shared model-load clock around a unit of work and emit one structured line.
"""

from __future__ import annotations

import logging


def load_snapshot() -> dict:
    """Model-load clock snapshot; ``{}`` when local models are unavailable."""
    try:
        from app.services.local_models import model_load_clock

        return model_load_clock.snapshot()
    except Exception:  # pylint: disable=broad-exception-caught
        return {}


def load_delta(before: dict) -> dict:
    """Loads that happened since ``before``."""
    try:
        from app.services.local_models import ModelLoadClock, model_load_clock

        return ModelLoadClock.diff(before, model_load_clock.snapshot())
    except Exception:  # pylint: disable=broad-exception-caught
        return {"total_seconds": 0.0, "seconds": {}, "counts": {}}


def describe_loads(delta: dict) -> str:
    """Compact ``chat×1,embed×2`` summary of a load delta."""
    try:
        from app.services.local_models import ModelLoadClock

        return ModelLoadClock.describe(delta)
    except Exception:  # pylint: disable=broad-exception-caught
        return "none"


def log_stage_timing(
    logger: logging.Logger,
    kind: str,
    total: float,
    load_before: dict,
    **extra,
) -> None:
    """Emit ``[Timing] <kind> total=… model_load=… inference=… loads=…``."""
    delta = load_delta(load_before)
    load = float(delta.get("total_seconds") or 0.0)
    extras = " ".join(f"{k}={v}" for k, v in extra.items())
    logger.info(
        "[Timing] %s total=%.1fs model_load=%.1fs inference=%.1fs loads=%s %s",
        kind,
        total,
        load,
        max(0.0, total - load),
        describe_loads(delta),
        extras,
    )
