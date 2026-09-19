"""In-process Qwen3-ASR and Marlin — no HTTP model sidecars.

Loaded lazily into the API process from MODELS_DIR snapshots. Only one heavy
family is kept resident at a time to bound memory (same idea as the old
local-models engine, without the network hop).
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.log import get_logger
from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path

logger = get_logger("MultimodalRuntime")

# Match prior Marlin service defaults for video decoding.
os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "pyav")
os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")
os.environ.setdefault("FPS", "2.0")
os.environ.setdefault("FPS_MAX_FRAMES", "240")
os.environ.setdefault("FPS_MIN_FRAMES", "4")


class MultimodalRuntime:
    """Lazy Qwen3-ASR / Marlin loaded inside the API process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._device: str | None = None
        self._asr_model = None
        self._asr_processor = None
        self._asr_path: Path | None = None
        self._aligner_model = None
        self._aligner_processor = None
        self._marlin_model = None

    @property
    def device(self) -> str:
        if self._device is None:
            from app.core.inference_device import resolve_torch_device

            self._device = resolve_torch_device()
        return self._device

    def status(self) -> dict[str, Any]:
        return {
            "mode": "in_process",
            "device": self.device,
            "models_ready": {
                "asr": is_hf_snapshot_ready(multimodal_model_path("asr")),
                "marlin": is_hf_snapshot_ready(multimodal_model_path("marlin")),
            },
            "loaded": {
                "asr": self._asr_model is not None,
                "marlin": self._marlin_model is not None,
            },
        }

    def _unload_except(self, keep: str) -> None:
        changed = False
        if keep != "asr" and self._asr_model is not None:
            self._asr_model = None
            self._asr_processor = None
            self._asr_path = None
            self._aligner_model = None
            self._aligner_processor = None
            changed = True
        if keep != "marlin" and self._marlin_model is not None:
            self._marlin_model = None
            changed = True
        if changed:
            gc.collect()
            try:
                from app.services.local_models import release_accelerator_memory

                release_accelerator_memory()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def _unload_ggufs(self) -> None:
        """Exclusive residency: free chat/embed/rerank before HF multimodal loads."""
        try:
            from app.services.local_models import local_gguf_reranker, local_llama_runtime

            local_llama_runtime.unload()
            local_gguf_reranker.unload()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("GGUF unload before multimodal skipped: %s", exc)

    @staticmethod
    def _record_load(kind: str, started: float) -> None:
        """Feed HF loads into the shared model-load clock (see local_models)."""
        try:
            from app.services.local_models import model_load_clock

            model_load_clock.record(kind, time.perf_counter() - started)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def unload(self, family: str | None = None) -> dict[str, Any]:
        family = family.lower() if family else None
        valid = {None, "asr", "marlin"}
        if family not in valid:
            raise ValueError("family must be one of: asr, marlin")
        with self._lock:
            if family is None:
                self._unload_except("")
            elif family == "asr":
                self._asr_model = None
                self._asr_processor = None
                self._asr_path = None
                gc.collect()
            elif family == "marlin":
                self._marlin_model = None
                gc.collect()
            logger.info("Unloaded multimodal family: %s", family or "all")
            return self.status()

    # ---- Transcription (Qwen3-ASR) ----------------------------------------

    def _load_asr(self, model_path: Path) -> None:
        if self._asr_model is not None and self._asr_path == model_path:
            return
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        self._unload_ggufs()
        self._unload_except("asr")
        logger.info("Loading Qwen3-ASR from %s on %s", model_path, self.device)
        started = time.perf_counter()
        import torch

        dtype = torch.float32 if self.device == "cpu" else torch.float16
        self._asr_model = (
            AutoModelForMultimodalLM.from_pretrained(
                str(model_path), dtype=dtype, low_cpu_mem_usage=True
            )
            .to(self.device)
            .eval()
        )
        self._asr_processor = AutoProcessor.from_pretrained(str(model_path))
        self._asr_path = model_path
        self._record_load("asr", started)
        logger.info("Qwen3-ASR loaded")

    def _load_aligner(self, model_path: Path) -> None:
        """The forced aligner rides along with the ASR model (same layout, same device)."""
        if self._aligner_model is not None:
            return
        from transformers import AutoProcessor
        from transformers.models.qwen3_asr import Qwen3ASRForTokenClassification

        logger.info("Loading Qwen3 forced aligner from %s", model_path)
        started = time.perf_counter()
        self._aligner_model = (
            Qwen3ASRForTokenClassification.from_pretrained(
                str(model_path), dtype=self._asr_model.dtype, low_cpu_mem_usage=True
            )
            .to(self.device)
            .eval()
        )
        self._aligner_processor = AutoProcessor.from_pretrained(str(model_path))
        self._record_load("aligner", started)

    def _resolve_ffmpeg_bins(self) -> tuple[str | None, str | None]:
        """Locate system ``ffmpeg`` / ``ffprobe`` (PATH + common install dirs).

        GUI-launched apps on macOS often miss Homebrew's ``/opt/homebrew/bin``
        even when the tools are installed — search those dirs explicitly.
        """
        import shutil

        extras = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            str(Path.home() / "bin"),
        ]
        if process_path := os.environ.get("PATH", ""):
            search_path = os.pathsep.join([process_path, *extras])
        else:
            search_path = os.pathsep.join(extras)

        ffmpeg = shutil.which("ffmpeg", path=search_path)
        ffprobe = shutil.which("ffprobe", path=search_path)
        if not ffmpeg:
            for d in extras:
                cand = Path(d) / "ffmpeg"
                if cand.is_file() and os.access(cand, os.X_OK):
                    ffmpeg = str(cand)
                    break
        if not ffprobe:
            for d in extras:
                cand = Path(d) / "ffprobe"
                if cand.is_file() and os.access(cand, os.X_OK):
                    ffprobe = str(cand)
                    break
        # pydub needs both; treat as unavailable if either is missing.
        if ffmpeg and ffprobe:
            return ffmpeg, ffprobe
        return None, None

    def _load_audio_mono_16k_ffmpeg(self, audio_path: str, ffmpeg: str, ffprobe: str):
        """Decode via system ffmpeg/ffprobe + pydub (preferred when available)."""
        import numpy as np
        from pydub import AudioSegment

        # pydub's mediainfo always spawns a bare "ffprobe" name (PATH lookup).
        # Converter accepts an absolute path; put the bin dir on PATH for probe.
        bin_dir = str(Path(ffmpeg).resolve().parent)
        path_now = os.environ.get("PATH", "")
        if bin_dir not in path_now.split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + path_now
        AudioSegment.converter = ffmpeg

        segment = AudioSegment.from_file(audio_path)
        segment = segment.set_frame_rate(16000).set_channels(1)
        samples = np.array(segment.get_array_of_samples(), dtype=np.float32)
        if segment.sample_width == 2:
            samples /= 32768.0
        elif segment.sample_width == 4:
            samples /= 2147483648.0
        elif segment.sample_width == 1:
            samples = (samples - 128.0) / 128.0
        return samples

    def _load_audio_mono_16k_pyav(self, audio_path: str):
        """Decode via PyAV (bundled FFmpeg libs — no system binary required)."""
        import av
        import numpy as np

        chunks: list = []
        with av.open(audio_path) as container:
            stream = next(iter(container.streams.audio), None)
            if stream is None:
                raise RuntimeError(f"No audio stream in {audio_path}")
            resampler = av.audio.resampler.AudioResampler(
                format="flt",
                layout="mono",
                rate=16000,
            )
            for frame in container.decode(stream):
                for out_frame in resampler.resample(frame):
                    arr = out_frame.to_ndarray()
                    chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
            for out_frame in resampler.resample(None):
                arr = out_frame.to_ndarray()
                chunks.append(np.asarray(arr, dtype=np.float32).reshape(-1))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _load_audio_mono_16k(self, audio_path: str):
        """Decode audio to mono float32 @ 16 kHz.

        Prefers system ``ffmpeg``/``ffprobe`` when found; otherwise falls back to
        PyAV so packaged installs work without Homebrew.
        """
        ffmpeg, ffprobe = self._resolve_ffmpeg_bins()
        if ffmpeg and ffprobe:
            try:
                audio = self._load_audio_mono_16k_ffmpeg(audio_path, ffmpeg, ffprobe)
                logger.info("Audio decoded with system ffmpeg (%s)", ffmpeg)
                return audio
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "System ffmpeg decode failed (%s); falling back to PyAV: %s",
                    ffmpeg,
                    exc,
                )
        else:
            logger.info("System ffmpeg/ffprobe not found; decoding audio with PyAV")
        return self._load_audio_mono_16k_pyav(audio_path)

    def transcribe_audio_path(self, audio_path: str) -> str:
        """Transcribe with Qwen3-ASR on the engine this machine has.

        Apple Silicon runs it on the GPU through MLX and never loads the torch
        model, so no accelerator memory is taken from the chat model; every
        other platform goes through transformers.
        """
        from app.core.config import settings
        from app.services import asr_engine

        choice = asr_engine.choose(
            multimodal_model_path("asr").parent,
            preferred_engine=settings.ASR_ENGINE,
        )
        if not choice.ready:
            raise RuntimeError(
                f"Qwen3-ASR is not downloaded ({choice.reason}). "
                "Download the media models on the Models page."
            )
        if choice.engine != asr_engine.ENGINE_MLX:
            return self._transcribe_with_transformers(choice.model_path, audio_path)

        aligner = multimodal_model_path("aligner")
        speakers = self._diarizer_ready() and is_hf_snapshot_ready(aligner)
        if settings.ASR_SPEAKERS and not speakers:
            logger.info("Speaker labels skipped: aligner/diarizer not downloaded")
        with self._lock:
            self._unload_except("")
        transcript = asr_engine.transcribe_with_mlx(
            choice.model_path,
            audio_path,
            language=settings.ASR_LANGUAGE,
            aligner_path=aligner if speakers else None,
        )
        if not speakers or not transcript.words:
            return transcript.text
        turns = self._speaker_turns(self._load_audio_mono_16k(audio_path))
        if not turns:
            # The transcript is already in hand; labels are the optional half.
            return transcript.text
        return asr_engine.label_speakers(transcript, turns)

    def _diarizer_ready(self) -> bool:
        from app.core.config import settings

        return bool(settings.ASR_SPEAKERS) and is_hf_snapshot_ready(
            multimodal_model_path("diarizer")
        )

    def _speaker_turns(self, audio) -> list:
        """Who spoke when, or [] when diarization is off, missing, or fails."""
        from app.core.config import settings
        from app.services import asr_engine

        if not self._diarizer_ready():
            return []
        try:
            turns = asr_engine.speaker_turns(
                audio,
                16000,
                multimodal_model_path("diarizer"),
                step=settings.ASR_DIARIZE_STEP,
                max_speakers=settings.ASR_MAX_SPEAKERS,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Speaker labels skipped: %s", exc)
            return []
        logger.info("Speaker labels: %d speakers", len({t.speaker for t in turns}))
        return turns

    def _transcribe_with_transformers(self, model_path: Path, audio_path: str) -> str:
        from app.core.config import settings
        from app.services.asr_engine import language_name

        from app.services import asr_engine

        aligner = multimodal_model_path("aligner")
        speakers = self._diarizer_ready() and is_hf_snapshot_ready(aligner)
        if settings.ASR_SPEAKERS and not speakers:
            logger.info("Speaker labels skipped: aligner/diarizer not downloaded")
        with self._lock:
            self._load_asr(model_path)
            assert self._asr_model is not None and self._asr_processor is not None
            audio = self._load_audio_mono_16k(audio_path)
            if audio.size == 0:
                return ""
            if not speakers:
                return self._asr_generate(audio)
            # Same recipe as the MLX library: transcribe and align 30 s
            # chunks, then attribute the timed words to pyannote's turns.
            self._load_aligner(aligner)
            texts: list[str] = []
            words: list[asr_engine.Word] = []
            for chunk, offset in asr_engine.split_audio_into_chunks(audio, 16000):
                text = self._asr_generate(chunk)
                if not text:
                    continue
                texts.append(text)
                words += asr_engine.align_with_transformers(
                    self._aligner_processor, self._aligner_model, chunk, 16000, text, offset
                )
            transcript = asr_engine.Transcript(" ".join(texts), words)
            turns = self._speaker_turns(audio)
        return asr_engine.label_speakers(transcript, turns) if turns else transcript.text

    def _asr_generate(self, audio) -> str:
        """Run the loaded transformers Qwen3-ASR over one mono 16 kHz clip."""
        from app.core.config import settings
        from app.services.asr_engine import language_name

        import torch

        request = {"audio": audio, "sampling_rate": 16000}
        if language_name(settings.ASR_LANGUAGE):
            request["language"] = language_name(settings.ASR_LANGUAGE)
        inputs = self._asr_processor.apply_transcription_request(**request).to(
            self._asr_model.device, self._asr_model.dtype
        )
        # No fixed cap: the bound scales with the recording so a long
        # lecture is never cut mid-sentence (speech is ~3 tokens/s).
        seconds = audio.size / 16000
        with torch.no_grad():
            output_ids = self._asr_model.generate(
                **inputs, max_new_tokens=int(seconds * 8) + 256
            )
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        text = self._asr_processor.decode(generated, return_format="transcription_only")[0]
        return (text or "").strip()

    # ---- Marlin -----------------------------------------------------------

    @staticmethod
    def _patch_video_decoder() -> None:
        from transformers.video_processing_utils import BaseVideoProcessor
        from transformers.video_utils import load_video

        def _fetch_videos_pyav(self, video_url_or_urls, sample_indices_fn=None):
            if isinstance(video_url_or_urls, list):
                return list(
                    zip(
                        *[
                            self.fetch_videos(x, sample_indices_fn=sample_indices_fn)
                            for x in video_url_or_urls
                        ]
                    )
                )
            return load_video(
                video_url_or_urls, backend="pyav", sample_indices_fn=sample_indices_fn
            )

        BaseVideoProcessor.fetch_videos = _fetch_videos_pyav

    def _load_marlin(self) -> None:
        if self._marlin_model is not None:
            return
        path = multimodal_model_path("marlin")
        if not is_hf_snapshot_ready(path):
            raise RuntimeError(
                f"Marlin model not found at {path}. Download the media models on the Models page."
            )
        from transformers import AutoModelForCausalLM

        self._unload_ggufs()
        self._unload_except("marlin")
        self._patch_video_decoder()
        device = self.device
        from app.core.inference_device import prepare_qwen3_5_inference, resolve_torch_dtype

        dtype = resolve_torch_dtype(device)
        try:
            prepare_qwen3_5_inference(device)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Qwen3.5 prepare skipped: %s", exc)
        model_path = str(path)
        logger.info("Loading Marlin from %s on %s (%s)", model_path, device, dtype)
        started = time.perf_counter()
        self._marlin_model = (
            AutoModelForCausalLM.from_pretrained(
                model_path,
                trust_remote_code=True,
                dtype=dtype,
                low_cpu_mem_usage=True,
            )
            .to(device)
            .eval()
        )
        self._record_load("marlin", started)

    def caption_video_path(self, video_path: str) -> dict[str, Any]:
        with self._lock:
            self._load_marlin()
            assert self._marlin_model is not None
            started = time.perf_counter()
            result = self._marlin_model.caption(video_path)
            return {
                "scene": result.get("scene", ""),
                "events": result.get("events", []),
                "elapsed_seconds": time.perf_counter() - started,
            }


multimodal_runtime = MultimodalRuntime()
