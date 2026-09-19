"""Multimodal runtime readiness (in-process Qwen3-ASR / Marlin).

Legacy HTTP sidecars are retired. This module verifies weights on disk and
optionally installs torch/transformers into the *current* API interpreter so
models load in-process.
"""

from __future__ import annotations

import platform
import importlib
import os
import site
import subprocess
import sys

from app.core.log import get_logger
from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path

logger = get_logger("MultimodalServices")

_MULTIMODAL_PIP = [
    "torch",
    # Marlin requires transformers>=5.7 (Qwen3.5 backbone); Qwen3-ASR ships there too.
    "transformers>=5.7.0",
    "accelerate>=1.12.0",
    "einops>=0.8.1",
    "safetensors>=0.7.0",
    "librosa>=0.11.0",
    "pydub>=0.25.1",
    "timm>=1.0.24",
    "Pillow>=12.0.0",
    "qwen-vl-utils>=0.0.14",
    "av",
    # Speaker labels on transcripts (CPU, every platform).
    "pyannote.audio>=4.0",
]
if sys.platform == "darwin" and platform.machine() == "arm64":
    # Same marker as requirements-multimodal.txt: the Apple GPU path for
    # transcription.
    _MULTIMODAL_PIP += ["mlx-qwen3-asr>=0.4"]


def _deps_importable() -> tuple[bool, str | None]:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import librosa  # noqa: F401
        import pydub  # noqa: F401
        from PIL import Image  # noqa: F401

        # Marlin hard-requires Qwen3.5 (transformers>=5.7) + qwen-vl-utils.
        ver = tuple(int(x) for x in transformers.__version__.split(".")[:2])
        if ver < (5, 7):
            return False, (
                f"transformers {transformers.__version__} < 5.7 "
                "(Marlin needs Qwen3_5ForConditionalGeneration)"
            )
        import qwen_vl_utils  # noqa: F401
        import av  # noqa: F401

        # Exercise the real model entrypoints Qwen3-ASR/Marlin need. A bare
        # ``import transformers`` can succeed while AutoModel* fails (e.g. when
        # numpy/_core/tests was stripped from the desktop bundle).
        from transformers import (  # noqa: F401
            AutoModelForCausalLM,
            AutoModelForMultimodalLM,
        )

        return True, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, str(exc)


def ensure_multimodal_python_deps(*, install: bool = False) -> dict:
    """Ensure the API process can import torch/transformers for in-process ML."""
    ok, err = _deps_importable()
    if ok:
        return {"ok": True, "installed": False}
    if not install:
        return {
            "ok": False,
            "installed": False,
            "error": (
                "Multimodal Python deps missing in API process "
                f"({err}). Call with install_deps=True or pip install "
                "torch transformers librosa pydub."
            ),
        }
    # The desktop runtime sets PYTHONUSERBASE under DATA_DIR: the packages land
    # there, not inside the (signed, read-only) app bundle. A venv has no user
    # site, so dev installs go into the venv as before.
    user_site = bool(os.environ.get("PYTHONUSERBASE"))
    logger.info(
        "Installing multimodal deps %s …",
        f"into {os.environ['PYTHONUSERBASE']}" if user_site else f"with {sys.executable}",
    )
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", *(["--user"] if user_site else []), *_MULTIMODAL_PIP],
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"ok": False, "installed": False, "error": str(exc)}
    if user_site:
        # This process started before the directory had anything in it.
        site.addsitedir(site.getusersitepackages())
        importlib.invalidate_caches()
    ok, err = _deps_importable()
    return {"ok": ok, "installed": True, "error": None if ok else err}


def services_ready() -> dict:
    """Compatibility status payload — models are in-process, not HTTP services."""
    from app.services.multimodal_runtime import multimodal_runtime

    deps_ok, deps_err = _deps_importable()
    status = multimodal_runtime.status()
    return {
        "mode": "in_process",
        "local_models": deps_ok and status["models_ready"].get("asr", False),
        "marlin": deps_ok and status["models_ready"].get("marlin", False),
        "deps_ok": deps_ok,
        "deps_error": deps_err,
        "runtime": status,
    }


def ensure_multimodal_services(*, install_deps: bool = False) -> dict:
    """Prepare in-process multimodal runtime (no HTTP processes spawned)."""
    asr = multimodal_model_path("asr")
    marlin = multimodal_model_path("marlin")
    models = {
        "asr": is_hf_snapshot_ready(asr),
        "marlin": is_hf_snapshot_ready(marlin),
    }
    if not models["asr"]:
        return {
            "started": False,
            "mode": "in_process",
            "error": "Download Qwen3-ASR on the Models page first",
            "models": models,
            "paths": {"asr": str(asr), "marlin": str(marlin)},
        }

    deps = ensure_multimodal_python_deps(install=install_deps)
    if not deps.get("ok"):
        return {
            "started": False,
            "mode": "in_process",
            "error": deps.get("error"),
            "models": models,
            "deps": deps,
        }

    return {
        "started": True,
        "mode": "in_process",
        "already_running": False,
        "models": models,
        "deps": deps,
        "services": services_ready(),
        "message": (
            "Qwen3-ASR / Marlin load in-process on demand "
            "(no sidecar HTTP services)."
        ),
    }
