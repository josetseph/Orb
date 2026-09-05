"""Identify what kind of local model a path holds, and which runtime can run it.

Users point Orb at a file or a folder; Orb works out the rest. Three layouts
matter for chat:

* **GGUF** — one ``*.gguf`` file (or a folder holding one). Runs through
  llama-cpp-python on every platform.
* **MLX** — a Hugging Face-style folder whose ``config.json`` carries a
  ``quantization`` block, or whose weights are ``.npz``. Runs through ``mlx_lm``
  on Apple Silicon only.
* **Transformers** — a plain Hugging Face folder (``config.json`` +
  ``*.safetensors`` / ``*.bin``). Runs through ``transformers`` when the
  multimodal extras are installed.

Detection reports what a folder *is*; :func:`resolve_runtime` decides what can
actually run it here. Those are deliberately separate, following the engine
selection in local-transcription-service: a layout that this machine cannot run
should produce a precise message, never a silent substitution.
"""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
from pathlib import Path

from app.core.log import get_logger

logger = get_logger("ModelFormats")

# "Model-00001-of-00003.gguf" — llama.cpp loads the rest from the first shard.
SHARD_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.I)

_MLX_WEIGHTS = ("weights.npz", "model.npz")
_HF_WEIGHT_GLOBS = ("*.safetensors", "*.bin")

GGUF_MAGIC = b"GGUF"

# Only a causal language model can hold a conversation. Whisper (ASR) and
# Florence (vision) folders are valid Hugging Face layouts and would otherwise
# be offered as chat models — both sit in Orb's own models directory.
_CAUSAL_SUFFIXES = ("ForCausalLM", "ForConditionalGeneration", "LMHeadModel")
_NON_CHAT_MODEL_TYPES = {
    "whisper": "a speech-recognition model",
    "florence2": "a vision model",
    "clip": "an image-embedding model",
    "bert": "an embedding model",
    "xlm-roberta": "an embedding model",
}

# ``ForConditionalGeneration`` alone is too permissive — Marlin (video) and
# Whisper (audio) both use it. Match the architecture family instead, which is
# what actually distinguishes them from a chat model.
_NON_CHAT_ARCHITECTURES = {
    "marlin": "a video-understanding model",
    "whisper": "a speech-recognition model",
    "florence": "a vision model",
    "clip": "an image-embedding model",
    "blip": "an image-captioning model",
    "siglip": "an image-embedding model",
}


class ModelFormat(str, Enum):
    GGUF = "gguf"
    MLX = "mlx"
    TRANSFORMERS = "transformers"


class Runtime(str, Enum):
    LLAMA_CPP = "llama_cpp"
    MLX_LM = "mlx_lm"
    TRANSFORMERS = "transformers"


#: Which runtime serves each layout.
FORMAT_RUNTIME = {
    ModelFormat.GGUF: Runtime.LLAMA_CPP,
    ModelFormat.MLX: Runtime.MLX_LM,
    ModelFormat.TRANSFORMERS: Runtime.TRANSFORMERS,
}

_RUNTIME_MODULE = {
    Runtime.LLAMA_CPP: "llama_cpp",
    Runtime.MLX_LM: "mlx_lm",
    Runtime.TRANSFORMERS: "transformers",
}

_RUNTIME_INSTALL_HINT = {
    Runtime.LLAMA_CPP: "pip install llama-cpp-python",
    Runtime.MLX_LM: "pip install mlx-lm  (Apple Silicon only)",
    Runtime.TRANSFORMERS: (
        "install the multimedia extras: "
        "pip install -r backend/requirements-multimodal.txt"
    ),
}


@dataclass(frozen=True)
class LocalModel:
    """A local model Orb can describe, whether or not it can run it."""

    path: Path
    format: ModelFormat
    name: str
    size_bytes: int
    #: Set when the layout cannot run on this machine.
    unsupported_reason: str | None = None
    #: False when this is not a chat model at all (Whisper, Florence, an
    #: encoder). Distinct from a runtime that is merely unavailable here:
    #: a scanner should hide the former and surface the latter.
    chat_capable: bool = True
    warnings: tuple[str, ...] = ()

    @property
    def runtime(self) -> Runtime:
        return FORMAT_RUNTIME[self.format]

    @property
    def runnable(self) -> bool:
        return self.unsupported_reason is None

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2)


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def runtime_installed(runtime: Runtime) -> bool:
    module = _RUNTIME_MODULE[runtime]
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def is_shard_continuation(path: Path) -> bool:
    """True for shards 2..N; only the first shard is openable."""
    match = SHARD_RE.match(path.name)
    return bool(match) and int(match.group("index")) > 1


def _dir_size(path: Path, limit: int = 4000) -> int:
    total = 0
    for count, child in enumerate(path.rglob("*")):
        if count > limit:
            break
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _read_config(path: Path) -> dict:
    try:
        return json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


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


def _first_gguf(path: Path) -> Path | None:
    """The loadable GGUF in a folder — first shard when the model is split."""
    candidates = sorted(
        p
        for p in path.glob("*.gguf")
        if p.is_file() and not is_shard_continuation(p) and looks_like_gguf(p)
    )
    return candidates[0] if candidates else None


def _has_any(path: Path, globs: tuple[str, ...]) -> bool:
    return any(next(path.glob(g), None) is not None for g in globs)


def detect_format(path: Path) -> ModelFormat | None:
    """What layout is at ``path``? ``None`` when it is not a recognisable model."""
    if path.is_file():
        if path.suffix.lower() == ".gguf" and looks_like_gguf(path):
            return ModelFormat.GGUF
        return None
    if not path.is_dir():
        return None

    if _first_gguf(path) is not None:
        return ModelFormat.GGUF

    if not (path / "config.json").exists():
        return None

    # MLX bundles are HF-shaped. The reliable markers are .npz weights or a
    # quantization block, which mlx_lm writes when converting a model.
    if any((path / name).exists() for name in _MLX_WEIGHTS):
        return ModelFormat.MLX
    config = _read_config(path)
    if isinstance(config.get("quantization"), dict):
        return ModelFormat.MLX

    if _has_any(path, _HF_WEIGHT_GLOBS):
        return ModelFormat.TRANSFORMERS
    return None


def loadable_path(path: Path, model_format: ModelFormat) -> Path:
    """The exact path a runtime should open (a GGUF folder resolves to its file)."""
    if model_format is ModelFormat.GGUF and path.is_dir():
        first = _first_gguf(path)
        if first is not None:
            return first
    return path


def chat_capability_problem(path: Path, model_format: ModelFormat) -> str | None:
    """Why this model cannot serve chat, from its config. ``None`` if it can.

    Only applies to Hugging Face-shaped folders; a GGUF carries no architecture
    list, so name heuristics remain the only hint there.
    """
    if model_format is ModelFormat.GGUF:
        return None
    config = _read_config(path)
    model_type = str(config.get("model_type") or "").lower()
    if model_type in _NON_CHAT_MODEL_TYPES:
        return (
            f"{path.name} is {_NON_CHAT_MODEL_TYPES[model_type]} "
            f"({model_type}), not a chat model."
        )
    architectures = config.get("architectures")
    if isinstance(architectures, list) and architectures:
        for arch in architectures:
            if not isinstance(arch, str):
                continue
            lowered = arch.lower()
            for family, description in _NON_CHAT_ARCHITECTURES.items():
                if lowered.startswith(family):
                    return f"{path.name} is {description} ({arch}), not a chat model."
        if not any(
            isinstance(a, str) and a.endswith(_CAUSAL_SUFFIXES) for a in architectures
        ):
            return (
                f"{path.name} is a {architectures[0]} model, which cannot "
                "generate chat responses."
            )
    return None


def resolve_runtime(model_format: ModelFormat) -> tuple[Runtime, str | None]:
    """``(runtime, unsupported_reason)`` for running ``model_format`` here."""
    runtime = FORMAT_RUNTIME[model_format]
    if runtime is Runtime.MLX_LM and not is_apple_silicon():
        return runtime, (
            "MLX models run on Apple Silicon only. Use a GGUF build of this "
            "model on this machine."
        )
    if not runtime_installed(runtime):
        return runtime, (
            f"This is a {model_format.value} model, which needs the "
            f"{_RUNTIME_MODULE[runtime]} runtime — {_RUNTIME_INSTALL_HINT[runtime]}."
        )
    return runtime, None


def model_display_name(path: Path, model_format: ModelFormat) -> str:
    if model_format is ModelFormat.GGUF:
        target = loadable_path(path, model_format)
        stem = SHARD_RE.match(target.name)
        return stem.group("stem") if stem else target.stem
    config = _read_config(path)
    # The folder name beats model_type: a Marlin checkout reports "qwen3_5",
    # which tells the user nothing about what they are looking at.
    value = config.get("_name_or_path")
    if isinstance(value, str) and value.strip():
        return Path(value).name
    return path.name


def describe(path: str | Path) -> LocalModel | None:
    """Describe the model at ``path``, or ``None`` if it is not one."""
    target = Path(path).expanduser()
    model_format = detect_format(target)
    if model_format is None:
        return None
    _runtime, runtime_reason = resolve_runtime(model_format)
    # A layout Orb could run, holding a model that cannot chat, is still
    # unsupported — and saying which kind of model it is beats a generic error.
    capability_reason = chat_capability_problem(target, model_format)
    reason = runtime_reason or capability_reason
    loadable = loadable_path(target, model_format)
    size = loadable.stat().st_size if loadable.is_file() else _dir_size(target)

    warnings: list[str] = []
    haystack = f"{model_display_name(target, model_format)} {target.name}".lower()
    if "rerank" in haystack:
        warnings.append("Looks like a reranker model; it will answer, but poorly.")
    if "embed" in haystack:
        warnings.append("Looks like an embedding model; it cannot hold a conversation.")

    return LocalModel(
        path=target,
        format=model_format,
        name=model_display_name(target, model_format),
        size_bytes=size,
        unsupported_reason=reason,
        chat_capable=capability_reason is None,
        warnings=tuple(warnings),
    )
