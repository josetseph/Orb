"""Discover local chat GGUFs on disk, so any model can be used — not only catalog entries.

The curated catalog (``model_catalog.py``) stays the source of *download*
recommendations. This module answers a different question: "what is actually on
this machine right now?" Users can drop a GGUF into ``MODELS_DIR/gguf`` (or pick
one anywhere) and select it, whether Orb knows the model or not.

Model references stored per KB are one of:
  * a catalog id            — ``gemma4-e4b-q4``
  * a MODELS_DIR-relative path — ``gguf/My-Model-Q4_K_M.gguf``
  * an absolute path        — ``/Volumes/x/My-Model.gguf``

Relative refs are preferred for files under MODELS_DIR so a KB survives the user
moving their models directory (e.g. off a slow external disk).
"""

from __future__ import annotations

import functools
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.log import get_logger
from app.core.paths import resolve_models_dir
from app.services.gguf_metadata import GgufInfo, try_read_gguf_metadata
from app.services import model_formats
from app.services.model_formats import is_shard_continuation

logger = get_logger("ModelDiscovery")

# A real GGUF header alone is larger than this; anything smaller is a stub,
# a partial download, or a macOS AppleDouble sidecar.
_MIN_GGUF_BYTES = 32 * 1024

# Directories never worth walking for models. A virtualenv under MODELS_DIR can
# hold ~100k files; on an external disk that walk took minutes.
_SKIP_DIRS = frozenset(
    {"venv", ".venv", ".venvs", "node_modules", "__pycache__", "site-packages"}
)
# MODELS_DIR/gguf/<maybe one folder per model>/file.gguf is as deep as this goes.
_MAX_SCAN_DEPTH = 4


@dataclass(frozen=True)
class LocalModel:
    """A local GGUF found on disk."""

    ref: str  # what a KB stores (relative to MODELS_DIR when possible)
    path: str
    label: str
    architecture: str
    size_gb: float
    context_length: int | None
    shards: int
    # Non-blocking advisories, e.g. a file that looks like a reranker.
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


def shard_count(path: Path) -> int:
    match = model_formats.SHARD_RE.match(path.name)
    return int(match.group("total")) if match else 1


def is_probably_junk(path: Path) -> bool:
    """Skip dotfiles, macOS AppleDouble sidecars, and partial downloads."""
    name = path.name
    if name.startswith(".") or name.startswith("._"):
        return True
    if name.endswith((".partial", ".tmp", ".download")):
        return True
    try:
        return path.stat().st_size < _MIN_GGUF_BYTES
    except OSError:
        return True


def model_ref_for(path: Path, models_dir: Path | None = None) -> str:
    """MODELS_DIR-relative ref when the file lives under it, else an absolute path."""
    root = (models_dir or resolve_models_dir()).resolve()
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolve_model_ref(ref: str, models_dir: Path | None = None) -> Path | None:
    """Turn a stored path ref back into a file. ``None`` when it is not a path ref."""
    text = (ref or "").strip()
    if not text or not text.lower().endswith(".gguf"):
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (models_dir or resolve_models_dir()) / candidate
    return candidate


def chat_warnings(info: GgufInfo) -> list[str]:
    """Advisories for using ``info`` as a chat model (never hard failures).

    A reranker GGUF is architecturally a causal LM with no distinguishing
    metadata, so name text is the only available hint — hence a warning rather
    than a block.
    """
    warnings = model_formats.name_warnings(info.name, info.path.name)
    if not info.has_chat_template:
        warnings.append(
            "No chat template in the file — llama.cpp will fall back to a generic "
            "prompt format and quality may suffer."
        )
    return warnings


def describe_local_model(
    info: GgufInfo, models_dir: Path | None = None
) -> LocalModel:
    label = info.display_name
    if info.size_label and info.size_label.lower() not in label.lower():
        label = f"{label} ({info.size_label})"
    return LocalModel(
        ref=model_ref_for(info.path, models_dir),
        path=str(info.path),
        label=label,
        architecture=info.architecture,
        size_gb=info.size_gb,
        context_length=info.context_length,
        shards=shard_count(info.path),
        warnings=tuple(chat_warnings(info)),
    )


def scan_dir_for_gguf(root: Path, max_depth: int = _MAX_SCAN_DEPTH) -> list[Path]:
    """Every loadable GGUF under ``root``.

    Walks with pruning rather than ``rglob`` so a virtualenv or cache directory
    under MODELS_DIR is never descended into.
    """
    if not root.is_dir():
        return []
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        # Prune in place — os.walk honours mutation of ``dirnames``.
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [
                d for d in dirnames if not d.startswith(".") and d.lower() not in _SKIP_DIRS
            ]
        for filename in filenames:
            if not filename.lower().endswith(".gguf"):
                continue
            path = current / filename
            if is_probably_junk(path) or is_shard_continuation(path):
                continue
            found.append(path)
    return sorted(found)


@functools.lru_cache(maxsize=256)
def _read(path_str: str, _mtime_ns: int, _size: int) -> GgufInfo | None:
    """Header read cached by (path, mtime, size) so rescans are free."""
    return try_read_gguf_metadata(Path(path_str))


def _cached_metadata(path: Path) -> GgufInfo | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


clear_cache = _read.cache_clear


def discover_chat_models(models_dir: Path | None = None) -> list[LocalModel]:
    """Every local chat GGUF under MODELS_DIR, sorted by name.

    Embedding models are excluded outright — the header's ``pooling_type``
    identifies them, and they cannot chat.
    """
    root = (models_dir or resolve_models_dir()).resolve()
    models: list[LocalModel] = []
    for path in scan_dir_for_gguf(root):
        info = _cached_metadata(path)
        if info is None:
            continue
        if info.is_embedding_model:
            logger.debug("Skipping embedding model %s", path.name)
            continue
        models.append(describe_local_model(info, root))
    models.sort(key=lambda m: m.label.lower())
    return models


def inspect_chat_model(path: Path) -> tuple[GgufInfo | None, str | None]:
    """Validate one file for chat use. Returns ``(info, error)``.

    ``error`` is set when the file cannot serve chat at all.
    """
    if not path.exists():
        return None, f"No such file: {path}"
    if not path.is_file():
        return None, f"Not a file: {path}"
    info = try_read_gguf_metadata(path)
    if info is None:
        return None, f"{path.name} is not a readable GGUF file."
    if info.is_embedding_model:
        return info, (
            f"{info.display_name} is an embedding model and cannot be used for chat."
        )
    if is_shard_continuation(path):
        return info, (
            f"{path.name} is part {path.name.split('-')[-3]} of a split model — "
            "select the first shard (…-00001-of-…) instead."
        )
    return info, None
