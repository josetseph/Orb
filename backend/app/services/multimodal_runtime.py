"""In-process Florence-2, Whisper, and Marlin — no HTTP model sidecars.

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
    """Lazy Florence / Whisper / Marlin loaded inside the API process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._device: str | None = None
        self._florence_model = None
        self._florence_processor = None
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
                "florence": is_hf_snapshot_ready(multimodal_model_path("florence")),
                "whisper": is_hf_snapshot_ready(multimodal_model_path("whisper")),
                "marlin": is_hf_snapshot_ready(multimodal_model_path("marlin")),
            },
            "loaded": {
                "florence": self._florence_model is not None,
                "whisper": self._whisper_model is not None,
                "marlin": self._marlin_model is not None,
            },
        }

    def _unload_except(self, keep: str) -> None:
        changed = False
        if keep != "florence" and self._florence_model is not None:
            self._florence_model = None
            self._florence_processor = None
            changed = True
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
        valid = {None, "florence", "whisper", "marlin"}
        if family not in valid:
            raise ValueError("family must be one of: florence, whisper, marlin")
        with self._lock:
            if family is None:
                self._unload_except("")
            elif family == "florence":
                self._florence_model = None
                self._florence_processor = None
                gc.collect()
            elif family == "whisper":
                self._whisper_model = None
                self._whisper_processor = None
                gc.collect()
            elif family == "marlin":
                self._marlin_model = None
                gc.collect()
            logger.info("Unloaded multimodal family: %s", family or "all")
            return self.status()

    # ---- Florence ---------------------------------------------------------

    def _patch_florence_config_file(self, model_path: str) -> None:
        config_path = os.path.join(model_path, "config.json")
        if not os.path.exists(config_path):
            return
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            text_config = config.setdefault("text_config", {})
            changed = False
            if "forced_bos_token_id" not in text_config:
                text_config["forced_bos_token_id"] = text_config.get("bos_token_id", 0)
                changed = True
            if "forced_eos_token_id" not in text_config:
                text_config["forced_eos_token_id"] = text_config.get("eos_token_id", 2)
                changed = True
            if "decoder_start_token_id" not in text_config:
                text_config["decoder_start_token_id"] = text_config.get(
                    "eos_token_id", 2
                )
                changed = True
            if changed:
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2)
                    f.write("\n")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _patch_florence_remote_code(self, model_path: str) -> None:
        patch_marker = (
            "# Orb compatibility: transformers 5 may omit forced_bos_token_id"
        )
        insertion = (
            f"        {patch_marker}\n"
            '        if not hasattr(self, "forced_bos_token_id"):\n'
            '            self.forced_bos_token_id = kwargs.get("forced_bos_token_id", None)\n\n'
        )
        paths = [Path(model_path) / "configuration_florence2.py"]
        cache_root = (
            Path.home() / ".cache" / "huggingface" / "modules" / "transformers_modules"
        )
        if cache_root.exists():
            paths.extend(cache_root.glob("**/configuration_florence2.py"))
        target = "        # ensure backward compatibility for BART CNN models\n"
        for path in paths:
            if not path.exists():
                continue
            try:
                source = path.read_text(encoding="utf-8")
                if patch_marker in source or target not in source:
                    continue
                path.write_text(
                    source.replace(target, insertion + target, 1),
                    encoding="utf-8",
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        # Florence2Processor passes do_resize=None into CLIPImageProcessor; on
        # transformers 5 that disables resize (wrong HxW → empty captions). When
        # do_resize=True is passed without size/resample, transformers 5 raises.
        # Patch the image_processor call to always supply size + resample.
        resize_marker = "# Orb compatibility: pass size/resample with do_resize"
        proc_paths = [Path(model_path) / "processing_florence2.py"]
        if cache_root.exists():
            proc_paths.extend(cache_root.glob("**/processing_florence2.py"))
        old_call = (
            "        pixel_values = self.image_processor(\n"
            "            images,\n"
            "            do_resize=do_resize,\n"
            "            do_normalize=do_normalize,\n"
            "            return_tensors=return_tensors,\n"
            "            image_mean=image_mean,\n"
            "            image_std=image_std,\n"
            "            input_data_format=input_data_format,\n"
            "            data_format=data_format,\n"
            "            resample=resample,\n"
            "            do_convert_rgb=do_convert_rgb,\n"
            "        )[\"pixel_values\"]\n"
        )
        new_call = (
            f"        {resize_marker}\n"
            "        _do_resize = True if do_resize is None else do_resize\n"
            "        _resample = resample if resample is not None else getattr(\n"
            "            self.image_processor, \"resample\", None\n"
            "        )\n"
            "        _size = getattr(self.image_processor, \"size\", None)\n"
            "        pixel_values = self.image_processor(\n"
            "            images,\n"
            "            do_resize=_do_resize,\n"
            "            size=_size,\n"
            "            do_normalize=do_normalize,\n"
            "            return_tensors=return_tensors,\n"
            "            image_mean=image_mean,\n"
            "            image_std=image_std,\n"
            "            input_data_format=input_data_format,\n"
            "            data_format=data_format,\n"
            "            resample=_resample,\n"
            "            do_convert_rgb=do_convert_rgb,\n"
            "        )[\"pixel_values\"]\n"
        )
        for path in proc_paths:
            if not path.exists():
                continue
            try:
                source = path.read_text(encoding="utf-8")
                if resize_marker in source:
                    continue
                if old_call in source:
                    path.write_text(
                        source.replace(old_call, new_call, 1), encoding="utf-8"
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def _patch_florence_generation_config(self) -> None:
        config_candidates = [
            getattr(self._florence_model, "config", None),
            getattr(self._florence_model, "generation_config", None),
        ]
        model_config = getattr(self._florence_model, "config", None)
        if model_config is not None:
            config_candidates.append(getattr(model_config, "text_config", None))
        language_model = getattr(self._florence_model, "language_model", None)
        if language_model is not None:
            config_candidates.append(getattr(language_model, "config", None))
            config_candidates.append(getattr(language_model, "generation_config", None))
        for cfg in config_candidates:
            if cfg is None:
                continue
            for attr, value in (
                ("forced_bos_token_id", None),
                ("forced_eos_token_id", None),
                ("decoder_start_token_id", getattr(cfg, "bos_token_id", None)),
            ):
                if not hasattr(cfg, attr):
                    setattr(cfg, attr, value)

    def _patch_tokenizer_additional_special_tokens(self) -> None:
        """Florence processor expects ``additional_special_tokens`` (transformers 4 API).

        Transformers 5 renamed this to ``_extra_special_tokens``; expose a bridge
        property so Florence-2 remote processor code keeps working.
        """
        from transformers.tokenization_utils_base import PreTrainedTokenizerBase

        if getattr(PreTrainedTokenizerBase, "_orb_addl_special_patched", False):
            return

        def _get(self):  # noqa: ANN001
            return list(getattr(self, "_extra_special_tokens", []) or [])

        def _set(self, value):  # noqa: ANN001
            object.__setattr__(self, "_extra_special_tokens", list(value or []))

        PreTrainedTokenizerBase.additional_special_tokens = property(_get, _set)
        PreTrainedTokenizerBase._orb_addl_special_patched = True

    def _load_florence(self) -> None:
        if self._florence_model is not None:
            return
        path = multimodal_model_path("florence")
        if not is_hf_snapshot_ready(path):
            raise RuntimeError(
                f"Florence model not found at {path}. Download multimodal models in Setup."
            )
        from transformers import AutoModelForCausalLM, AutoProcessor

        self._unload_ggufs()
        self._unload_except("florence")
        model_path = str(path)
        self._patch_florence_config_file(model_path)
        self._patch_florence_remote_code(model_path)
        self._patch_tokenizer_additional_special_tokens()
        logger.info("Loading Florence from %s on %s", model_path, self.device)
        started = time.perf_counter()
        # Florence-2 remote code predates transformers 5 SDPA checks; force eager attn.
        load_kwargs: dict[str, Any] = {
            "trust_remote_code": True,
            "attn_implementation": "eager",
        }
        try:
            self._florence_model = (
                AutoModelForCausalLM.from_pretrained(model_path, **load_kwargs)
                .to(self.device)
                .eval()
            )
        except (TypeError, ValueError) as exc:
            # Older remote-code / transformers without attn_implementation kw.
            logger.warning(
                "Florence load with attn_implementation failed (%s); retrying", exc
            )
            self._florence_model = (
                AutoModelForCausalLM.from_pretrained(
                    model_path, trust_remote_code=True
                )
                .to(self.device)
                .eval()
            )
        if not hasattr(self._florence_model, "_supports_sdpa"):
            type(self._florence_model)._supports_sdpa = False  # type: ignore[attr-defined]
        # Checkpoint stores BART embeddings as language_model.model.shared; transformers 5
        # may leave embed_tokens/lm_head randomly initialized. Re-tie before generate.
        self._tie_florence_weights()
        self._florence_processor = AutoProcessor.from_pretrained(
            model_path, trust_remote_code=True
        )
        self._patch_florence_generation_config()
        if not hasattr(self._florence_model, "_supports_sdpa"):
            type(self._florence_model)._supports_sdpa = False  # type: ignore[attr-defined]
        self._record_load("florence", started)
        logger.info("Florence loaded")

    def _tie_florence_weights(self) -> None:
        """Re-bind Florence shared embeddings after transformers 5 load.

        Checkpoint stores BART embeddings only as ``language_model.model.shared``.
        Transformers 5 leaves encoder/decoder ``embed_tokens`` and ``lm_head``
        randomly initialized; keep Florence2ScaledWordEmbedding modules and force
        their ``.weight`` (and ``lm_head.weight``) to share ``shared.weight``.
        """
        model = self._florence_model
        if model is None:
            return
        language_model = getattr(model, "language_model", None)
        inner = getattr(language_model, "model", None) if language_model is not None else None
        shared = getattr(inner, "shared", None) if inner is not None else None
        if language_model is None or inner is None or shared is None:
            logger.warning("Florence weight tie skipped: missing language_model.shared")
            return
        try:
            shared_w = shared.weight
            inner.encoder.embed_tokens.weight = shared_w
            inner.decoder.embed_tokens.weight = shared_w
            language_model.lm_head.weight = shared_w
            tied = (
                inner.encoder.embed_tokens.weight.data_ptr() == shared_w.data_ptr()
                and inner.decoder.embed_tokens.weight.data_ptr() == shared_w.data_ptr()
                and language_model.lm_head.weight.data_ptr() == shared_w.data_ptr()
            )
            if tied:
                logger.info("Florence embeddings hard-tied to shared weights")
            else:
                logger.error(
                    "Florence weight tie failed: shared=%s enc=%s dec=%s head=%s",
                    shared_w.data_ptr(),
                    inner.encoder.embed_tokens.weight.data_ptr(),
                    inner.decoder.embed_tokens.weight.data_ptr(),
                    language_model.lm_head.weight.data_ptr(),
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Florence weight tie failed: %s", exc)

    def _resize_for_florence(self, image):
        """Downscale large images and pad to a square (Florence vision requires square maps)."""
        from PIL import Image

        max_pixels = int(getattr(settings, "FLORENCE_MAX_IMAGE_PIXELS", 0) or 1_500_000)
        working = image
        if max_pixels > 0:
            pixels = working.width * working.height
            if pixels > max_pixels:
                scale = (max_pixels / pixels) ** 0.5
                target_size = (
                    max(1, int(working.width * scale)),
                    max(1, int(working.height * scale)),
                )
                working = working.copy()
                resampling = getattr(getattr(Image, "Resampling", None), "LANCZOS", 1)
                working.thumbnail(target_size, resampling)

        # Florence-2 remote vision encoder asserts square feature maps.
        side = max(working.width, working.height)
        if working.width == side and working.height == side:
            return working
        canvas = Image.new("RGB", (side, side), color=(0, 0, 0))
        offset = ((side - working.width) // 2, (side - working.height) // 2)
        canvas.paste(working, offset)
        return canvas

    def describe_image_path(self, image_path: str) -> str:
        from PIL import Image

        with self._lock:
            self._load_florence()
            image = Image.open(image_path)
            return self._describe_pil(image)

    def _describe_pil(self, image) -> str:
        import torch

        assert self._florence_model is not None and self._florence_processor is not None
        if image.mode != "RGB":
            image = image.convert("RGB")
        image = self._resize_for_florence(image)
        prompt = "<MORE_DETAILED_CAPTION>"
        # Florence2Processor + transformers 5: passing do_resize=None disables
        # CLIP resize (wrong HxW → empty captions); passing do_resize=True without
        # size/resample raises. Preprocess pixels via image_processor defaults.
        pixel_values = self._florence_processor.image_processor(
            images=image, return_tensors="pt"
        )["pixel_values"]
        prompts = self._florence_processor._construct_prompts([prompt])
        text_inputs = self._florence_processor.tokenizer(
            prompts, return_tensors="pt"
        )
        model_dtype = next(self._florence_model.parameters()).dtype
        inputs = {"pixel_values": pixel_values}
        for key, value in text_inputs.items():
            if value is None:
                continue
            inputs[key] = value
        moved = {}
        for key, value in inputs.items():
            if torch.is_floating_point(value):
                moved[key] = value.to(device=self.device, dtype=model_dtype)
            else:
                moved[key] = value.to(self.device)
        inputs = moved
        with torch.no_grad():
            # Greedy decode: beam search is very slow on MPS with Florence remote code.
            generated_ids = self._florence_model.generate(
                **inputs, max_new_tokens=256, num_beams=1, do_sample=False, use_cache=False
            )
        generated_text = self._florence_processor.batch_decode(
            generated_ids, skip_special_tokens=False
        )[0]
        parsed = self._florence_processor.post_process_generation(
            generated_text, task=prompt, image_size=(image.width, image.height)
        )
        return parsed.get(prompt, "") or ""

    # ---- Whisper ----------------------------------------------------------

    def _load_whisper(self) -> None:
        if self._whisper_model is not None:
            return
        path = multimodal_model_path("whisper")
        if not is_hf_snapshot_ready(path):
            raise RuntimeError(
                f"Whisper model not found at {path}. Download multimodal models in Setup."
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
                language="en",
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
                f"Marlin model not found at {path}. Download multimodal models in Setup."
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
