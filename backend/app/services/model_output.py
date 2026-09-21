"""Model replies are used as written or not at all.

Nothing in the pipeline repairs, coerces, fuzzy-matches or fills in a reply. A
reply that does not parse, does not fit its schema, or contradicts what it was
given (a relationship naming an entity that was not listed) raises
``ModelOutputError`` and is counted. The count is a result of the experiment:
how often a model and prompt produce usable output is what is being measured.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.services import trace

T = TypeVar("T", bound=BaseModel)


class ModelOutputError(ValueError):
    """A model reply that cannot be used as written."""

    def __init__(self, stage: str, reason: str, raw: str = "") -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason, self.raw = stage, reason, raw


def failure_log() -> Path:
    return Path(settings.DATA_DIR) / "logs" / "invalid_model_output.jsonl"


def reject(stage: str, model: str | None, reason: str, raw: str = "") -> ModelOutputError:
    """Count an unusable reply (trace + DATA_DIR/logs/invalid_model_output.jsonl) and return the error to raise."""
    trace.record("invalid_output", stage=stage, model=model, reason=reason)
    try:
        path = failure_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "stage": stage, "model": model, "reason": reason, "raw": raw}) + "\n")
    except OSError:
        pass  # counting must never be what breaks a run
    return ModelOutputError(stage, reason, raw)


def parse(raw: str, schema: type[T], *, stage: str, model: str | None) -> T:
    """``raw`` must be JSON that fits ``schema`` exactly as the model wrote it."""
    try:
        return schema.model_validate_json(raw or "")
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "reply"
        raise reject(stage, model, f"{where}: {first['msg']} ({exc.error_count()} problem(s))", raw) from exc
