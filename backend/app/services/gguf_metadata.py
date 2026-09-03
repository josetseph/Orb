"""Read GGUF model metadata from the file header — no weights, no dependencies.

A GGUF file starts with a key/value block describing the model (architecture,
name, context length, tokenizer). Reading it costs ~0.1-0.3s even for a 7 GB
file, so Orb can describe every GGUF on disk without loading any of them.

Used to let users pick *any* local chat GGUF rather than only curated catalog
entries: the header says whether a file is usable as a chat model and what
context length it supports.

Spec: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from app.core.log import get_logger

logger = get_logger("GgufMetadata")

GGUF_MAGIC = b"GGUF"

# Guards against corrupt/hostile headers claiming absurd sizes. A real model has
# tens of KV pairs and a vocab in the low hundreds of thousands.
_MAX_KV_PAIRS = 4096
_MAX_STRING_BYTES = 64 * 1024 * 1024
_MAX_ARRAY_ITEMS = 8_000_000

# GGUF value type ids → struct formats (little-endian).
_SCALAR_FORMATS = {
    0: "<B",   # uint8
    1: "<b",   # int8
    2: "<H",   # uint16
    3: "<h",   # int16
    4: "<I",   # uint32
    5: "<i",   # int32
    6: "<f",   # float32
    7: "<?",   # bool
    10: "<Q",  # uint64
    11: "<q",  # int64
    12: "<d",  # float64
}
_TYPE_STRING = 8
_TYPE_ARRAY = 9

# Keys we keep. Everything else (notably the token vocab) is skipped.
_WANTED_SUFFIXES = (
    ".context_length",
    ".embedding_length",
    ".block_count",
    ".pooling_type",
)
_WANTED_EXACT = (
    "general.architecture",
    "general.name",
    "general.basename",
    "general.size_label",
    "general.file_type",
    "general.quantization_version",
    "tokenizer.chat_template",
)


class GgufError(RuntimeError):
    """The file is not a readable GGUF (bad magic, truncated, or malformed)."""


@dataclass(frozen=True)
class GgufInfo:
    """Header facts about one GGUF file."""

    path: Path
    size_bytes: int
    architecture: str = ""
    name: str = ""
    size_label: str = ""
    context_length: int | None = None
    embedding_length: int | None = None
    block_count: int | None = None
    file_type: int | None = None
    has_chat_template: bool = False
    # Present on embedding models (they pool token states); absent on causal LMs.
    pooling_type: int | None = None
    tensor_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_embedding_model(self) -> bool:
        """True when the file is an embedding model, which cannot serve chat.

        ``pooling_type`` is the only reliable signal: embed *and* rerank GGUFs
        derived from instruct models still carry a chat template, so the
        template says nothing about the role.
        """
        return self.pooling_type is not None

    @property
    def display_name(self) -> str:
        return self.name or self.path.stem

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2)


def _read_struct(stream: BinaryIO, fmt: str) -> tuple:
    size = struct.calcsize(fmt)
    data = stream.read(size)
    if len(data) != size:
        raise GgufError("Unexpected end of file while reading header")
    return struct.unpack(fmt, data)


def _read_string(stream: BinaryIO) -> str:
    (length,) = _read_struct(stream, "<Q")
    if length > _MAX_STRING_BYTES:
        raise GgufError(f"Refusing to read a {length}-byte metadata string")
    data = stream.read(length)
    if len(data) != length:
        raise GgufError("Unexpected end of file while reading a string")
    return data.decode("utf-8", errors="replace")


def _skip_or_read_value(stream: BinaryIO, value_type: int, keep: bool) -> Any:
    """Read a value when ``keep``; otherwise consume it as cheaply as possible."""
    if value_type == _TYPE_STRING:
        text = _read_string(stream)
        return text if keep else None
    if value_type == _TYPE_ARRAY:
        (elem_type,) = _read_struct(stream, "<I")
        (count,) = _read_struct(stream, "<Q")
        if count > _MAX_ARRAY_ITEMS:
            raise GgufError(f"Refusing to read a {count}-item metadata array")
        if elem_type == _TYPE_STRING:
            # Length-prefixed strings cannot be seeked past; read and drop them.
            values = []
            for _ in range(count):
                text = _read_string(stream)
                if keep:
                    values.append(text)
            return values if keep else None
        if elem_type == _TYPE_ARRAY:
            values = [_skip_or_read_value(stream, elem_type, keep) for _ in range(count)]
            return values if keep else None
        fmt = _SCALAR_FORMATS.get(elem_type)
        if fmt is None:
            raise GgufError(f"Unknown GGUF array element type {elem_type}")
        width = struct.calcsize(fmt)
        if keep:
            data = stream.read(width * count)
            if len(data) != width * count:
                raise GgufError("Unexpected end of file while reading an array")
            return list(struct.unpack(f"<{count}{fmt[1]}", data))
        stream.seek(width * count, 1)
        return None
    fmt = _SCALAR_FORMATS.get(value_type)
    if fmt is None:
        raise GgufError(f"Unknown GGUF value type {value_type}")
    return _read_struct(stream, fmt)[0]


def _is_wanted(key: str) -> bool:
    return key in _WANTED_EXACT or key.endswith(_WANTED_SUFFIXES)


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_gguf_metadata(path: str | Path) -> GgufInfo:
    """Parse one GGUF file's header.

    Raises :class:`GgufError` when the file is missing, not GGUF, or malformed.
    """
    file_path = Path(path)
    try:
        size_bytes = file_path.stat().st_size
    except OSError as exc:
        raise GgufError(f"Cannot stat {file_path}: {exc}") from exc

    try:
        with open(file_path, "rb", buffering=1024 * 1024) as stream:
            magic = stream.read(4)
            if magic != GGUF_MAGIC:
                raise GgufError(f"{file_path.name} is not a GGUF file")
            (_version,) = _read_struct(stream, "<I")
            (tensor_count,) = _read_struct(stream, "<Q")
            (kv_count,) = _read_struct(stream, "<Q")
            if kv_count > _MAX_KV_PAIRS:
                raise GgufError(f"Refusing to read {kv_count} metadata entries")

            found: dict[str, Any] = {}
            for _ in range(kv_count):
                key = _read_string(stream)
                (value_type,) = _read_struct(stream, "<I")
                keep = _is_wanted(key)
                value = _skip_or_read_value(stream, value_type, keep)
                if keep:
                    found[key] = value
    except GgufError:
        raise
    except OSError as exc:
        raise GgufError(f"Cannot read {file_path}: {exc}") from exc

    architecture = str(found.get("general.architecture") or "")

    def pick(suffix: str) -> Any:
        """Prefer the key namespaced by this file's architecture.

        Keys are ``<arch>.context_length`` etc. Matching on the suffix alone
        would pick arbitrarily if a file carried more than one namespace.
        """
        if architecture:
            exact = found.get(f"{architecture}{suffix}")
            if exact is not None:
                return exact
        for key, value in found.items():
            if key.endswith(suffix):
                return value
        return None

    return GgufInfo(
        path=file_path,
        size_bytes=size_bytes,
        architecture=architecture,
        name=str(found.get("general.name") or ""),
        size_label=str(found.get("general.size_label") or ""),
        context_length=_as_int(pick(".context_length")),
        embedding_length=_as_int(pick(".embedding_length")),
        block_count=_as_int(pick(".block_count")),
        file_type=_as_int(found.get("general.file_type")),
        has_chat_template=bool(found.get("tokenizer.chat_template")),
        pooling_type=_as_int(pick(".pooling_type")),
        tensor_count=int(tensor_count),
        raw=found,
    )


def try_read_gguf_metadata(path: str | Path) -> GgufInfo | None:
    """``read_gguf_metadata`` that returns ``None`` instead of raising.

    Scanning a folder must not fail because one file is a partial download.
    """
    try:
        return read_gguf_metadata(path)
    except GgufError as exc:
        logger.debug("Skipping %s: %s", path, exc)
        return None
