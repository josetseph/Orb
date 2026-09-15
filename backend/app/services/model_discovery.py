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

import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.log import get_logger
from app.core.paths import resolve_models_dir
from app.services.gguf_metadata import GgufInfo, try_read_gguf_metadata
from app.services import model_formats

logger = get_logger("ModelDiscovery")

# "Model-00001-of-00003.gguf" — llama.cpp loads the rest from the first shard.
_SHARD_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)

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
    """A local model found on disk, in any supported layout."""

    ref: str  # what a KB stores (relative to MODELS_DIR when possible)
    path: str
    label: str
    architecture: str
    size_gb: float
    context_length: int | None
    shards: int
    # "gguf" | "mlx" | "transformers"
    format: str = "gguf"
    #: False when this machine cannot run the layout (reason below).
    runnable: bool = True
    unsupported_reason: str | None = None
    # Non-blocking advisories, e.g. a file that looks like a reranker.
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


def is_shard_continuation(path: Path) -> bool:
    """True for shards 2..N — only the first shard is openable / listed."""
    match = _SHARD_RE.match(path.name)
    return bool(match) and int(match.group("index")) > 1


def shard_count(path: Path) -> int:
    match = _SHARD_RE.match(path.name)
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
    warnings: list[str] = []
    haystack = f"{info.name} {info.path.name}".lower()
    if "rerank" in haystack:
        warnings.append("Looks like a reranker model; it will answer, but poorly.")
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


def scan_dir_for_models(root: Path, max_depth: int = _MAX_SCAN_DEPTH) -> list[Path]:
    """Every candidate model under ``root``: GGUF files and model folders.

    Walks with pruning rather than ``rglob`` so a virtualenv or cache directory
    under MODELS_DIR is never descended into. A folder holding ``config.json``
    is a candidate in its own right (MLX / Hugging Face layouts) and is not
    descended into further.
    """
    if not root.is_dir():
        return []
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.parts) - root_depth
        # A model folder is a leaf: never walk into its shards or subfolders.
        if "config.json" in filenames and current != root:
            found.append(current)
            dirnames[:] = []
            continue
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


# Back-compat alias for callers that only wanted GGUFs.
scan_dir_for_gguf = scan_dir_for_models


class _MetadataCache:
    """Caches header reads keyed by (path, mtime, size) so rescans are free."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, int, GgufInfo | None]] = {}

    def get(self, path: Path) -> GgufInfo | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = str(path)
        with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                return cached[2]
        info = try_read_gguf_metadata(path)
        with self._lock:
            self._entries[key] = (stat.st_mtime, stat.st_size, info)
        return info

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _MetadataCache()


def clear_cache() -> None:
    _cache.clear()


def _is_orb_support_model(path: Path) -> bool:
    """True for the media models Orb downloads for itself."""
    try:
        from app.services.multimodal_models import multimodal_model_path

        resolved = path.resolve()
        return any(
            multimodal_model_path(kind).resolve() == resolved
            for kind in ("asr", "marlin")
        )
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def discover_chat_models(
    models_dir: Path | None = None, *, include_unrunnable: bool = True
) -> list[LocalModel]:
    """Every local chat model under MODELS_DIR, in any layout, sorted by name.

    Embedding models are excluded outright — a GGUF embedder is identified by
    ``pooling_type`` and an HF one by its architecture, and neither can chat.
    Layouts this machine cannot run are listed with ``runnable=False`` and a
    reason instead of being hidden, so "where did my model go?" never happens.
    """
    root = (models_dir or resolve_models_dir()).resolve()
    models: list[LocalModel] = []
    for path in scan_dir_for_models(root):
        if path.is_file():
            # GGUF: the header carries pooling_type and the context window.
            info = _cache.get(path)
            if info is None:
                continue
            if info.is_embedding_model:
                logger.debug("Skipping embedding model %s", path.name)
                continue
            models.append(describe_local_model(info, root))
            continue

        if _is_orb_support_model(path):
            # Qwen3-ASR and Marlin live in MODELS_DIR by design; they
            # are Orb's own media models, never chat options.
            continue
        described = model_formats.describe(path)
        if described is None:
            continue
        if not described.chat_capable:
            # Speech models and encoders live in MODELS_DIR too; listing
            # them as "blocked chat models" is noise, not information.
            logger.debug("Skipping non-chat model %s", path.name)
            continue
        if not include_unrunnable and not described.runnable:
            continue
        models.append(
            LocalModel(
                ref=model_ref_for(path, root),
                path=str(path),
                label=described.name,
                architecture=described.format.value,
                size_gb=described.size_gb,
                context_length=None,
                shards=1,
                format=described.format.value,
                runnable=described.runnable,
                unsupported_reason=described.unsupported_reason,
                warnings=described.warnings,
            )
        )
    models.sort(key=lambda m: m.label.lower())
    return models


def inspect_any_chat_model(path: Path) -> tuple[object | None, str | None]:
    """Validate any supported layout for chat. Returns ``(info, error)``.

    GGUF keeps its header-based checks; other layouts go through
    ``model_formats``, which reports why an MLX or Hugging Face folder cannot
    run here rather than failing later inside a loader.
    """
    if not path.exists():
        return None, f"No such file: {path}"
    if path.is_file():
        return inspect_chat_model(path)
    described = model_formats.describe(path)
    if described is None:
        return None, (
            f"{path.name} does not look like a model folder — expected a .gguf "
            "file, or config.json beside weights."
        )
    if not described.runnable:
        return described, described.unsupported_reason
    return described, None


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
