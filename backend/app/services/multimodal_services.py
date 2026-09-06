"""Multimodal runtime readiness (in-process Florence / Whisper / Marlin).

Legacy HTTP sidecars are retired. This module verifies weights on disk and
optionally installs torch/transformers into the *current* API interpreter so
models load in-process.
"""

from __future__ import annotations

import subprocess
import sys

from app.core.log import get_logger
from app.services.multimodal_models import is_hf_snapshot_ready, multimodal_model_path

logger = get_logger("MultimodalServices")

_MULTIMODAL_PIP = [
    "torch",
    # Marlin requires transformers>=5.7 (Qwen3.5 backbone). Florence/Whisper
    # also run on 5.x with Orb compatibility patches.
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
]


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

        # Exercise the real model entrypoints Florence/Whisper need. A bare
        # ``import transformers`` can succeed while AutoModel* fails (e.g. when
        # numpy/_core/tests was stripped from the desktop bundle).
        from transformers import (  # noqa: F401
            AutoModelForCausalLM,
            AutoModelForSpeechSeq2Seq,
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
    logger.info("Installing multimodal deps into %s …", sys.executable)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", *_MULTIMODAL_PIP],
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {"ok": False, "installed": False, "error": str(exc)}
    ok, err = _deps_importable()
    return {"ok": ok, "installed": True, "error": None if ok else err}


def services_ready() -> dict:
    """Compatibility status payload — models are in-process, not HTTP services."""
    from app.services.multimodal_runtime import multimodal_runtime

    deps_ok, deps_err = _deps_importable()
    status = multimodal_runtime.status()
    return {
        "mode": "in_process",
        "local_models": deps_ok and status["models_ready"].get("florence", False),
        "marlin": deps_ok and status["models_ready"].get("marlin", False),
        "deps_ok": deps_ok,
        "deps_error": deps_err,
        "runtime": status,
    }


def ensure_multimodal_services(
    *,
    install_deps: bool = False,
    start_marlin: bool = True,  # noqa: ARG001 — API compat; Marlin is in-process
) -> dict:
    """Prepare in-process multimodal runtime (no HTTP processes spawned)."""
    florence = multimodal_model_path("florence")
    whisper = multimodal_model_path("whisper")
    marlin = multimodal_model_path("marlin")
    models = {
        "florence": is_hf_snapshot_ready(florence),
        "whisper": is_hf_snapshot_ready(whisper),
        "marlin": is_hf_snapshot_ready(marlin),
    }
    if not models["florence"] or not models["whisper"]:
        return {
            "started": False,
            "mode": "in_process",
            "error": "Download Florence + Whisper on the Models page first",
            "models": models,
            "paths": {
                "florence": str(florence),
                "whisper": str(whisper),
                "marlin": str(marlin),
            },
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
            "Florence / Whisper / Marlin load in-process on demand "
            "(no sidecar HTTP services)."
        ),
    }
