"""Learn how large an extraction chunk each model can actually handle.

The limit that matters is the model's **output** ceiling, not its context
window: the extraction prompt emits roughly 2.5x its input as JSON, so a
million-token context still cannot answer a 300k-token chunk. Nothing exposes
that ceiling — the OpenAI ``/v1/models`` response carries ``id``/``object``/
``created``/``owned_by`` and no limits — so it cannot be looked up, only
discovered.

The pipeline already discovers it on every call: ``ingestion_generate_with_meta``
reports ``truncated`` when the model ran out of room. This module turns that
signal into a per-model budget that shrinks the moment output is truncated and
grows only after repeated successes at close to the current size, so a
conservative default converges on what a model can really do instead of
staying conservative forever.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.core.log import get_logger
from app.core.paths import resolve_data_dir

logger = get_logger("IngestionPipeline")

#: Cut to this fraction of the size that truncated. Aggressive on purpose — a
#: truncated call is wasted work, and the split-and-retry pays for it twice.
SHRINK = 0.6
#: Grow by this much once a model looks comfortable at its current size.
GROW = 1.3
#: Consecutive near-full successes before growing. Above 1 so a single easy
#: note cannot ratchet the budget up.
WINS_BEFORE_GROW = 3
#: A success only says something about the ceiling if it nearly filled it.
NEAR_FULL = 0.8
#: Never exceed this, whatever the model claims to allow: one enormous call is
#: slow to retry and loses everything when it fails.
HARD_CEILING = 32_000
#: Matches extraction_chunking.MIN_SPLIT_TOKENS; below this a truncated chunk
#: is repaired rather than split again.
FLOOR = 400

_STORE_NAME = "extraction_budgets.json"
_lock = threading.Lock()


def _store_path() -> Path:
    return resolve_data_dir() / _STORE_NAME


def _pinned() -> int | None:
    """An explicit chunk size in Settings disables learning."""
    from app.core.config import settings

    pinned = settings.EXTRACTION_CHUNK_TOKENS
    return max(FLOOR, int(pinned)) if pinned else None


def _load() -> dict:
    try:
        with open(_store_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        # Losing what we learned costs a slower next run, never correctness.
        logger.warning("[Extraction] Could not persist chunk budgets: %s", exc)


def _key(model: str | None) -> str:
    return (model or "default").strip() or "default"


def learned_budget(model: str | None, default: int) -> int:
    """The budget to use for ``model``, or ``default`` if nothing is known."""
    pinned = _pinned()
    if pinned is not None:
        return pinned
    with _lock:
        entry = _load().get(_key(model)) or {}
    value = entry.get("budget")
    if not isinstance(value, int):
        return default
    return max(FLOOR, min(HARD_CEILING, value))


def _update(model: str | None, mutate) -> None:
    if _pinned() is not None:
        return
    with _lock:
        data = _load()
        key = _key(model)
        entry = data.get(key)
        if not isinstance(entry, dict):
            entry = {}
        changed = mutate(entry)
        if not changed:
            return
        data[key] = entry
        _save(data)


def record_truncation(model: str | None, used_tokens: int) -> None:
    """Output ran out of room at ``used_tokens`` — that size is too big."""

    def mutate(entry: dict) -> bool:
        target = max(FLOOR, int(used_tokens * SHRINK))
        current = entry.get("budget")
        if isinstance(current, int) and current <= target:
            # Already at or below the size that failed; nothing new learned.
            entry["wins"] = 0
            return True
        entry["budget"] = target
        entry["wins"] = 0
        logger.info(
            "[Extraction] %s truncated at ~%d tokens — chunk budget now %d",
            _key(model),
            used_tokens,
            target,
        )
        return True

    _update(model, mutate)


def record_success(model: str | None, used_tokens: int, budget: int) -> None:
    """A chunk came back whole. Only near-full chunks say anything useful."""
    if budget <= 0 or used_tokens < budget * NEAR_FULL:
        return

    def mutate(entry: dict) -> bool:
        wins = int(entry.get("wins") or 0) + 1
        current = int(entry.get("budget") or budget)
        if wins < WINS_BEFORE_GROW:
            entry["wins"] = wins
            entry["budget"] = current
            return True
        grown = min(HARD_CEILING, int(current * GROW))
        entry["wins"] = 0
        entry["budget"] = grown
        if grown > current:
            logger.info(
                "[Extraction] %s handled %d full chunks — chunk budget now %d",
                _key(model),
                WINS_BEFORE_GROW,
                grown,
            )
        return True

    _update(model, mutate)
