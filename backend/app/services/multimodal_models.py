"""Download the media models into MODELS_DIR: Phonon-2, the speaker diarizer, Marlin."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.log import get_logger
from app.core.paths import (
    local_download_staging_dir,
    looks_like_network_volume,
    resolve_models_dir,
)
from app.services import asr_engine

logger = get_logger("MultimodalModels")


def _hf_repo_and_dir(kind: str) -> tuple[str, str]:
    """HuggingFace snapshots only; Phonon-2 is fetched by fermion (asr_engine)."""
    if kind == "marlin":
        return settings.MODEL_MARLIN_HF, settings.MODEL_MARLIN_LOCAL
    if kind == "diarizer":
        return asr_engine.DIARIZER_REPO, asr_engine.DIARIZER_DIR
    raise ValueError(f"Unknown multimodal model kind: {kind}")


def multimodal_model_path(kind: str) -> Path:
    _, local_name = _hf_repo_and_dir(kind)
    return resolve_models_dir() / local_name


def is_hf_snapshot_ready(dest: Path) -> bool:
    """True when a HF snapshot looks usable (config + weights)."""
    if not dest.is_dir():
        return False
    has_config = (
        (dest / "config.json").exists()
        or (dest / "config.yaml").exists()
        or (dest / "model_index.json").exists()
    )
    if not has_config:
        # Some repos nest or use preprocessor_config alone
        has_config = (dest / "preprocessor_config.json").exists()
    weight_globs = (
        "*.safetensors",
        "*.bin",
        "*.pt",
        "*.pth",
        "*.gguf",
        "*.onnx",
    )
    has_weights = False
    for pattern in weight_globs:
        if any(dest.rglob(pattern)):
            has_weights = True
            break
    if not has_weights:
        # Some HF dirs use shards under subfolders already covered by rglob;
        # fall back to non-trivial total size.
        total = 0
        try:
            for p in dest.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
                    if total > 50_000_000:
                        has_weights = True
                        break
        except OSError:
            return False
    return bool(has_config or has_weights) and has_weights


def ensure_hf_snapshot(
    repo_id: str,
    dest: Path,
    *,
    on_progress=None,
    label: str | None = None,
) -> Path:
    """Download a Hugging Face repo into dest if missing/incomplete.

    When dest is on a network mount, download into a local cache first, then copy
    (HF locks + multi-GB blobs are unreliable directly on SMB/NAS).
    """
    import shutil
    import tempfile

    dest = Path(dest)
    if is_hf_snapshot_ready(dest):
        logger.info(f"Multimodal model already present: {dest}")
        if on_progress:
            on_progress(label or repo_id, 100)
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required to download the diarizer and Marlin. "
            "Install with: pip install huggingface_hub"
        ) from exc

    name = label or repo_id
    logger.info(f"Downloading {name} ({repo_id}) → {dest}")
    if on_progress:
        on_progress(name, 1)

    use_staging = looks_like_network_volume(dest)
    download_dir = dest
    staging: Path | None = None
    if use_staging:
        staging_root = local_download_staging_dir()
        staging = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=str(staging_root)))
        download_dir = staging
        logger.info(f"Staging {name} on local disk → {staging}, then move to {dest}")

    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(download_dir),
        )
        if staging is not None:
            if dest.exists():
                for child in dest.iterdir():
                    if child.name == ".cache":
                        shutil.rmtree(child, ignore_errors=True)
                        continue
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            for child in staging.iterdir():
                target = dest / child.name
                if child.is_dir():
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    if not is_hf_snapshot_ready(dest):
        raise RuntimeError(f"Download finished but model looks incomplete: {dest}")

    if on_progress:
        on_progress(name, 100)
    logger.info(f"Ready: {dest}")
    return dest


def ensure_multimodal_models(
    *,
    include_marlin: bool = True,
    on_progress=None,
) -> dict[str, Path]:
    """
    Ensure Phonon-2, the speaker diarizer (+ Marlin) live under MODELS_DIR.

    Marlin defaults to the ungated mirror ``lunahr/Marlin-2B-ungated``
    (override with MODEL_MARLIN_HF). No HF token required for that repo.
    """
    out: dict[str, Path] = {}
    # Phonon-2 (164 MB): fermion verifies and unpacks its own container. It is
    # one of the on-demand media packages, so they go in first when missing.
    from app.services.multimodal_services import ensure_multimodal_python_deps

    deps = ensure_multimodal_python_deps(install=True)
    if not deps.get("ok"):
        raise RuntimeError(f"Could not install the media packages: {deps.get('error')}")
    if on_progress:
        on_progress("asr", 0)
    out["asr"] = asr_engine.download_phonon(resolve_models_dir())
    if on_progress:
        on_progress("asr", 100)

    # Speaker labels: the 30 MB pyannote pipeline over Phonon's word timings.
    out["diarizer"] = ensure_hf_snapshot(
        asr_engine.DIARIZER_REPO,
        multimodal_model_path("diarizer"),
        on_progress=on_progress,
        label="diarizer",
    )

    if include_marlin:
        repo, _ = _hf_repo_and_dir("marlin")
        dest = multimodal_model_path("marlin")
        try:
            out["marlin"] = ensure_hf_snapshot(
                repo, dest, on_progress=on_progress, label="marlin"
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            msg = str(exc)
            if "gated" in msg.lower() or "401" in msg or "restricted" in msg.lower():
                logger.warning(
                    "Marlin download failed (gated/auth). "
                    f"Current MODEL_MARLIN_HF={repo}. "
                    "Use lunahr/Marlin-2B-ungated (default) or set HF_TOKEN for a gated repo."
                )
                if on_progress:
                    on_progress("marlin (skipped — auth)", 100)
            else:
                raise
    return out
