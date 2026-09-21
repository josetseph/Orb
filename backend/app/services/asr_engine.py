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

import json
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
#: Word timings; speaker labels split text on them. Same aligner in the two
#: layouts, like the ASR model itself.
ALIGNER_REPO = {
    ENGINE_MLX: "Qwen/Qwen3-ForcedAligner-0.6B",
    ENGINE_TRANSFORMERS: "Qwen/Qwen3-ForcedAligner-0.6B-hf",
}
ALIGNER_DIR = {
    ENGINE_MLX: "qwen3-forced-aligner-0.6b",
    ENGINE_TRANSFORMERS: "qwen3-forced-aligner-0.6b-hf",
}
#: The aligner is rated for up to 5 minutes; 30 s chunks split at the
#: quietest point are what the MLX library uses, so both engines match.
MAX_CHUNK_SECONDS = 30.0
#: Byte-identical to the gated pyannote/speaker-diarization-community-1,
#: without the gate: no HuggingFace account or token needed.
DIARIZER_REPO = "pyannote-community/speaker-diarization-community-1"
DIARIZER_DIR = "pyannote-community-1"

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
        cfg = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    names = [cfg.get("model_type", ""), *(cfg.get("architectures") or [])]
    return any("qwen3asr" in str(n).lower().replace("_", "") for n in names)


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


@dataclass
class Word:
    word: str
    start: float
    end: float


@dataclass
class Transcript:
    text: str
    #: Empty unless a forced aligner ran.
    words: list[Word]


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


def transcribe_with_mlx(
    model_path: Path,
    audio_path: str,
    *,
    language: str | None,
    aligner_path: Path | None = None,
) -> Transcript:
    """Transcribe on the Apple GPU. ``language=None`` lets the model detect it.

    With ``aligner_path`` the library also emits one timed entry per word; the
    aligner strips punctuation, but the raw text keeps it token-for-token, so
    it is restored from there.
    """
    import mlx_qwen3_asr  # type: ignore

    logger.info("Transcribing with Qwen3-ASR via MLX (%s)", model_path.name)
    extra = {}
    if aligner_path is not None:
        extra = {"return_timestamps": True, "forced_aligner": str(aligner_path)}
    try:
        result = mlx_qwen3_asr.transcribe(
            audio_path,
            model=str(model_path),
            language=language_name(language),
            verbose=False,
            **extra,
        )
        text = (getattr(result, "text", "") or "").strip()
        words = [
            Word(str(e.get("text") or "").strip(), float(e.get("start") or 0.0), float(e.get("end") or 0.0))
            for e in (_as_dict(x) for x in (getattr(result, "segments", None) or []))
            if str(e.get("text") or "").strip()
        ]
        restore_punctuation(words, text.split())
        return Transcript(text, words)
    finally:
        # The weights are dropped with the call; give Metal its buffers back.
        try:
            import mlx.core as mx  # type: ignore

            mx.clear_cache()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def _bare(token: str) -> str:
    return "".join(ch for ch in token.lower() if ch.isalnum())


def restore_punctuation(words: list[Word], tokens: list[str]) -> None:
    """Give each aligned word the punctuated token it came from.

    The aligner strips punctuation; the text keeps it. The two sequences are
    nearly identical but not always the same length: over an 86-minute lecture
    the aligner returned 14 more words than the text had tokens, and an
    all-or-nothing length check then dropped every full stop in the recording
    — the note, and entity extraction after it, got one unbroken run of words.
    Matching runs keeps the punctuation everywhere the two agree.
    """
    from difflib import SequenceMatcher

    matcher = SequenceMatcher(
        None, [_bare(w.word) for w in words], [_bare(t) for t in tokens], autojunk=False
    )
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            words[block.a + offset].word = tokens[block.b + offset]


def _as_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return {}


def speaker_turns(
    audio,
    sample_rate: int,
    diarizer_path: Path,
    *,
    step: float,
    max_speakers: int | None,
) -> list[Turn]:
    """Who spoke when, from pyannote community-1 on the GPU when there is one.

    Measured on an M3 over a 10-minute lecture slice at step 2.0: 40 s on
    ``mps`` against 284 s on ``cpu``, with identical turns.

    The waveform is handed over decoded (Orb already has it as mono 16 kHz
    float32) so pyannote never needs torchcodec/ffmpeg of its own.
    """
    import warnings

    import torch
    from pyannote.audio import Pipeline

    from app.core.inference_device import resolve_torch_device

    # Emitted per chunk from pooling when a speaker is active for a single
    # frame; harmless, and it floods the log on a long lecture.
    warnings.filterwarnings("ignore", message=r"std\(\): degrees of freedom is <= 0")
    logger.info("Diarizing with %s (step %.1fs)", diarizer_path.name, step)
    # The folder's config.yaml resolves its $model/ sub-models relative to itself.
    pipeline = Pipeline.from_pretrained(str(diarizer_path / "config.yaml"))
    if pipeline is None:
        raise RuntimeError(f"Could not load the speaker pipeline at {diarizer_path}")
    try:
        pipeline.to(torch.device(resolve_torch_device()))
        # Fewer, wider windows are the one lever that speeds this up: ~95% of
        # the time is the per-window speaker-embedding pass.
        if step:
            pipeline._segmentation.step = step  # pylint: disable=protected-access
        kwargs = {"max_speakers": max_speakers} if max_speakers else {}
        waveform = torch.from_numpy(audio.astype("float32")).unsqueeze(0)
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kwargs)
        # pyannote 4.x returns DiarizeOutput; earlier versions a bare Annotation.
        annotation = getattr(output, "speaker_diarization", output)
        turns = [
            Turn(float(seg.start), float(seg.end), str(spk))
            for seg, _, spk in annotation.itertracks(yield_label=True)
        ]
        turns.sort(key=lambda t: t.start)
        return turns
    finally:
        del pipeline
        from app.services.local_models import release_accelerator_memory

        release_accelerator_memory()  # gc + the mps/cuda cache the pipeline filled


def speaker_at(turns: list[Turn], when: float) -> str | None:
    """Speaker active at ``when``; falls back to the nearest turn.

    Words routinely land in diarization gaps (breaths, short pauses), so an
    unlabelled word is worse than one attributed to its closest turn.
    """
    if not turns:
        return None
    for turn in turns:
        if turn.start <= when <= turn.end:
            return turn.speaker
    nearest = min(turns, key=lambda t: t.start - when if when < t.start else when - t.end)
    return nearest.speaker


def label_speakers(transcript: Transcript, turns: list[Turn]) -> str:
    """``Speaker 1: …`` paragraphs, one per run of the same speaker.

    Words are attributed by midpoint. Labels are numbered in order of first
    appearance rather than pyannote's arbitrary SPEAKER_xx ids. Without word
    timings or turns the plain transcript comes back unchanged.
    """
    if not transcript.words or not turns:
        return transcript.text
    names: dict[str, str] = {}
    lines: list[list[str]] = []
    current: str | None = None
    for word in transcript.words:
        speaker = speaker_at(turns, (word.start + word.end) / 2) or ""
        if speaker not in names:
            names[speaker] = f"Speaker {len(names) + 1}"
        if speaker != current or not lines:
            lines.append([f"{names[speaker]}:"])
            current = speaker
        lines[-1].append(word.word)
    return "\n\n".join(" ".join(line) for line in lines)


def split_audio_into_chunks(audio, sr: int, max_chunk_sec: float = MAX_CHUNK_SECONDS):
    """``(chunk, offset_seconds)`` pieces no longer than ``max_chunk_sec``.

    Long audio is bisected at the lowest-energy half-second window in the
    middle 60%, recursively, so cuts land in pauses rather than mid-word.
    Ported from mlx_qwen3_asr.chunking so the transformers engine chunks the
    same way.
    """
    import numpy as np

    if len(audio) / sr <= max_chunk_sec:
        return [(audio, 0.0)]
    n = len(audio)
    window = max(1, int(0.5 * sr))
    best, best_rms = n // 2, float("inf")
    for pos in range(int(n * 0.2), int(n * 0.8) - window, window // 2 or 1):
        rms = float(np.sqrt(np.mean(audio[pos : pos + window] ** 2)))
        if rms < best_rms:
            best, best_rms = pos + window // 2, rms
    if best <= 0 or best >= n:
        best = max(1, n // 2)
    left = split_audio_into_chunks(audio[:best], sr, max_chunk_sec)
    right = split_audio_into_chunks(audio[best:], sr, max_chunk_sec)
    return left + [(chunk, offset + best / sr) for chunk, offset in right]


def align_with_transformers(processor, model, audio, sr: int, text: str, offset: float = 0.0) -> list[Word]:
    """Word timings for ``text`` over ``audio`` from the transformers aligner.

    The processor's word list drops punctuation; ``restore_punctuation`` puts
    the text's spelling back wherever the two agree, as on the MLX path.
    """
    import torch

    inputs, word_lists = processor.prepare_forced_aligner_inputs(
        audio=audio, transcript=text, sampling_rate=sr
    )
    inputs = inputs.to(model.device, model.dtype)
    with torch.inference_mode():
        logits = model(**inputs).logits
    items = processor.decode_forced_alignment(
        logits=logits,
        input_ids=inputs["input_ids"],
        word_lists=word_lists,
        timestamp_token_id=model.config.timestamp_token_id,
    )[0]
    words = [
        Word(i["text"], float(i["start_time"]) + offset, float(i["end_time"]) + offset)
        for i in items
    ]
    restore_punctuation(words, text.split())
    return words
