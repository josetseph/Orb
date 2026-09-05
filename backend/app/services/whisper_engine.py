"""Pick and run a Whisper backend: MLX on Apple Silicon, transformers elsewhere.

Two findings drive this module, both measured in the sibling
local-transcription-service project:

* **CTranslate2 and PyTorch have no Metal path**, so "auto" device selection on
  a Mac quietly resolves to CPU. ``mlx-whisper`` runs the same architecture on
  the GPU and transcribed an 81-minute recording in 26 minutes against 96 —
  while using 6% CPU instead of 420%, so the machine stays usable.
* **large-v3-turbo is not a free speed-up on real audio.** Its decoder is cut
  from 32 layers to 4, which barely moves WER on clean read speech but makes it
  invent text in low-signal stretches: repetition loops, foreign script, and
  ~20% more words than large-v3 on identical distant-mic input. Orb shipped
  turbo as the default; this module ships large-v3 and leaves turbo opt-in.

Engine selection follows the same order as that project: an explicit choice
wins, then the on-disk model's format (which states unambiguously what can read
it), then the platform — and in automatic mode only an *installed* engine is
ever chosen, so a bundle the machine cannot run produces a precise error rather
than a silent substitution.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from app.core.log import get_logger

logger = get_logger("MultimodalRuntime")

ENGINE_MLX = "mlx"
ENGINE_TRANSFORMERS = "transformers"
ENGINES = (ENGINE_MLX, ENGINE_TRANSFORMERS)

#: Repo per engine. large-v3, not turbo — see the module docstring.
DEFAULT_REPO = {
    ENGINE_MLX: "mlx-community/whisper-large-v3-mlx",
    ENGINE_TRANSFORMERS: "openai/whisper-large-v3",
}
#: Folder name under MODELS_DIR for each engine's default.
DEFAULT_LOCAL_DIR = {
    ENGINE_MLX: "whisper-large-v3-mlx",
    ENGINE_TRANSFORMERS: "whisper-large-v3",
}

_MLX_MARKERS = ("weights.npz", "model.npz")


@dataclass(frozen=True)
class WhisperChoice:
    """The engine and model Orb will transcribe with, and why."""

    engine: str
    model_path: Path | None
    repo_id: str | None
    reason: str

    @property
    def ready(self) -> bool:
        return self.model_path is not None


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _installed(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def engine_available(engine: str) -> bool:
    if engine == ENGINE_MLX:
        # mlx-whisper is Apple-Silicon only; the wheel simply is not installed
        # elsewhere, but guard the platform too so a stray install cannot win.
        return is_apple_silicon() and _installed("mlx_whisper")
    return _installed("transformers")


def is_mlx_bundle(path: Path) -> bool:
    """An MLX Whisper folder: config.json beside npz/safetensors weights."""
    if not path.is_dir() or not (path / "config.json").exists():
        return False
    if any((path / name).exists() for name in _MLX_MARKERS):
        return True
    # mlx-community publishes safetensors too; the repo name is then the hint.
    return "mlx" in path.name.lower()


def is_transformers_bundle(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_config = (path / "config.json").exists()
    has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.bin"))
    return has_config and has_weights


def detect_engine_for(path: Path) -> str | None:
    """Which engine can read this folder, from its layout alone."""
    if is_mlx_bundle(path):
        return ENGINE_MLX
    if is_transformers_bundle(path):
        return ENGINE_TRANSFORMERS
    return None


def _candidate_dirs(models_dir: Path) -> list[Path]:
    """Whisper folders already on disk, newest naming first.

    An existing install keeps working: someone who downloaded turbo before this
    change should not lose transcription because the default moved.
    """
    names = [
        DEFAULT_LOCAL_DIR[ENGINE_MLX],
        DEFAULT_LOCAL_DIR[ENGINE_TRANSFORMERS],
        "whisper-large-v3-turbo",
    ]
    seen: list[Path] = []
    for name in names:
        candidate = models_dir / name
        if candidate.is_dir():
            seen.append(candidate)
    # Any other whisper-ish folder the user placed there.
    for child in sorted(models_dir.glob("*whisper*")):
        if child.is_dir() and child not in seen:
            seen.append(child)
    return seen


def choose(
    models_dir: Path,
    *,
    preferred_engine: str | None = None,
    explicit_path: Path | None = None,
) -> WhisperChoice:
    """Decide engine + model. ``preferred_engine`` of ``None``/``auto`` means detect."""
    requested = (preferred_engine or "auto").lower().strip()
    if requested not in ("auto", "") and requested not in ENGINES:
        raise ValueError(
            f"Unknown Whisper engine {requested!r}; choose from {', '.join(ENGINES)} or 'auto'."
        )

    if explicit_path is not None:
        detected = detect_engine_for(explicit_path)
        if detected is None:
            raise ValueError(f"{explicit_path} is not a Whisper model folder.")
        engine = requested if requested in ENGINES else detected
        if not engine_available(engine):
            raise RuntimeError(_missing_engine_message(engine))
        return WhisperChoice(engine, explicit_path, None, "explicit model path")

    # An explicit engine is never silently substituted.
    if requested in ENGINES:
        if not engine_available(requested):
            raise RuntimeError(_missing_engine_message(requested))
        for path in _candidate_dirs(models_dir):
            if detect_engine_for(path) == requested:
                return WhisperChoice(requested, path, None, f"{requested} model on disk")
        return WhisperChoice(
            requested, None, DEFAULT_REPO[requested], f"{requested} requested; needs download"
        )

    # Automatic: a model already on disk states which engine can read it.
    for path in _candidate_dirs(models_dir):
        detected = detect_engine_for(path)
        if detected and engine_available(detected):
            return WhisperChoice(detected, path, None, f"{path.name} on disk")

    preference = (
        (ENGINE_MLX, ENGINE_TRANSFORMERS)
        if is_apple_silicon()
        else (ENGINE_TRANSFORMERS, ENGINE_MLX)
    )
    for engine in preference:
        if engine_available(engine):
            return WhisperChoice(
                engine, None, DEFAULT_REPO[engine], f"platform default ({engine})"
            )
    # Name the engine this machine ought to use, so the error that follows
    # points at the right package.
    return WhisperChoice(
        preference[0], None, DEFAULT_REPO[preference[0]], "no engine installed"
    )


def _missing_engine_message(engine: str) -> str:
    if engine == ENGINE_MLX:
        if not is_apple_silicon():
            return (
                "The mlx Whisper engine runs on Apple Silicon only. "
                "Use the transformers engine on this machine."
            )
        return (
            "The mlx Whisper engine needs mlx-whisper: "
            "pip install 'mlx-whisper>=0.4'"
        )
    return (
        "The transformers Whisper engine needs the multimedia extras: "
        "pip install -r backend/requirements-multimodal.txt"
    )


def transcribe_with_mlx(model_path: Path, audio_path: str, *, language: str | None) -> str:
    """Transcribe on the Apple GPU. ``language=None`` lets Whisper detect it."""
    import mlx_whisper  # type: ignore

    logger.info("Transcribing with mlx-whisper (%s)", model_path.name)
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=str(model_path),
        language=language,
        # Whisper's own guards against the degenerate loops that turbo produced.
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
    )
    return (result.get("text") or "").strip()
