"""Speech to text with Phonon-2, and who-said-what from pyannote.

Phonon-2 is FermionResearch's quantized NVIDIA Parakeet TDT 0.6B v3: a
164 MB download, English only, run by the ``fermion-research`` package on MLX
(Apple Silicon) or its CPU engine (Linux, Windows, Intel Macs). It replaced
Qwen3-ASR 1.7B in October 2026. On an M3 with a far-field lecture, scored as
word agreement with a commercial transcript:

- 10-minute slice: 79.3% against 81.3%
- 88-minute lecture: 73.7% against 75.5%
- 88 minutes with word timings: 181 s against about 17 minutes
- download: 164 MB against 4.7 GB plus a 1.8 GB aligner

Run-to-run noise on the slice is ~3 points, so the cost is ~2 points on long
recordings. Phonon never looped or switched language (Qwen on auto-detect
wrote Hindi and Chinese into English lectures), and its decoder gives
punctuated word timings directly, so no forced aligner or punctuation repair
is needed. Speaker labels come from the diarizer and those timings.

Model files live under ``MODELS_DIR/fermion`` (fermion's own cache layout,
pointed there with ``FERMION_CACHE_DIR`` inside this process). They are only
ever downloaded from the Models page; transcription refuses rather than fetch.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.log import get_logger

logger = get_logger("MultimodalRuntime")

#: What ``fermion.load_speech`` takes.
PHONON_MODEL = "phonon-2"
#: Byte-identical to the gated pyannote/speaker-diarization-community-1,
#: without the gate: no HuggingFace account or token needed.
DIARIZER_REPO = "pyannote-community/speaker-diarization-community-1"
DIARIZER_DIR = "pyannote-community-1"


@dataclass
class Word:
    word: str
    start: float
    end: float


@dataclass
class Transcript:
    text: str
    words: list[Word]


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


# ── Phonon-2 ─────────────────────────────────────────────────────────────────


def fermion_cache_dir(models_dir: Path) -> Path:
    return models_dir / "fermion"


def _point_fermion_at(models_dir: Path) -> None:
    """Keep fermion's downloads with Orb's other models (its default is ~/.cache)."""
    os.environ["FERMION_CACHE_DIR"] = str(fermion_cache_dir(models_dir))


def _phonon_profile():
    """``(repo, key, pin)`` for Phonon-2 as fermion publishes it."""
    from fermion.transcribe import _resolve  # pinned fermion-research version

    repo, key, pin, _local = _resolve(PHONON_MODEL)
    return repo, key, pin


def phonon_dir(models_dir: Path) -> Path:
    """Where the unpacked Phonon-2 profile lives (it may not exist yet)."""
    from fermion._speech import fetch

    _point_fermion_at(models_dir)
    repo, _key, pin = _phonon_profile()
    return fetch.profile_dir(repo, pin["unpack_dir"])


def is_phonon_ready(models_dir: Path) -> bool:
    """Downloaded and unpacked. Never touches the network."""
    try:
        d = phonon_dir(models_dir)
    except Exception:  # pylint: disable=broad-exception-caught
        return False  # fermion-research not installed
    return (d / "config.json").is_file() and (d / "model.fermion").is_file()


def _unwrap(call, what: str):
    """fermion raises SystemExit for bad input, a CLI convention; in the
    backend that would pass ``except Exception`` and end the worker."""
    try:
        return call()
    except SystemExit as exc:
        raise RuntimeError(f"{what}: {exc}") from None


def download_phonon(models_dir: Path) -> Path:
    """Fetch and unpack Phonon-2 (164 MB) into MODELS_DIR/fermion; idempotent."""
    from fermion._speech import fetch

    _point_fermion_at(models_dir)
    repo, key, pin = _phonon_profile()
    return _unwrap(lambda: fetch.ensure(repo, key, pin, quiet=True), "Phonon-2 download")


def load_phonon(models_dir: Path):
    """Load Phonon-2 on this machine's engine (MLX on Apple Silicon, CPU elsewhere)."""
    if not is_phonon_ready(models_dir):
        raise RuntimeError(
            "Phonon-2 is not downloaded. Download the media models on the Models page."
        )
    import fermion

    _point_fermion_at(models_dir)
    return _unwrap(lambda: fermion.load_speech(PHONON_MODEL), "Phonon-2 load")


def transcribe_with_phonon(speech, wav_path: str) -> Transcript:
    """Punctuated text and word timings for a 16 kHz mono WAV.

    ``truncated`` means a segment ran out of token budget and speech is
    missing: a failure, never a short transcript passed off as complete.
    """
    result = _unwrap(lambda: speech.transcribe_detailed(wav_path), "Phonon-2")
    if result.truncated:
        raise RuntimeError(
            "Phonon-2 stopped short on part of this recording (truncated), so the "
            "transcript would be missing speech"
        )
    words = [
        Word(str(w["text"]), float(w["start"]), float(w["end"]))
        for w in (result.words or [])
        if result.timed
    ]
    return Transcript((result.text or "").strip(), words)


# ── Speaker labels and transcript lines ──────────────────────────────────────


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


# A transcript line: a sentence, a pause, or this many seconds, whichever first.
MAX_LINE_SECONDS = 12.0
MAX_LINE_GAP = 1.0
# Short cues ("Yes.", "Okay.") read better joined to their neighbour.
JOIN_GAP = 1.5
JOIN_CHARS = 140
_STAMP_RE = re.compile(r"^\[\d+:\d{2}\] ", re.M)


def timed_lines(transcript: Transcript, turns: list[Turn]) -> str:
    """``[MM:SS] Speaker 1: …`` lines; without turns, ``[MM:SS] …``.

    Words are attributed by midpoint and speakers numbered by first appearance
    rather than pyannote's arbitrary SPEAKER_xx ids. A line ends on . ? !, a
    pause, a change of speaker, or ``MAX_LINE_SECONDS``. Minutes do not roll
    over into hours, so a stamp reads straight off a player. Without word
    timings the plain transcript comes back unchanged.
    """
    if not transcript.words:
        return transcript.text
    names: dict[str, str] = {}
    cues: list[tuple[float, float, str, list[str]]] = []  # start, end, speaker, words
    for word in transcript.words:
        speaker = (speaker_at(turns, (word.start + word.end) / 2) or "") if turns else ""
        if speaker and speaker not in names:
            names[speaker] = f"Speaker {len(names) + 1}"
        last = cues[-1] if cues else None
        if (
            last is None
            or speaker != last[2]
            or last[3][-1].endswith((".", "?", "!"))
            or word.start - last[1] > MAX_LINE_GAP
            or word.end - last[0] > MAX_LINE_SECONDS
        ):
            cues.append((word.start, word.end, speaker, [word.word]))
        else:
            cues[-1] = (last[0], word.end, speaker, last[3] + [word.word])

    lines: list[str] = []
    prev: tuple[float, float, str, list[str]] | None = None
    for cue in cues:
        text = " ".join(cue[3])
        if prev and cue[2] == prev[2] and cue[0] - prev[1] < JOIN_GAP and len(lines[-1]) < JOIN_CHARS:
            lines[-1] += " " + text
        else:
            minutes, seconds = divmod(int(max(cue[0], 0.0)), 60)
            who = f"{names[cue[2]]}: " if cue[2] else ""
            lines.append(f"[{minutes:02d}:{seconds:02d}] {who}{text}")
        prev = cue
    # A blank line between entries: Markdown joins single-newline lines into
    # one paragraph, so every viewer but a raw editor showed a wall of text.
    return "\n\n".join(lines)


def untimed(text: str) -> str:
    """The transcript as the summariser reads it: no stamps, one paragraph per
    run of a speaker. Timestamps cost about half as many tokens again and a
    summary has no use for them."""
    out: list[str] = []
    who_before = None
    for line in _STAMP_RE.sub("", text or "").splitlines():
        if not line.strip():  # entries are blank-line separated
            continue
        who, sep, said = line.partition(": ")
        if sep and who.startswith("Speaker ") and who == who_before:
            out[-1] += " " + said
        else:
            out.append(line)
            who_before = who if sep and who.startswith("Speaker ") else None
    return "\n\n".join(o for o in out if o.strip())
