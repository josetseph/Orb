"""Resume a failed or interrupted ingestion from the step that broke, not from the top.

Every expensive step the pipeline takes for a note is memoised on disk under
DATA_DIR/ingestion_cache/<kb>/<note>/, keyed by everything that decides its
result: each model call (provider, model, messages, settings — see
``LLMService._chat``), each image read by the vision model, each window of a
transcription. A retry after a 503, a cancel or a crash replays the steps that
already succeeded — the inputs are deterministic, so chunk 7 of 12 gets the
same prompt it got last time — and only pays for the rest. Only successes are
saved; a reply a caller rejects is ``forget``-ed. The note's folder is deleted
when its ingestion completes, so a deliberate re-ingest starts clean and the
folder never outlives the failure it was saved for.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from app.core.log import get_logger
from app.core.paths import resolve_data_dir

logger = get_logger("IngestionPipeline")

#: (kb_id, note_id) of the ingestion running in this task, or None.
_active: ContextVar[tuple[str, str] | None] = ContextVar("ingestion_checkpoint", default=None)


def _dir(kb_id: str, note_id: str) -> Path:
    return resolve_data_dir() / "ingestion_cache" / kb_id / note_id


def activate(kb_id: str, note_id: str) -> int:
    """Route this task's steps through the note's cache; returns steps already saved."""
    _active.set((kb_id, note_id))
    d = _dir(kb_id, note_id)
    return sum(1 for p in d.glob("*.json")) if d.is_dir() else 0


def clear(kb_id: str, note_id: str) -> None:
    """Drop the note's saved steps — its ingestion finished."""
    shutil.rmtree(_dir(kb_id, note_id), ignore_errors=True)


def _slot(kind: str, parts: list) -> Path | None:
    key = _active.get()
    if key is None:
        return None
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return _dir(*key) / f"{kind}-{digest}.json"


def memo(kind: str, parts: list, compute: Callable[[], Any], keep: Callable[[Any], bool] = bool) -> Any:
    """``compute()`` once per ingestion: inside one, the result (JSON) is saved keyed by
    ``kind`` and ``parts`` and replayed on a retry. Outside one it simply runs.

    ``keep`` says which results count as a success worth saving.
    """
    slot = _slot(kind, parts)
    if slot is not None and slot.exists():
        try:
            return json.loads(slot.read_text(encoding="utf-8"))["value"]
        except (OSError, ValueError, KeyError):
            pass  # unreadable (a crash mid-write): just run the step again
    value = compute()
    if slot is not None and keep(value):
        try:
            slot.parent.mkdir(parents=True, exist_ok=True)
            slot.write_text(json.dumps({"value": value}), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Could not save ingestion checkpoint: %s", exc)
    return value


def forget(kind: str, parts: list) -> None:
    """Drop a saved result that turned out unusable, so a retry runs the step again."""
    slot = _slot(kind, parts)
    if slot is not None:
        slot.unlink(missing_ok=True)


def chat_parts(provider, model, messages, temperature, max_tokens, json_mode) -> list:
    """What decides a model reply — the key ``LLMService._chat`` saves it under."""
    return [provider, model, messages, temperature, max_tokens, json_mode]


def forget_reply(llm, prompt: str, temperature: float, json_mode: bool = True) -> None:
    """``forget`` for an ingestion-model reply to ``prompt`` that a caller could not use."""
    forget(
        "llm",
        chat_parts(
            getattr(llm, "ingestion_provider", None),
            llm.get_ingestion_model(),
            [{"role": "user", "content": prompt}],
            temperature,
            None,
            json_mode,
        ),
    )


def file_digest(path: str) -> str:
    """Content key for a file a step reads (an image, a page render)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
