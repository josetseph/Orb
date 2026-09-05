"""Download Florence-2, Whisper, and Marlin into MODELS_DIR for local multimedia."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.log import get_logger
from app.core.paths import (
    local_download_staging_dir,
    looks_like_network_volume,
    resolve_models_dir,
)

logger = get_logger("MultimodalModels")


def _whisper_repo_and_dir() -> tuple[str, str]:
    """Whisper's repo depends on the engine this machine will actually use.

    An explicit MODEL_WHISPER_HF still wins, so a pinned deployment is
    unaffected; otherwise the platform default is chosen (MLX on Apple Silicon).
    """
    from app.services import whisper_engine

    configured_repo = (settings.MODEL_WHISPER_HF or "").strip()
    configured_dir = (settings.MODEL_WHISPER_LOCAL or "").strip()
    if configured_repo and configured_dir:
        return configured_repo, configured_dir

    choice = whisper_engine.choose(
        resolve_models_dir(), preferred_engine=settings.WHISPER_ENGINE
    )
    if choice.model_path is not None:
        return (
            configured_repo or whisper_engine.DEFAULT_REPO[choice.engine],
            choice.model_path.name,
        )
    engine = choice.engine
    return (
        configured_repo or whisper_engine.DEFAULT_REPO[engine],
        configured_dir or whisper_engine.DEFAULT_LOCAL_DIR[engine],
    )


def _hf_repo_and_dir(kind: str) -> tuple[str, str]:
    if kind == "florence":
        return settings.MODEL_FLORENCE_HF, settings.MODEL_FLORENCE_LOCAL
    if kind == "whisper":
        return _whisper_repo_and_dir()
    if kind == "marlin":
        return settings.MODEL_MARLIN_HF, settings.MODEL_MARLIN_LOCAL
    raise ValueError(f"Unknown multimodal model kind: {kind}")


def multimodal_model_path(kind: str) -> Path:
    _, local_name = _hf_repo_and_dir(kind)
    return resolve_models_dir() / local_name


def is_hf_snapshot_ready(dest: Path) -> bool:
    """True when a HF snapshot looks usable (config + weights)."""
    if not dest.is_dir():
        return False
    has_config = (dest / "config.json").exists() or (dest / "model_index.json").exists()
    if not has_config:
        # Florence / some repos nest or use preprocessor_config alone
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
            "huggingface_hub is required to download Florence/Whisper/Marlin. "
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
    Ensure Florence-2 + Whisper (+ Marlin) live under MODELS_DIR.

    Marlin defaults to the ungated mirror ``lunahr/Marlin-2B-ungated``
    (override with MODEL_MARLIN_HF). No HF token required for that repo.
    """
    out: dict[str, Path] = {}
    for kind in ("florence", "whisper"):
        repo, _ = _hf_repo_and_dir(kind)
        dest = multimodal_model_path(kind)
        out[kind] = ensure_hf_snapshot(
            repo, dest, on_progress=on_progress, label=kind
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
