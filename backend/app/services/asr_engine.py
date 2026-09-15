"""Pick and run the Qwen3-ASR transcription backend: MLX on Apple Silicon, transformers elsewhere.

Measured in the sibling local-transcription-service project on a 76-minute
distant-mic lecture (see qwen-vs-whisper-report.md at the repo root): Qwen3-ASR
1.7B through MLX ran at 5.6x realtime against Whisper large-v3's 2.5x, and where
Whisper lost 103 s of speech to blank output, hit six repetition loops and
emitted 107 duplicate segments, Qwen had none. On a hand-transcribed passage
both scored 11.3% WER. Orb therefore ships one transcriber, Qwen3-ASR, in the
two layouts it is published in:

* ``mlx`` — ``mlx-qwen3-asr`` on the Apple GPU, reading the original
  ``Qwen/Qwen3-ASR-1.7B`` layout.
* ``transformers`` — ``Qwen/Qwen3-ASR-1.7B-hf`` through PyTorch, for every other
  platform (CUDA or CPU). Marlin already needs this stack.

Engine selection: an explicit choice is never substituted. In automatic mode
the platform picks the engine, a model on disk in that engine's layout is used,
and only an *installed* engine is ever chosen, so a bundle the machine cannot
run produces a precise error rather than a silent swap.
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

#: Repo per engine — the same model in the layout each library reads.
DEFAULT_REPO = {
    ENGINE_MLX: "Qwen/Qwen3-ASR-1.7B",
    ENGINE_TRANSFORMERS: "Qwen/Qwen3-ASR-1.7B-hf",
}
#: Folder name under MODELS_DIR for each engine's default.
DEFAULT_LOCAL_DIR = {
    ENGINE_MLX: "qwen3-asr-1.7b",
    ENGINE_TRANSFORMERS: "qwen3-asr-1.7b-hf",
}

# The MLX library wants a language name, not an ISO code; transformers takes
# either. Unknown codes pass through.
LANGUAGE_NAMES = {
    "en": "English", "zh": "Chinese", "fr": "French", "de": "German",
    "es": "Spanish", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "pt": "Portuguese", "ru": "Russian", "ar": "Arabic",
}


def language_name(code: str | None) -> str | None:
    if not code:
        return None
    return LANGUAGE_NAMES.get(code.lower(), code)


@dataclass(frozen=True)
class AsrChoice:
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
        # mlx-qwen3-asr is Apple-Silicon only; guard the platform too so a
        # stray install elsewhere cannot win.
        return is_apple_silicon() and _installed("mlx_qwen3_asr")
    return _installed("transformers")


def is_asr_bundle(path: Path) -> bool:
    """A Qwen3-ASR folder: HF layout whose config names the Qwen3-ASR architecture."""
    config = path / "config.json"
    if not path.is_dir() or not config.exists():
        return False
    try:
        text = config.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return "qwen3asr" in text.replace("_", "").replace("-", "")


def detect_engine_for(path: Path) -> str | None:
    """Which engine can read this folder.

    Both layouts share a config, so the published name tells them apart: the
    transformers conversion is the ``-hf`` repo.
    """
    if not is_asr_bundle(path):
        return None
    return ENGINE_TRANSFORMERS if path.name.lower().endswith("-hf") else ENGINE_MLX


def _candidate_dirs(models_dir: Path) -> list[Path]:
    """Qwen3-ASR folders already on disk, preferred naming first."""
    names = [
        DEFAULT_LOCAL_DIR[ENGINE_MLX],
        DEFAULT_LOCAL_DIR[ENGINE_TRANSFORMERS],
        "qwen3-asr-0.6b",
        "qwen3-asr-0.6b-hf",
    ]
    seen: list[Path] = []
    for name in names:
        candidate = models_dir / name
        if candidate.is_dir():
            seen.append(candidate)
    for child in sorted(models_dir.glob("*qwen3-asr*")):
        if child.is_dir() and child not in seen:
            seen.append(child)
    return seen


def choose(
    models_dir: Path,
    *,
    preferred_engine: str | None = None,
    explicit_path: Path | None = None,
) -> AsrChoice:
    """Decide engine + model. ``preferred_engine`` of ``None``/``auto`` means detect."""
    requested = (preferred_engine or "auto").lower().strip()
    if requested not in ("auto", "") and requested not in ENGINES:
        raise ValueError(
            f"Unknown transcription engine {requested!r}; choose from {', '.join(ENGINES)} or 'auto'."
        )

    if explicit_path is not None:
        detected = detect_engine_for(explicit_path)
        if detected is None:
            raise ValueError(f"{explicit_path} is not a Qwen3-ASR model folder.")
        engine = requested if requested in ENGINES else detected
        if not engine_available(engine):
            raise RuntimeError(_missing_engine_message(engine))
        return AsrChoice(engine, explicit_path, None, "explicit model path")

    # An explicit engine is never silently substituted.
    if requested in ENGINES:
        if not engine_available(requested):
            raise RuntimeError(_missing_engine_message(requested))
        for path in _candidate_dirs(models_dir):
            if detect_engine_for(path) == requested:
                return AsrChoice(requested, path, None, f"{requested} model on disk")
        return AsrChoice(
            requested, None, DEFAULT_REPO[requested], f"{requested} requested; needs download"
        )

    preference = (
        (ENGINE_MLX, ENGINE_TRANSFORMERS)
        if is_apple_silicon()
        else (ENGINE_TRANSFORMERS, ENGINE_MLX)
    )
    for engine in preference:
        if not engine_available(engine):
            continue
        for path in _candidate_dirs(models_dir):
            if detect_engine_for(path) == engine:
                return AsrChoice(engine, path, None, f"{path.name} on disk")
        return AsrChoice(engine, None, DEFAULT_REPO[engine], f"platform default ({engine})")
    # Name the engine this machine ought to use, so the error that follows
    # points at the right package.
    return AsrChoice(
        preference[0], None, DEFAULT_REPO[preference[0]], "no engine installed"
    )


def _missing_engine_message(engine: str) -> str:
    if engine == ENGINE_MLX:
        if not is_apple_silicon():
            return (
                "The mlx transcription engine runs on Apple Silicon only. "
                "Use the transformers engine on this machine."
            )
        return (
            "The mlx transcription engine needs mlx-qwen3-asr: "
            "pip install 'mlx-qwen3-asr>=0.4'"
        )
    return (
        "The transformers transcription engine needs the multimedia extras: "
        "pip install -r backend/requirements-multimodal.txt"
    )


def transcribe_with_mlx(model_path: Path, audio_path: str, *, language: str | None) -> str:
    """Transcribe on the Apple GPU. ``language=None`` lets the model detect it."""
    import mlx_qwen3_asr  # type: ignore

    logger.info("Transcribing with Qwen3-ASR via MLX (%s)", model_path.name)
    try:
        result = mlx_qwen3_asr.transcribe(
            audio_path,
            model=str(model_path),
            language=language_name(language),
            verbose=False,
        )
        return (getattr(result, "text", "") or "").strip()
    finally:
        # The weights are dropped with the call; give Metal its buffers back.
        try:
            import mlx.core as mx  # type: ignore

            mx.clear_cache()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
