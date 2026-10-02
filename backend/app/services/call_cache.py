"""Experiment cache for reranker scores and query embeddings (``LLM_CALL_CACHE_DIR``; off when unset).

The GPU returns slightly different floats for the same input from run to run, and a reranker score
that moves in the third decimal reorders candidates, changes what the model reads and so the answer.
Recording them, like model replies, makes two runs of one configuration identical and lets a change
replay everything it does not touch. The key is the kind of call, the model file and the exact inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings


def cached(kind: str, key: list[Any], compute: Callable[[], Any]) -> Any:
    if not settings.LLM_CALL_CACHE_DIR:
        return compute()
    digest = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = Path(settings.LLM_CALL_CACHE_DIR) / kind / digest[:2] / f"{digest}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # lanes share the cache
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)
    return value
