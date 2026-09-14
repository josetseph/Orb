"""In-process Whisper and Marlin — no HTTP model sidecars.

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


def _resolve_torch_device() -> str:
    from app.core.inference_device import resolve_torch_device

    return resolve_torch_device()


def _resolve_torch_dtype(device: str):
    from app.core.inference_device import resolve_torch_dtype

    return resolve_torch_dtype(device)


def _prepare_qwen35(device: str) -> None:
    try:
        from app.core.inference_device import prepare_qwen3_5_inference

        prepare_qwen3_5_inference(device)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Qwen3.5 prepare skipped: %s", exc)


class MultimodalRuntime:
    """Lazy Whisper / Marlin loaded inside the API process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._device: str | None = None
        self._whisper_model = None
        self._whisper_processor = None
        self._marlin_model = None

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = _resolve_torch_device()
        return self._device

    def status(self) -> dict[str, Any]:
        return {
            "mode": "in_process",
            "device": self.device,
            "models_ready": {
                "whisper": is_hf_snapshot_ready(multimodal_model_path("whisper")),
                "marlin": is_hf_snapshot_ready(multimodal_model_path("marlin")),
            },
            "loaded": {
                "whisper": self._whisper_model is not None,
                "marlin": self._marlin_model is not None,
            },
        }

    def _unload_except(self, keep: str) -> None:
        changed = False
        if keep != "whisper" and self._whisper_model is not None:
            self._whisper_model = None
            self._whisper_processor = None
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
        valid = {None, "whisper", "marlin"}
        if family not in valid:
            raise ValueError("family must be one of: whisper, marlin")
        with self._lock:
            if family is None:
                self._unload_except("")
            elif family == "whisper":
                self._whisper_model = None
                self._whisper_processor = None
                gc.collect()
            elif family == "marlin":
                self._marlin_model = None
                gc.collect()
            logger.info("Unloaded multimodal family: %s", family or "all")
            return self.status()

    # ---- Whisper ----------------------------------------------------------

    def _load_whisper(self) -> None:
        if self._whisper_model is not None:
            return
        path = multimodal_model_path("whisper")
        if not is_hf_snapshot_ready(path):
            raise RuntimeError(
                f"Whisper model not found at {path}. Download the media models on the Models page."
            )
        if "turbo" in path.name.lower():
            # Measured on distant-mic audio: turbo's shallow decoder invents
            # text in low-signal stretches where large-v3 stays quiet.
            logger.warning(
                "Using %s — the turbo decoder hallucinates on noisy audio. "
                "Download whisper-large-v3 on the Models page for reliable transcripts.",
                path.name,
            )
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        self._unload_ggufs()
        self._unload_except("whisper")
        model_path = str(path)
        logger.info("Loading Whisper from %s on %s", model_path, self.device)
        started = time.perf_counter()
        import torch

        dtype = torch.float32 if self.device == "cpu" else torch.float16
        self._whisper_model = (
            AutoModelForSpeechSeq2Seq.from_pretrained(
                model_path,
                torch_dtype=dtype,
                low_cpu_mem_usage=True,
            )
            .to(self.device)
            .eval()
        )
        self._whisper_processor = AutoProcessor.from_pretrained(model_path)
        self._record_load("whisper", started)
        logger.info("Whisper loaded")

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
        # Back-compat attribute used by some pydub call sites.
        AudioSegment.ffmpeg = ffmpeg  # type: ignore[attr-defined]

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
        """Transcribe with the best engine this machine has.

        On Apple Silicon with mlx-whisper installed this runs on the GPU and
        never loads the PyTorch model at all, so no accelerator memory is taken
        from the resident chat/embed model.
        """
        from app.core.config import settings
        from app.services import whisper_engine

        choice = whisper_engine.choose(
            multimodal_model_path("whisper").parent,
            preferred_engine=settings.WHISPER_ENGINE,
        )
        if choice.engine == whisper_engine.ENGINE_MLX and choice.ready:
            # mlx-whisper holds its own weights; drop any resident torch model
            # first so both are not in memory at once.
            with self._lock:
                self._unload_except("")
            return whisper_engine.transcribe_with_mlx(
                choice.model_path, audio_path, language=settings.WHISPER_LANGUAGE
            )
        return self._transcribe_with_transformers(audio_path)

    def _transcribe_with_transformers(self, audio_path: str) -> str:
        from app.core.config import settings

        with self._lock:
            self._load_whisper()
            assert self._whisper_model is not None and self._whisper_processor is not None
            audio = self._load_audio_mono_16k(audio_path)
            if audio.size == 0:
                return ""
            model_dtype = next(self._whisper_model.parameters()).dtype
            input_features = self._whisper_processor(
                audio, sampling_rate=16000, return_tensors="pt"
            ).input_features.to(device=self.device, dtype=model_dtype)
            generated_ids = self._whisper_model.generate(
                input_features,
                generation_config=self._whisper_model.generation_config,
                # None lets Whisper detect the language; forcing "en" on
                # non-English audio is a known source of invented transcripts.
                language=settings.WHISPER_LANGUAGE,
                task="transcribe",
            )
            return self._whisper_processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0]

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
        dtype = _resolve_torch_dtype(device)
        _prepare_qwen35(device)
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
