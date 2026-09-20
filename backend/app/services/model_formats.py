"""GGUF layout helpers: shards, the GGUF magic, and name-based advisories.

Users point Orb at a ``*.gguf`` file or a folder holding one; everything runs
through llama-cpp-python. Header-level checks live in ``gguf_metadata``.
"""

from __future__ import annotations

import re
from pathlib import Path

# "Model-00001-of-00003.gguf" — llama.cpp loads the rest from the first shard.
SHARD_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)

GGUF_MAGIC = b"GGUF"


def is_shard_continuation(path: Path) -> bool:
    """True for shards 2..N; only the first shard is openable."""
    match = SHARD_RE.match(path.name)
    return bool(match) and int(match.group("index")) > 1


def looks_like_gguf(path: Path) -> bool:
    """Verify the GGUF magic rather than trusting the extension.

    macOS writes 4 KB ``._name`` AppleDouble sidecars beside files on exFAT
    volumes; they carry the same extension and are not models.
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == GGUF_MAGIC
    except OSError:
        return False


def loadable_path(path: Path) -> Path:
    """The file llama.cpp should open: a folder resolves to its first GGUF shard."""
    if not path.is_dir():
        return path
    candidates = sorted(
        p
        for p in path.glob("*.gguf")
        if p.is_file() and not is_shard_continuation(p) and looks_like_gguf(p)
    )
    return candidates[0] if candidates else path


def name_warnings(*names: str) -> list[str]:
    """Advisories from the name alone — a reranker GGUF carries no other hint."""
    haystack = " ".join(names).lower()
    warnings: list[str] = []
    if "rerank" in haystack:
        warnings.append("Looks like a reranker model; it will answer, but poorly.")
    if "embed" in haystack:
        warnings.append("Looks like an embedding model; it cannot hold a conversation.")
    return warnings
