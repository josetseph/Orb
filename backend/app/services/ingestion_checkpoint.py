"""Resume a failed ingestion from the model call that broke, not from the top.

Every model call the pipeline makes for a note is memoised on disk under
DATA_DIR/ingestion_cache/<kb>/<note>/, keyed by the model and the prompt. A
retry after a 503 or a cancel replays the calls that already succeeded from
the cache — the prompts are deterministic, so chunk 7 of 12 gets the same
prompt it got last time — and only pays for the rest. The note's cache is
deleted when its ingestion completes, so a deliberate re-ingest starts clean
and the folder never outlives the failure it was saved for.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextvars import ContextVar
from pathlib import Path

from app.core.log import get_logger
from app.core.paths import resolve_data_dir

logger = get_logger("IngestionPipeline")

#: (kb_id, note_id) of the ingestion running in this task, or None.
_active: ContextVar[tuple[str, str] | None] = ContextVar("ingestion_checkpoint", default=None)


def _dir(kb_id: str, note_id: str) -> Path:
    return resolve_data_dir() / "ingestion_cache" / kb_id / note_id


def activate(kb_id: str, note_id: str) -> int:
    """Route this task's model calls through the note's cache; returns calls already saved."""
    _active.set((kb_id, note_id))
    d = _dir(kb_id, note_id)
    return sum(1 for p in d.glob("*.json")) if d.is_dir() else 0


def clear(kb_id: str, note_id: str) -> None:
    """Drop the note's saved calls — its ingestion finished."""
    shutil.rmtree(_dir(kb_id, note_id), ignore_errors=True)


def _slot(llm, prompt: str, temperature: float) -> Path | None:
    key = _active.get()
    if key is None:
        return None
    digest = hashlib.sha256(
        f"{llm.get_ingestion_model()}\n{temperature}\n{prompt}".encode("utf-8")
    ).hexdigest()
    return _dir(*key) / f"{digest}.json"


async def generate_with_meta(
    llm, prompt: str, temperature: float = 0.1, json_mode: bool = False
) -> tuple[str, dict]:
    """``llm.ingestion_generate_with_meta`` with a replay from disk when the call was already made."""
    slot = _slot(llm, prompt, temperature)
    if slot is not None and slot.exists():
        try:
            saved = json.loads(slot.read_text(encoding="utf-8"))
            return saved["raw"], saved["meta"]
        except (OSError, ValueError, KeyError):
            pass  # unreadable checkpoint: just make the call again
    raw, meta = await llm.ingestion_generate_with_meta(prompt, temperature=temperature, json_mode=json_mode)
    if slot is not None:
        try:
            slot.parent.mkdir(parents=True, exist_ok=True)
            slot.write_text(json.dumps({"raw": raw, "meta": meta}), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save ingestion checkpoint: %s", exc)
    return raw, meta


async def generate(llm, prompt: str, temperature: float = 0.1, json_mode: bool = False) -> str:
    raw, _ = await generate_with_meta(llm, prompt, temperature=temperature, json_mode=json_mode)
    return raw
