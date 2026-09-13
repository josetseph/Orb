"""Direct local GGUF download + in-process llama-cpp-python (no llama-server / Ollama / LM Studio)."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from app.core.config import settings
from app.core.log import get_logger
from app.core.paths import (
    local_download_staging_dir,
    looks_like_network_volume,
    resolve_models_dir,
)

logger = get_logger("LocalModels")

def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip() != "":
            return val
    return default



# Gemma 4 degeneration under compact SWA — same signature as content-machine.
_ORDINAL_LOOP_RE = re.compile(
    r"(?:\bor the\b[\s\S]{0,40}?){12,}",
    re.IGNORECASE,
)


class RepetitionLoopError(RuntimeError):
    """Raised when Gemma 4 enters an ordinal / 'or the' cascade."""


def _raise_if_degeneration(text: str) -> None:
    if text and _ORDINAL_LOOP_RE.search(text):
        raise RepetitionLoopError(
            "LLM entered a repetition loop (ordinal/or-the cascade). "
            "Retrying with a fresh sample."
        )


class PromptTooLongError(RuntimeError):
    """Prompt alone (nearly) fills the context window — nothing left to generate."""


class ModelLoadClock:
    """Process-wide accumulator of time spent *loading* heavy models.

    Every GGUF / HF load records here so ingestion and chat can report
    ``model_load`` separately from inference — a slow disk and a slow model
    look identical in a bare stage timer.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seconds: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def record(self, kind: str, seconds: float) -> None:
        with self._lock:
            self._seconds[kind] = self._seconds.get(kind, 0.0) + float(seconds)
            self._counts[kind] = self._counts.get(kind, 0) + 1
        logger.info("[ModelLoad] %s loaded in %.1fs", kind, seconds)

    def snapshot(self) -> dict:
        with self._lock:
            return {"seconds": dict(self._seconds), "counts": dict(self._counts)}

    @staticmethod
    def diff(before: dict, after: dict) -> dict:
        """Loads that happened between two snapshots."""
        b_sec = before.get("seconds", {})
        b_cnt = before.get("counts", {})
        seconds = {
            k: round(v - b_sec.get(k, 0.0), 2)
            for k, v in after.get("seconds", {}).items()
            if v - b_sec.get(k, 0.0) > 0
        }
        counts = {
            k: v - b_cnt.get(k, 0)
            for k, v in after.get("counts", {}).items()
            if v - b_cnt.get(k, 0) > 0
        }
        return {
            "total_seconds": round(sum(seconds.values()), 2),
            "seconds": seconds,
            "counts": counts,
        }

    @staticmethod
    def describe(delta: dict) -> str:
        counts = delta.get("counts") or {}
        if not counts:
            return "none"
        return ",".join(f"{k}×{n}" for k, n in sorted(counts.items()))


model_load_clock = ModelLoadClock()

# Generation budget: leave a little headroom below n_ctx, and refuse prompts that
# leave less than this many tokens for the answer.
_GEN_SAFETY_MARGIN = 32
_MIN_OUTPUT_TOKENS = 256


def _default_chat_n_ctx() -> int:
    return int(_env_first("ORB_LLAMA_N_CTX", "LIVEOS_LLAMA_N_CTX", default="16384"))


def _default_chat_max_tokens() -> int | None:
    """Explicit output cap from env, or None to use whatever context remains.

    Unset by default: a fixed cap silently truncates long extractions, so the
    runtime sizes ``max_tokens`` per call from ``n_ctx - prompt_tokens`` instead.
    """
    raw = _env_first("ORB_LLAMA_MAX_TOKENS", "LIVEOS_LLAMA_MAX_TOKENS")
    if not raw:
        return None
    try:
        return int(raw) or None
    except ValueError:
        return None


def _clamp_ctx_to_model(gguf_path: Path, requested_ctx: int) -> int:
    """Never ask for more context than the model was trained for.

    With arbitrary user-supplied GGUFs the configured 16k default can exceed a
    model's real window (some are 4k/8k), which wastes KV cache at best and
    produces garbage past the trained length at worst. Reading the header costs
    ~0.1-0.3s against a load measured in tens of seconds.
    """
    try:
        from app.services.gguf_metadata import try_read_gguf_metadata

        info = try_read_gguf_metadata(gguf_path)
    except Exception:  # pylint: disable=broad-exception-caught
        return requested_ctx
    model_ctx = getattr(info, "context_length", None) if info else None
    if not model_ctx or model_ctx <= 0 or model_ctx >= requested_ctx:
        return requested_ctx
    logger.info(
        "Lowering n_ctx %s -> %s to match %s's trained context window",
        requested_ctx,
        model_ctx,
        gguf_path.name,
    )
    return int(model_ctx)


def _default_repeat_penalty() -> float:
    raw = _env_first("ORB_LLAMA_REPEAT_PENALTY", "LIVEOS_LLAMA_REPEAT_PENALTY", default="1.12")
    try:
        return float(raw)
    except ValueError:
        return 1.12


def model_idle_seconds() -> float:
    """Seconds of inactivity before unloading in-process GGUFs.

    Override with ORB_MODEL_IDLE_SECONDS (default 300 = 5 minutes).
    Set to 0 to keep models loaded for the whole app session.
    """
    raw = _env_first("ORB_MODEL_IDLE_SECONDS", "LIVEOS_MODEL_IDLE_SECONDS", default="300")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300.0


def release_accelerator_memory() -> None:
    """GC + empty CUDA/MPS caches after unloading a heavy model (content-machine pattern)."""
    gc.collect()
    try:
        import torch
    except ImportError:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    try:
        mps = getattr(torch, "mps", None)
        backends = getattr(torch, "backends", None)
        backends_mps = getattr(backends, "mps", None) if backends is not None else None
        if mps is None or backends_mps is None or not backends_mps.is_available():
            return
        try:
            allocated = int(mps.driver_allocated_memory())
        except Exception:  # pylint: disable=broad-exception-caught
            return
        if allocated > 0:
            mps.empty_cache()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _close_llama_handle(model) -> None:
    if model is None:
        return
    try:
        close = getattr(model, "close", None)
        if callable(close):
            close()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _llama_metal_safe_kwargs(base: dict) -> dict:
    """Apply Gemma-4 / Metal-safe llama.cpp defaults from content-machine.

    Full-size SWA (``swa_full=True``) is required for stable Gemma 4 output —
    compact SWA fits 32k but causes ordinal/"or the" repetition collapse.
    16k + swa_full fits ~24GB Metal; 32k + swa_full OOMs.
    Flash attention stays off unless explicitly opted in.
    """
    kwargs = dict(base)
    raw_swa = (_env_first("ORB_LLAMA_SWA_FULL", "LIVEOS_LLAMA_SWA_FULL", default="") or "").strip().lower()
    if raw_swa in {"0", "false", "no"}:
        kwargs["swa_full"] = False
    else:
        # Default true (content-machine): stable text over max context.
        kwargs["swa_full"] = True
    if (_env_first("ORB_LLAMA_FLASH_ATTN", "LIVEOS_LLAMA_FLASH_ATTN", default="") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        kwargs["flash_attn"] = True
    return kwargs


def _construct_llama(Llama, **kwargs):
    safe = _llama_metal_safe_kwargs(kwargs)
    try:
        return Llama(**safe)
    except TypeError as exc:
        cleaned = {
            k: v for k, v in safe.items() if k not in ("swa_full", "flash_attn")
        }
        if cleaned.keys() == safe.keys():
            raise
        logger.warning(
            "Llama() rejected swa_full/flash_attn (%s); retrying without them", exc
        )
        return Llama(**cleaned)


def _unload_multimodal_families() -> None:
    """Best-effort: free Florence/Whisper/Marlin before loading a GGUF."""
    try:
        from app.services.multimodal_runtime import multimodal_runtime

        multimodal_runtime.unload(None)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Multimodal unload before GGUF skipped: %s", exc)
# Pinned defaults (overridden by Setup selection / manifest)
CHAT_MODEL_ID = os.environ.get(
    "ORB_CHAT_GGUF",
    "bartowski/google_gemma-4-E4B-it-GGUF/google_gemma-4-E4B-it-Q4_K_M.gguf",
)
EMBED_MODEL_ID = os.environ.get(
    "ORB_EMBED_GGUF",
    "Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf",
)
RERANK_MODEL_ID = os.environ.get(
    "ORB_RERANK_GGUF",
    "mradermacher/Qwen3-Reranker-0.6B-GGUF/Qwen3-Reranker-0.6B.Q4_K_M.gguf",
)

_HF_BASE = "https://huggingface.co"


def _hf_file_url(repo_file: str) -> str:
    """repo_file like 'org/repo/file.gguf' → resolve URL."""
    parts = repo_file.strip("/").split("/")
    if len(parts) < 3:
        raise ValueError(f"Invalid HF path: {repo_file}")
    org, repo = parts[0], parts[1]
    filename = "/".join(parts[2:])
    return f"{_HF_BASE}/{org}/{repo}/resolve/main/{filename}"


def models_manifest_path() -> Path:
    return resolve_models_dir() / "manifest.json"


def load_manifest() -> dict:
    p = models_manifest_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_manifest(data: dict) -> None:
    root = resolve_models_dir()
    root.mkdir(parents=True, exist_ok=True)
    models_manifest_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _atomic_place(src: Path, dest: Path) -> None:
    """Move/copy src → dest reliably across volumes (NAS-safe)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.replace(dest)
        return
    except OSError:
        # Cross-device / SMB: copy then remove
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            if tmp.exists():
                tmp.unlink()
            shutil.copy2(src, tmp)
            tmp.replace(dest)
        finally:
            if tmp.exists() and tmp != dest:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            try:
                src.unlink(missing_ok=True)
            except OSError:
                pass


def _min_expected_gguf_bytes(model_id: str) -> int:
    """Lower bound for a complete GGUF — prefer catalog size_gb when known."""
    try:
        from app.services.model_catalog import ALL_MODELS

        filename = model_id.rsplit("/", 1)[-1]
        for opt in ALL_MODELS.values():
            if opt.hf_file == filename or opt.hf_path == model_id:
                # Allow ~8% short of catalog estimate (HF mirrors vary slightly).
                return max(50_000_000, int(opt.size_gb * 1_000_000_000 * 0.92))
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return 50_000_000


def _gguf_looks_complete(dest: Path, model_id: str) -> bool:
    if not dest.exists():
        return False
    size = dest.stat().st_size
    if size < _min_expected_gguf_bytes(model_id):
        return False
    # Truncated downloads often leave a tiny leftover or a half-written file;
    # also reject leftover .partial siblings that indicate a crashed move.
    if dest.with_suffix(dest.suffix + ".partial").exists():
        return False
    return True


def download_file(url: str, dest: Path, on_progress=None) -> Path:
    """Download url to dest. When dest is on a network volume, stage on local disk first.

    Interrupted downloads leave a ``*.partial`` file and are restarted from scratch
    (no byte-range resume). A finished file is only accepted when Content-Length
    matches (when the server sends it).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage_local = looks_like_network_volume(dest) or os.environ.get(
        "ORB_FORCE_DOWNLOAD_STAGING"
    )
    if stage_local:
        staging = local_download_staging_dir() / dest.name
        partial = staging.with_suffix(staging.suffix + ".partial")
        logger.info(f"Staging download on local disk → {staging} (then move to {dest})")
    else:
        staging = dest
        partial = dest.with_suffix(dest.suffix + ".partial")

    # No true resume over plain urlopen — delete partial and restart.
    if partial.exists():
        logger.info(f"Restarting incomplete download ({partial.name})")
        try:
            partial.unlink()
        except OSError:
            pass

    req = Request(url, headers={"User-Agent": "Orb/1.0"})
    with urlopen(req, timeout=120) as resp, open(partial, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        received = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if on_progress and total:
                on_progress(int(received * 100 / total))

    if total and received != total:
        try:
            partial.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Download incomplete for {dest.name}: got {received} of {total} bytes. "
            "Will retry on next attempt."
        )

    if stage_local:
        _atomic_place(partial, staging)
        logger.info(f"Moving staged model to MODELS_DIR: {dest}")
        _atomic_place(staging, dest)
    else:
        _atomic_place(partial, dest)
    return dest


def ensure_gguf(model_id: str, on_progress=None) -> Path:
    """Download a GGUF into MODELS_DIR/gguf/ if missing or incomplete."""
    filename = model_id.rsplit("/", 1)[-1]
    dest = resolve_models_dir() / "gguf" / filename
    if _gguf_looks_complete(dest, model_id):
        return dest
    if dest.exists():
        logger.warning(
            f"Removing incomplete GGUF {dest.name} "
            f"({dest.stat().st_size} bytes < expected) before re-download"
        )
        try:
            dest.unlink()
        except OSError:
            pass
    # Clean up abandoned NAS partials from older direct downloads
    stale = dest.with_suffix(dest.suffix + ".partial")
    if stale.exists():
        logger.info(f"Removing incomplete partial {stale.name} before re-download")
        try:
            stale.unlink()
        except OSError:
            pass
    url = _hf_file_url(model_id)
    logger.info(f"Downloading model {filename} from Hugging Face…")
    download_file(url, dest, on_progress)
    if not _gguf_looks_complete(dest, model_id):
        raise RuntimeError(
            f"Downloaded {filename} looks incomplete "
            f"({dest.stat().st_size if dest.exists() else 0} bytes)."
        )
    man = load_manifest()
    man.setdefault("gguf", {})[filename] = {
        "source": model_id,
        "path": str(dest),
        "bytes": dest.stat().st_size,
    }
    save_manifest(man)
    return dest


def save_selection(
    chat_id: str,
    embed_id: str,
    reranker_id: str,
    embedding_dims: int | None = None,
) -> dict:
    """Persist model selection and sync infrastructure that depends on the embed model."""
    if embedding_dims is None:
        from app.services.model_catalog import get_option

        opt = get_option(embed_id)
        embedding_dims = (
            int(opt.embedding_dims)
            if opt and opt.embedding_dims
            else int(getattr(settings, "EMBEDDING_DIMENSIONS", 1024) or 1024)
        )
    man = load_manifest()
    prev = man.get("selection") or {}
    prev_dims = prev.get("embedding_dims")
    prev_embed = prev.get("embed_id") or ""
    man["selection"] = {
        "chat_id": chat_id,
        "embed_id": embed_id,
        "reranker_id": reranker_id,
        "embedding_dims": int(embedding_dims),
        # Keep a recorded path only where the model it belongs to is unchanged.
        # Carrying them across a re-selection pointed the new id at the old
        # file, so the manifest disagreed with itself until the next download.
        **{
            path_key: prev[path_key]
            for path_key, id_key, new_id in (
                ("chat_path", "chat_id", chat_id),
                ("embed_path", "embed_id", embed_id),
                ("reranker_path", "reranker_id", reranker_id),
            )
            if path_key in prev and prev.get(id_key) == new_id
        },
    }
    save_manifest(man)
    result = sync_embedding_infrastructure(
        dims=int(embedding_dims), embed_id=embed_id or None
    )
    if prev_embed and prev_embed != embed_id:
        logger.warning(
            "[Models] Embed model changed %s → %s (dims %s → %s). "
            "Qdrant collections were resized; re-ingest notes so vectors match.",
            prev_embed,
            embed_id,
            prev_dims,
            embedding_dims,
        )
    elif prev_dims and int(prev_dims) != int(embedding_dims):
        logger.warning(
            "[Models] Embedding dims changed %s → %s. "
            "Qdrant collections were resized; re-ingest notes so vectors match.",
            prev_dims,
            embedding_dims,
        )
    return result


def sync_embedding_infrastructure(
    *,
    dims: int | None = None,
    embed_id: str | None = None,
) -> dict:
    """Make settings + every KB's Qdrant collections match the active embed model.

    Call this whenever the embed model (or its output dimension) changes — on
    selection, download, local LLM load, and API startup.
    """
    from app.core.config import settings as _settings

    if dims is None:
        man = load_manifest()
        sel = man.get("selection") or {}
        dims = sel.get("embedding_dims")
        if not dims and sel.get("embed_id"):
            from app.services.model_catalog import get_option

            opt = get_option(sel["embed_id"])
            dims = opt.embedding_dims if opt else None
        if not dims:
            dims = int(getattr(_settings, "EMBEDDING_DIMENSIONS", 1024) or 1024)
    dims = int(dims)
    if embed_id is None:
        embed_id = (load_manifest().get("selection") or {}).get("embed_id") or ""

    _settings.EMBEDDING_DIMENSIONS = dims
    if embed_id:
        _settings.EMBEDDING_MODEL = embed_id

    # Reranker is a cross-encoder (not tied to embed dims), but keep the
    # selected catalog id in settings so retrieval logs / UI stay accurate.
    try:
        sel = load_manifest().get("selection") or {}
        reranker_id = (sel.get("reranker_id") or "").strip()
        if reranker_id:
            _settings.MODEL_RERANKER_LOCAL = reranker_id
        # If the on-disk GGUF changed (e.g. 0.6B → 4B), drop a stale in-memory model.
        # Look up late so this works even if called during partial import.
        reranker = globals().get("local_gguf_reranker")
        path = reranker_gguf_path()
        if (
            reranker is not None
            and path
            and reranker.loaded
            and getattr(reranker, "_path", None) is not None
            and reranker._path != path  # pylint: disable=protected-access
        ):
            logger.info(
                "[Models] Reranker GGUF changed (%s → %s); unloading stale model",
                reranker._path,  # pylint: disable=protected-access
                path,
            )
            reranker.unload()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[Models] Could not sync reranker selection: %s", exc)

    synced: list[str] = []
    errors: list[str] = []

    # Default / global Qdrant collections
    try:
        from app.services.qdrant_service import qdrant_service

        qdrant_service.ensure_vector_size(dims)
        synced.append("default")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        errors.append(f"default: {exc}")
        logger.warning("[Models] Could not sync default Qdrant dims: %s", exc)

    # Per-KB collections (anon, votex365, …) — touch Qdrant only, never open Kuzu.
    try:
        from app.services.kb_registry import kb_registry
        from app.services.qdrant_service import QdrantService

        for meta in kb_registry.list_kbs():
            kb_id = meta.get("id") or ""
            if not kb_id or kb_id == "default":
                continue
            try:
                cores = meta.get("qdrant_col_cores")
                rels = meta.get("qdrant_col_rels")
                ctxs = meta.get("qdrant_col_contexts")
                if not (cores and rels and ctxs):
                    continue
                qs = QdrantService(
                    col_cores=cores,
                    col_relationships=rels,
                    col_contexts=ctxs,
                )
                qs.ensure_vector_size(dims)
                synced.append(kb_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(f"{kb_id}: {exc}")
                logger.warning(
                    "[Models] Could not sync Qdrant dims for KB %s: %s", kb_id, exc
                )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        errors.append(f"registry: {exc}")
        logger.warning("[Models] KB registry sync failed: %s", exc)

    logger.info(
        "[Models] Embedding infrastructure synced — dims=%s embed_id=%s kbs=%s",
        dims,
        embed_id or "(unchanged)",
        synced,
    )
    return {
        "embedding_dims": dims,
        "embed_id": embed_id or None,
        "synced_kbs": synced,
        "errors": errors,
    }


def resolve_selected_hf_paths(
    chat_id: str | None = None,
) -> dict[str, str]:
    """Map selection (or defaults) → HF org/repo/file paths."""
    from app.services.model_catalog import get_option, recommend_stack

    stack = recommend_stack(chat_id)
    chat_opt = None
    if chat_id:
        chat_opt = get_option(chat_id)
    if chat_opt is None:
        chat_opt_data = stack.get("suggested_chat")
        chat_opt = get_option(chat_opt_data["id"]) if chat_opt_data else None
    embed_opt = get_option((stack.get("embed") or {}).get("id", ""))
    rerank_opt = get_option((stack.get("reranker") or {}).get("id", ""))

    # Fall back to env/default strings if catalog missing
    chat_hf = chat_opt.hf_path if chat_opt else CHAT_MODEL_ID
    embed_hf = embed_opt.hf_path if embed_opt else EMBED_MODEL_ID
    rerank_hf = rerank_opt.hf_path if rerank_opt else RERANK_MODEL_ID
    return {
        "chat": chat_hf,
        "embed": embed_hf,
        "reranker": rerank_hf,
        "chat_id": chat_opt.id if chat_opt else "",
        "embed_id": embed_opt.id if embed_opt else "",
        "reranker_id": rerank_opt.id if rerank_opt else "",
        "embedding_dims": (
            embed_opt.embedding_dims if embed_opt and embed_opt.embedding_dims else 1024
        ),
    }


def ensure_chat_and_embed_models(
    on_progress=None, chat_id: str | None = None
) -> dict[str, Path]:
    """Download chat + embed (+ rerank) GGUFs for the selected/recommended stack."""
    resolved = resolve_selected_hf_paths(chat_id)
    save_selection(
        resolved["chat_id"],
        resolved["embed_id"],
        resolved["reranker_id"],
        embedding_dims=int(resolved["embedding_dims"]),
    )

    def _wrap(label):
        def cb(pct):
            if on_progress:
                on_progress(label, pct)

        return cb

    chat = ensure_gguf(resolved["chat"], _wrap("chat"))
    embed = ensure_gguf(resolved["embed"], _wrap("embed"))
    rerank = ensure_gguf(resolved["reranker"], _wrap("reranker"))

    # Paths + dims already persisted via save_selection; keep path fields updated.
    try:
        settings.EMBEDDING_DIMENSIONS = int(resolved["embedding_dims"])
        settings.EMBEDDING_MODEL = resolved["embed_id"] or "qwen3-embed"
        settings.MODEL_RERANKER_LOCAL = resolved["reranker_id"] or "qwen3-reranker"
        settings.LLM_MODEL = resolved["chat_id"] or settings.LLM_MODEL
        sync_embedding_infrastructure(
            dims=int(resolved["embedding_dims"]),
            embed_id=resolved["embed_id"] or None,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    man = load_manifest()
    man["selection"] = {
        **(man.get("selection") or {}),
        "chat_id": resolved["chat_id"],
        "embed_id": resolved["embed_id"],
        "reranker_id": resolved["reranker_id"],
        "embedding_dims": resolved["embedding_dims"],
        "chat_path": store_model_path(chat),
        "embed_path": store_model_path(embed),
        "reranker_path": store_model_path(rerank),
    }
    save_manifest(man)
    return {"chat": chat, "embed": embed, "reranker": rerank}


def store_model_path(path: Path | str) -> str:
    """How a selection path is written to the manifest.

    Relative to MODELS_DIR when the file lives under it, absolute otherwise.
    The models directory is meant to be the single place models are resolved
    from, so recording an absolute path inside it defeats the setting: move the
    directory and every selection points at a file that is no longer there,
    while the user is told the model "is not downloaded". Same rule per-KB
    model pins already use.
    """
    p = Path(path)
    try:
        return str(p.resolve().relative_to(resolve_models_dir().resolve()))
    except (ValueError, OSError):
        return str(p)


def selected_gguf(path_str: str | None) -> Path | None:
    """Resolve a manifest selection path against the current MODELS_DIR.

    Handles, in order: a relative path (the current format), an absolute path
    that still exists (a model kept outside MODELS_DIR), and — for manifests
    written before this was relative — the same basename under the current
    models directory, which is what makes a moved directory recoverable
    instead of looking like three missing downloads.
    """
    if not path_str:
        return None
    recorded = Path(path_str)
    if not recorded.is_absolute():
        resolved = resolve_models_dir() / recorded
        return resolved if resolved.exists() else None
    if recorded.exists():
        return recorded
    moved = resolve_models_dir() / "gguf" / recorded.name
    if moved.exists():
        logger.info(
            "[LocalModels] %s moved: %s → %s — rewriting manifest",
            recorded.name,
            recorded.parent,
            moved.parent,
        )
        return moved
    return None


def _heal_selection_paths(sel: dict) -> None:
    """Rewrite stale absolute paths in the manifest once they are resolved.

    Without this the fallback runs on every read and the manifest stays wrong,
    so the next thing to read it directly (or a fresh install reading an old
    file) still sees paths into a directory that no longer exists.
    """
    fixed = {}
    for key in ("chat_path", "embed_path", "reranker_path"):
        raw = sel.get(key)
        found = selected_gguf(raw)
        if not found or not raw:
            continue
        # Compare the *stored* form: `found` is always absolute, so comparing
        # it against an already-relative entry never matches and the manifest
        # gets rewritten on every read.
        stored = store_model_path(found)
        if stored != raw:
            fixed[key] = stored
    if not fixed:
        return
    try:
        man = load_manifest()
        current = man.get("selection") or {}
        current.update(fixed)
        man["selection"] = current
        save_manifest(man)
        logger.info("[LocalModels] Manifest paths repaired: %s", ", ".join(fixed))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[LocalModels] Could not repair manifest paths: %s", exc)


def gguf_paths_if_present() -> dict[str, Path] | None:
    """Return chat/embed paths from selection/manifest if files exist."""
    man = load_manifest()
    sel = man.get("selection") or {}
    chat = selected_gguf(sel.get("chat_path"))
    embed = selected_gguf(sel.get("embed_path"))
    if chat and embed:
        _heal_selection_paths(sel)
        if chat.stat().st_size > 1_000_000:
            out = {"chat": chat, "embed": embed}
            rp = selected_gguf(sel.get("reranker_path"))
            if rp:
                out["reranker"] = rp
            return out

    # Legacy fallback: env defaults
    chat = resolve_models_dir() / "gguf" / CHAT_MODEL_ID.rsplit("/", 1)[-1]
    embed = resolve_models_dir() / "gguf" / EMBED_MODEL_ID.rsplit("/", 1)[-1]
    if chat.exists() and chat.stat().st_size > 1_000_000 and embed.exists():
        return {"chat": chat, "embed": embed}
    return None


def reranker_gguf_path() -> Path | None:
    man = load_manifest()
    sel = man.get("selection") or {}
    rp = selected_gguf(sel.get("reranker_path"))
    if rp:
        return rp
    default = resolve_models_dir() / "gguf" / RERANK_MODEL_ID.rsplit("/", 1)[-1]
    if default.exists():
        return default
    return None


def _nvidia_smi_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def detect_llama_backend() -> dict:
    """
    Pick the best acceleration path for this machine.

    Override with:
      ORB_LLAMA_BACKEND=metal|cuda|vulkan|cpu|auto
      ORB_LLAMA_N_GPU_LAYERS=<int>   (-1 = all layers on GPU)
    """
    forced = (_env_first("ORB_LLAMA_BACKEND", "LIVEOS_LLAMA_BACKEND", default="auto") or "auto").lower().strip()
    n_gpu_env = _env_first("ORB_LLAMA_N_GPU_LAYERS", "LIVEOS_LLAMA_N_GPU_LAYERS")

    def _result(backend: str, n_gpu_layers: int, reason: str) -> dict:
        if n_gpu_env is not None and n_gpu_env != "":
            try:
                n_gpu_layers = int(n_gpu_env)
            except ValueError:
                pass
        return {
            "backend": backend,
            "n_gpu_layers": n_gpu_layers,
            "reason": reason,
            "install_hint": _install_hint(backend),
        }

    if forced in ("cpu", "metal", "cuda", "vulkan"):
        layers = 0 if forced == "cpu" else -1
        return _result(forced, layers, f"forced via ORB_LLAMA_BACKEND={forced}")

    system = sys.platform
    machine = platform.machine().lower()

    if system == "darwin":
        # Apple Silicon / Intel Mac — Metal when llama-cpp-python was built with it
        return _result("metal", -1, f"macOS {machine}: prefer Metal")

    if system in ("linux", "win32") and _nvidia_smi_available():
        return _result("cuda", -1, "NVIDIA GPU detected (nvidia-smi)")

    # Optional Vulkan on Linux/Windows when explicitly preferred later
    return _result("cpu", 0, "no GPU accelerator detected; using CPU")


def _install_hint(backend: str) -> str:
    if backend == "metal":
        return 'CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir'
    if backend == "cuda":
        return 'CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir'
    if backend == "vulkan":
        return 'CMAKE_ARGS="-DGGML_VULKAN=on" pip install llama-cpp-python --force-reinstall --no-cache-dir'
    return "pip install llama-cpp-python"


def _openaiish_chat_response(raw: dict, model: str) -> SimpleNamespace:
    """Map llama-cpp create_chat_completion dict → OpenAI-like object."""
    choice0 = (raw.get("choices") or [{}])[0]
    message = choice0.get("message") or {}
    content = message.get("content") or ""
    msg = SimpleNamespace(content=content, role=message.get("role") or "assistant")
    choice = SimpleNamespace(
        message=msg,
        finish_reason=choice0.get("finish_reason") or "stop",
        index=0,
    )
    return SimpleNamespace(
        id=raw.get("id") or "local-chat",
        model=model,
        choices=[choice],
        usage=raw.get("usage"),
    )


class _ChatCompletions:
    def __init__(self, runtime: "LocalLlamaRuntime", model_id: str):
        self._runtime = runtime
        self._model_id = model_id

    def create(self, **kwargs):
        messages = kwargs.get("messages") or []
        temperature = kwargs.get("temperature", 0.2)
        max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens")
        # response_format / extra_body accepted for API compat; llama.cpp JSON mode
        # is prompt-driven for our extraction path.
        return self._runtime.create_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=kwargs.get("model") or self._model_id,
        )


class _ChatNamespace:
    def __init__(self, runtime: "LocalLlamaRuntime", model_id: str):
        self.completions = _ChatCompletions(runtime, model_id)


class _ModelsNamespace:
    def __init__(self, model_ids: list[str]):
        self._ids = model_ids

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=mid) for mid in self._ids])


class LocalOpenAICompat:
    """Minimal OpenAI client surface backed by in-process llama-cpp-python."""

    def __init__(self, runtime: "LocalLlamaRuntime", model_id: str | None = None):
        self._runtime = runtime
        self._model_id = model_id or settings.LLM_MODEL or "local-chat"
        self.chat = _ChatNamespace(runtime, self._model_id)
        self.models = _ModelsNamespace([self._model_id])


class AsyncLocalOpenAICompat:
    """Async wrapper — runs sync llama.cpp calls in a thread pool."""

    def __init__(self, runtime: "LocalLlamaRuntime", model_id: str | None = None):
        self._sync = LocalOpenAICompat(runtime, model_id)
        self.models = self._sync.models

        class _AsyncCompletions:
            def __init__(self, sync_client: LocalOpenAICompat):
                self._sync = sync_client

            async def create(self, **kwargs):
                return await asyncio.to_thread(self._sync.chat.completions.create, **kwargs)

        class _AsyncChat:
            def __init__(self, sync_client: LocalOpenAICompat):
                self.completions = _AsyncCompletions(sync_client)

        self.chat = _AsyncChat(self._sync)


class LocalLlamaEmbeddings:
    """LangChain-style embed_query / embed_documents over the embed GGUF."""

    def __init__(self, runtime: "LocalLlamaRuntime"):
        self._runtime = runtime

    def embed_query(self, text: str) -> list[float]:
        return self._runtime.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._runtime.embed_batch(texts)


class LocalLlamaRuntime:
    """Process-local llama-cpp-python models (chat + optional embed)."""

    def __init__(self) -> None:
        self._chat = None
        self._embed = None
        self._lock = threading.RLock()
        self._chat_path: Path | None = None
        self._embed_path: Path | None = None
        self._embed_path_hint: Path | None = None
        self.accel: dict = detect_llama_backend()
        self._last_used = 0.0
        self._idle_watcher_started = False

    @property
    def loaded(self) -> bool:
        """True if chat GGUF is resident (setup / status alias)."""
        return self._chat is not None

    @property
    def any_gguf_loaded(self) -> bool:
        return self._chat is not None or self._embed is not None

    def status(self) -> dict:
        idle_for = (time.monotonic() - self._last_used) if self.any_gguf_loaded else None
        return {
            "loaded": self.loaded,
            "chat_loaded": self._chat is not None,
            "embed_loaded": self._embed is not None and self._embed is not self._chat,
            "chat_model": str(self._chat_path) if self._chat_path else None,
            "embed_model": str(self._embed_path) if self._embed_path else None,
            "accel": self.accel,
            "idle_seconds": round(idle_for, 1) if idle_for is not None else None,
            "idle_unload_after": model_idle_seconds(),
            "exclusive": True,
        }

    def _touch(self) -> None:
        self._last_used = time.monotonic()
        self._ensure_idle_watcher()

    def _ensure_idle_watcher(self) -> None:
        if self._idle_watcher_started:
            return
        self._idle_watcher_started = True

        def _loop() -> None:
            while True:
                time.sleep(30)
                limit = model_idle_seconds()
                if limit <= 0:
                    continue
                try:
                    self.unload_if_idle(limit)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(f"Idle unload check failed: {exc}")
                try:
                    local_gguf_reranker.unload_if_idle(limit)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(f"Reranker idle unload check failed: {exc}")
                try:
                    from app.services.chat_runtimes import unload_if_idle

                    unload_if_idle(limit)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(f"Chat-runtime idle unload check failed: {exc}")

        threading.Thread(
            target=_loop, name="orb-model-idle", daemon=True
        ).start()

    def _import_llama(self):
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError as exc:
            hint = self.accel.get("install_hint") or _install_hint("cpu")
            raise RuntimeError(
                "llama-cpp-python is not installed. "
                f"Install with GPU support if available:\n  {hint}"
            ) from exc
        return Llama

    def _chat_kwargs(self) -> dict:
        self.accel = detect_llama_backend()
        # content-machine: 16k + swa_full fits Metal; 32k + swa_full OOMs.
        n_ctx = _default_chat_n_ctx()
        max_tokens = _default_chat_max_tokens()
        prompt_reserve = int(_env_first("ORB_LLAMA_PROMPT_RESERVE", "LIVEOS_LLAMA_PROMPT_RESERVE", default="4096"))
        min_ctx = (max_tokens + prompt_reserve) if max_tokens else 0
        if n_ctx < min_ctx:
            logger.info(
                "Raising chat n_ctx %s → %s (max_tokens=%s + prompt_reserve=%s)",
                n_ctx,
                min_ctx,
                max_tokens,
                prompt_reserve,
            )
            n_ctx = min_ctx
        n_threads = _env_first("ORB_LLAMA_N_THREADS", "LIVEOS_LLAMA_N_THREADS")
        kwargs: dict = {
            "n_ctx": n_ctx,
            "n_gpu_layers": int(self.accel["n_gpu_layers"]),
            "verbose": False,
        }
        if n_threads:
            try:
                kwargs["n_threads"] = int(n_threads)
            except ValueError:
                pass
        return kwargs

    def _unload_peers_for_gguf(self, keep: str | None = None) -> None:
        """Unload every other heavy resident so only one model stays in RAM."""
        if keep != "rerank":
            try:
                local_gguf_reranker.unload()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("Reranker unload before GGUF skipped: %s", exc)
        if keep != "multimodal":
            _unload_multimodal_families()
        try:
            from app.services.chat_runtimes import unload_chat_runtimes

            unload_chat_runtimes()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Chat-runtime unload before GGUF skipped: %s", exc)

    def load(self, chat_gguf: Path, embed_gguf: Path | None = None) -> dict:
        """Load chat GGUF only (exclusive). Embed loads on demand and replaces chat.

        ``embed_gguf`` is recorded for later exclusive embed loads / dim probe.
        """
        with self._lock:
            self._unload_peers_for_gguf()
            self._unload_unlocked(release=False)
            result = self._load_chat_unlocked(Path(chat_gguf))
            # Probe embed dims without keeping chat+embed co-resident.
            embed_path = Path(embed_gguf) if embed_gguf else None
            if embed_path and embed_path != Path(chat_gguf):
                self._embed_path_hint = embed_path
                try:
                    self._unload_chat_unlocked(release=False)
                    self._load_embed_unlocked(embed_path)
                    sample = self._embed_unlocked("dimension probe")
                    self._unload_embed_unlocked(release=True)
                    # Restore chat for interactive use after setup.
                    self._load_chat_unlocked(Path(chat_gguf))
                    if sample:
                        sel = (load_manifest().get("selection") or {}).copy()
                        sync_embedding_infrastructure(
                            dims=len(sample),
                            embed_id=sel.get("embed_id") or None,
                        )
                        sel["embedding_dims"] = len(sample)
                        man2 = load_manifest()
                        man2["selection"] = {**(man2.get("selection") or {}), **sel}
                        save_manifest(man2)
                except Exception as probe_exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "Could not probe/sync embedding dims after load: %s", probe_exc
                    )
                    if self._chat is None:
                        try:
                            self._load_chat_unlocked(Path(chat_gguf))
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass
            else:
                self._embed_path_hint = Path(chat_gguf)
            man = load_manifest()
            man["runtime"] = {
                "engine": "llama-cpp-python",
                "backend": self.accel["backend"],
                "chat": str(self._chat_path),
                "embed": str(self._embed_path_hint or self._embed_path),
                "exclusive": True,
            }
            save_manifest(man)
            self._touch()
            result.update(
                {
                    "loaded": True,
                    "started": True,
                    "engine": "llama-cpp-python",
                    "backend": self.accel["backend"],
                    "n_gpu_layers": self._chat_kwargs()["n_gpu_layers"],
                    "reason": self.accel.get("reason"),
                    "install_hint": self.accel.get("install_hint"),
                    "chat_model": str(self._chat_path),
                    "embed_model": str(self._embed_path_hint or self._embed_path),
                    "idle_unload_after": model_idle_seconds(),
                    "exclusive": True,
                }
            )
            logger.info(f"Local LLM loaded (exclusive chat): {result}")
            return result

    def _load_chat_unlocked(self, chat_gguf: Path) -> dict:
        Llama = self._import_llama()
        chat_kwargs = self._chat_kwargs()
        chat_kwargs["n_ctx"] = _clamp_ctx_to_model(chat_gguf, chat_kwargs["n_ctx"])
        logger.info(
            "Loading chat GGUF in-process (%s, n_gpu_layers=%s, n_ctx=%s, swa_full=True): %s",
            self.accel["backend"],
            chat_kwargs["n_gpu_layers"],
            chat_kwargs["n_ctx"],
            chat_gguf,
        )
        started = time.perf_counter()
        self._chat = _construct_llama(
            Llama, model_path=str(chat_gguf), embedding=False, **chat_kwargs
        )
        model_load_clock.record("chat", time.perf_counter() - started)
        self._chat_path = Path(chat_gguf)
        self._embed = None
        self._embed_path = None
        return {"chat_model": str(self._chat_path), "n_ctx": chat_kwargs["n_ctx"]}

    def _load_embed_unlocked(self, embed_gguf: Path) -> None:
        Llama = self._import_llama()
        chat_kwargs = self._chat_kwargs()
        embed_kwargs = {
            **{k: v for k, v in chat_kwargs.items() if k != "n_ctx"},
            "n_ctx": int(_env_first("ORB_EMBED_N_CTX", "LIVEOS_EMBED_N_CTX", default="8192")),
        }
        logger.info(
            "Loading embed GGUF in-process (exclusive, n_ctx=%s): %s",
            embed_kwargs["n_ctx"],
            embed_gguf,
        )
        started = time.perf_counter()
        self._embed = _construct_llama(
            Llama, model_path=str(embed_gguf), embedding=True, **embed_kwargs
        )
        model_load_clock.record("embed", time.perf_counter() - started)
        self._embed_path = Path(embed_gguf)
        self._chat = None
        self._chat_path = None

    def _embed_unlocked(self, text: str) -> list[float]:
        assert self._embed is not None
        out = self._embed.create_embedding(input=text)
        data = out.get("data") if isinstance(out, dict) else None
        if data:
            return list(data[0]["embedding"])
        if isinstance(out, dict) and "embedding" in out:
            emb = out["embedding"]
            return list(emb[0] if emb and isinstance(emb[0], (list, tuple)) else emb)
        raise RuntimeError("Unexpected embedding response from llama-cpp-python")

    def _embed_batch_unlocked(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts in one create_embedding call.

        llama-cpp-python accepts a list input and returns one data entry per
        text, which avoids per-text ensure_loaded/lock/API overhead.
        """
        assert self._embed is not None
        out = self._embed.create_embedding(input=texts)
        data = out.get("data") if isinstance(out, dict) else None
        if data is not None and len(data) == len(texts):
            return [list(d["embedding"]) for d in data]
        # Length mismatch would silently mis-pair vectors with texts downstream —
        # fail loud so ingestion aborts instead of corrupting the index.
        raise RuntimeError(
            "Unexpected batch embedding response from llama-cpp-python "
            f"(expected {len(texts)} vectors, got {len(data) if data else 0})"
        )

    def ensure_loaded(self) -> None:
        """Back-compat: ensure chat GGUF is loaded (exclusive)."""
        self.ensure_chat_loaded()

    def ensure_chat_loaded(self, chat_gguf: Path | None = None) -> None:
        """Load chat GGUF only — unloads embed / rerank / multimodal first.

        ``chat_gguf`` selects a specific on-disk GGUF (per-KB model); ``None``
        means the Setup selection. A different path than the resident one swaps.
        """
        with self._lock:
            if self._chat is not None and (
                chat_gguf is None or Path(chat_gguf) == self._chat_path
            ):
                self._touch()
                return
            present = gguf_paths_if_present()
            if not present:
                raise RuntimeError(
                    "Local GGUF models are not downloaded. "
                    "Open Models to download one."
                )
            target = Path(chat_gguf) if chat_gguf else Path(present["chat"])
            if self._chat is not None:
                logger.info("Switching chat GGUF %s → %s", self._chat_path, target)
            self._unload_peers_for_gguf()
            self._unload_unlocked(release=True)
            self._load_chat_unlocked(target)
            self._embed_path_hint = present.get("embed") or present["chat"]
            self._touch()

    def resolve_chat_gguf(self, model: str | None) -> Path | None:
        """Map a catalog id or a GGUF path ref to a file on disk.

        Accepts three forms so any local model can be used, not only curated ones:
          * a catalog id            — ``gemma4-e4b-q4``
          * a MODELS_DIR-relative path — ``gguf/My-Model-Q4_K_M.gguf``
          * an absolute path        — ``/Volumes/x/My-Model.gguf``

        ``None`` means "use the Setup selection". A ref that names a specific
        model but cannot be satisfied raises, so a KB pinned to a model that was
        deleted or never downloaded fails loudly rather than silently answering
        with a different model.
        """
        name = (model or "").strip()
        if not name or name == "local-chat":
            return None
        from app.services.model_catalog import get_option
        from app.services.model_discovery import inspect_chat_model, resolve_model_ref

        opt = get_option(name)
        if opt is not None:
            if opt.role != "chat":
                raise RuntimeError(f"'{name}' is a {opt.role} model, not a chat model")
            candidate = resolve_models_dir() / "gguf" / opt.hf_file
            if _gguf_looks_complete(candidate, opt.hf_path):
                return candidate
            raise RuntimeError(
                f"Chat model '{opt.label}' is not downloaded. "
                "Download it on the Models page, or pick another model."
            )

        path_ref = resolve_model_ref(name)
        if path_ref is not None:
            _info, error = inspect_chat_model(path_ref)
            if error:
                raise RuntimeError(error)
            return path_ref
        # Folders (MLX / safetensors) are resolved by resolve_chat_model.

        if name != (settings.LLM_MODEL or ""):
            logger.warning("Unknown local chat model %r — using the Setup selection", name)
        return None

    def resolve_chat_model(self, model: str | None):
        """``(path, format)`` for any supported layout, or ``(None, None)``.

        Extends :meth:`resolve_chat_gguf` to folders: an MLX bundle or a plain
        Hugging Face checkpoint resolves here and is served by
        ``chat_runtimes``. ``(None, None)`` means "use the Setup selection".
        """
        from app.services import model_formats
        from app.services.model_discovery import resolve_model_ref

        name = (model or "").strip()
        if not name or name == "local-chat":
            return None, None

        # A catalog id or a .gguf path ref still goes through the GGUF path.
        try:
            gguf = self.resolve_chat_gguf(name)
        except RuntimeError:
            gguf = None
            if not Path(name).expanduser().is_dir():
                raise
        if gguf is not None:
            return gguf, model_formats.ModelFormat.GGUF

        candidate = resolve_model_ref(name)
        if candidate is None:
            candidate = Path(name).expanduser()
            if not candidate.is_absolute():
                candidate = resolve_models_dir() / name
        described = model_formats.describe(candidate)
        if described is None:
            return None, None
        if not described.runnable:
            raise RuntimeError(described.unsupported_reason)
        return model_formats.loadable_path(candidate, described.format), described.format

    def count_tokens(self, text: str) -> int:
        """Token count using whichever GGUF is resident; heuristic when none is.

        Never forces a model load — chunk sizing must not trigger a disk read.
        """
        if not text:
            return 0
        model = self._chat or self._embed
        if model is not None:
            try:
                return len(model.tokenize(text.encode("utf-8"), add_bos=False, special=True))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return len(text) // 4 + 1

    def _prompt_token_estimate(self, messages: list[dict]) -> int:
        """Approximate tokens the chat template will consume for ``messages``."""
        total = 4
        for msg in messages:
            content = msg.get("content") or ""
            total += self.count_tokens(content) + 8
        return total

    def _remaining_output_budget(self, messages: list[dict]) -> int:
        """Tokens left for generation once the prompt is in the context window."""
        assert self._chat is not None
        try:
            n_ctx = int(self._chat.n_ctx())
        except Exception:  # pylint: disable=broad-exception-caught
            n_ctx = _default_chat_n_ctx()
        prompt_tokens = self._prompt_token_estimate(messages)
        remaining = n_ctx - prompt_tokens - _GEN_SAFETY_MARGIN
        if remaining < _MIN_OUTPUT_TOKENS:
            raise PromptTooLongError(
                f"Prompt is ~{prompt_tokens} tokens; context window is {n_ctx}. "
                f"Fewer than {_MIN_OUTPUT_TOKENS} tokens would remain for the answer — "
                "split the input or raise ORB_LLAMA_N_CTX."
            )
        return remaining

    def ensure_embed_loaded(self) -> None:
        """Load embed GGUF only — unloads chat / rerank / multimodal first."""
        with self._lock:
            if self._embed is not None and self._embed is not self._chat:
                self._touch()
                return
            present = gguf_paths_if_present()
            if not present or not present.get("embed"):
                raise RuntimeError(
                    "Local embed GGUF is not downloaded. "
                    "Open Models to download one."
                )
            embed_path = Path(present["embed"])
            self._unload_peers_for_gguf()
            self._unload_chat_unlocked(release=True)
            self._load_embed_unlocked(embed_path)
            self._embed_path_hint = embed_path
            self._touch()

    def unload(self) -> None:
        with self._lock:
            self._unload_unlocked(release=True)

    def _unload_chat_unlocked(self, *, release: bool) -> None:
        if self._chat is None:
            return
        logger.info("Unloading in-process chat GGUF")
        _close_llama_handle(self._chat)
        self._chat = None
        self._chat_path = None
        if release:
            release_accelerator_memory()

    def _unload_embed_unlocked(self, *, release: bool) -> None:
        if self._embed is None:
            return
        if self._embed is self._chat:
            self._embed = None
            self._embed_path = None
            return
        logger.info("Unloading in-process embed GGUF")
        _close_llama_handle(self._embed)
        self._embed = None
        self._embed_path = None
        if release:
            release_accelerator_memory()

    def _unload_unlocked(self, release: bool = True) -> None:
        if self._chat is None and self._embed is None:
            return
        logger.info("Unloading in-process chat/embed GGUFs to free memory")
        _close_llama_handle(self._chat if self._chat is not self._embed else None)
        _close_llama_handle(self._embed)
        self._chat = None
        self._embed = None
        self._chat_path = None
        self._embed_path = None
        if release:
            release_accelerator_memory()

    def unload_if_idle(self, limit_seconds: float | None = None) -> bool:
        """Unload if unused longer than limit. Returns True if unloaded."""
        limit = model_idle_seconds() if limit_seconds is None else limit_seconds
        if limit <= 0:
            return False
        with self._lock:
            if not self.any_gguf_loaded:
                return False
            age = time.monotonic() - self._last_used
            if age < limit:
                return False
            logger.info(
                f"Unloading local GGUFs after {age:.0f}s idle (limit {limit:.0f}s)"
            )
            self._unload_unlocked(release=True)
            return True

    def create_chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> SimpleNamespace:
        from app.services import chat_runtimes
        from app.services.model_formats import ModelFormat

        target, model_format = self.resolve_chat_model(model)
        if model_format is not None and model_format is not ModelFormat.GGUF:
            # MLX / safetensors: a different backend answers, with the same
            # response shape, and evicts the GGUF models as it loads.
            runtime = chat_runtimes.runtime_for(model_format)
            runtime.ensure_loaded(target)
            return _openaiish_chat_response(
                runtime.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                ),
                model or settings.LLM_MODEL or "local-chat",
            )

        self.ensure_chat_loaded(target)
        assert self._chat is not None
        if max_tokens is None:
            max_tokens = _default_chat_max_tokens()
        # Size the answer to the context actually left, instead of a fixed cap
        # that truncates long extractions mid-JSON.
        budget = self._remaining_output_budget(messages)
        max_tokens = min(max_tokens, budget) if max_tokens else budget
        repeat_penalty = _default_repeat_penalty()
        model_id = model or settings.LLM_MODEL or "local-chat"
        last_error: Exception | None = None
        # content-machine: abort + retry ordinal/"or the" cascades (Gemma 4).
        for attempt in range(1, 4):
            try:
                raw = self._chat_completion_once(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    repeat_penalty=repeat_penalty,
                )
                return _openaiish_chat_response(raw, model_id)
            except RepetitionLoopError as exc:
                last_error = exc
                logger.warning(
                    "Chat repetition loop on attempt %s/3; retrying with fresh sample",
                    attempt,
                )
        raise RuntimeError(
            f"LLM repetition loop persisted after 3 attempts: {last_error}"
        )

    def _chat_completion_once(
        self,
        *,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        repeat_penalty: float,
    ) -> dict:
        assert self._chat is not None
        kwargs: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "repeat_penalty": repeat_penalty,
        }
        with self._lock:
            # Prefer streaming so we can abort mid-generation on ordinal loops.
            try:
                stream = self._chat.create_chat_completion(**kwargs, stream=True)
            except TypeError:
                raw = self._chat.create_chat_completion(**kwargs)
                self._touch()
                choice = (raw.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                text = (message.get("content") or "") + (
                    message.get("reasoning_content") or ""
                )
                _raise_if_degeneration(text)
                return raw

            parts: list[str] = []
            finish_reason: str | None = None
            for chunk in stream:
                try:
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                except (AttributeError, IndexError, TypeError):
                    continue
                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if not piece:
                    continue
                parts.append(piece)
                if len(parts) % 32 == 0:
                    so_far = "".join(parts)
                    if _ORDINAL_LOOP_RE.search(so_far):
                        logger.warning(
                            "Aborting chat stream: ordinal/or-the repetition detected"
                        )
                        raise RepetitionLoopError(
                            "LLM entered a repetition loop (ordinal/or-the cascade). "
                            "Retrying with a fresh sample."
                        )
            self._touch()
            text = "".join(parts)
            _raise_if_degeneration(text)
            if finish_reason == "length":
                logger.warning(
                    "Chat generation hit max_tokens=%s — output is truncated", max_tokens
                )
            return {
                "id": "local-chat",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": finish_reason or "stop",
                        "index": 0,
                    }
                ],
            }

    def embed(self, text: str) -> list[float]:
        self.ensure_embed_loaded()
        assert self._embed is not None
        with self._lock:
            out = self._embed_unlocked(text)
            self._touch()
            return out

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts with one residency check, one lock, one llama call."""
        if not texts:
            return []
        self.ensure_embed_loaded()
        assert self._embed is not None
        with self._lock:
            out = self._embed_batch_unlocked(texts)
            self._touch()
            return out

    def make_chat_clients(self):
        """Return (chat_client, async_chat_client, extraction_client) OpenAI-compat shims."""
        sync = LocalOpenAICompat(self)
        async_client = AsyncLocalOpenAICompat(self)
        return sync, async_client, sync


local_llama_runtime = LocalLlamaRuntime()


_RERANK_SYSTEM = (
    "Judge whether the Document meets the requirements based on the Query and the "
    'Instruct provided. Note that the answer can only be "yes" or "no".'
)
_RERANK_INSTRUCTION = (
    "Given a question, retrieve relevant passages that answer the question"
)


class LocalGgufReranker:
    """In-process Qwen3-Reranker via llama-cpp-python (yes/no logit scoring)."""

    def __init__(self) -> None:
        self._model = None
        self._path: Path | None = None
        self._lock = threading.RLock()
        self._yes_id: int | None = None
        self._no_id: int | None = None
        self._last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def ensure_loaded(self) -> bool:
        path = reranker_gguf_path()
        if not path:
            return False
        if self._model is not None and self._path == path:
            self._last_used = time.monotonic()
            return True
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError:
            logger.warning("llama-cpp-python missing; cannot load GGUF reranker")
            return False
        accel = detect_llama_backend()
        with self._lock:
            # Exclusive: drop chat/embed + multimodal before loading reranker.
            try:
                local_llama_runtime.unload()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("Chat/embed unload before rerank skipped: %s", exc)
            _unload_multimodal_families()
            if self._model is not None:
                _close_llama_handle(self._model)
                self._model = None
                release_accelerator_memory()
            logger.info(f"Loading reranker GGUF (exclusive): {path}")
            default_ctx = "8192"
            started = time.perf_counter()
            self._model = _construct_llama(
                Llama,
                model_path=str(path),
                n_ctx=int(_env_first("ORB_RERANK_N_CTX", "LIVEOS_RERANK_N_CTX", default=str(default_ctx))),
                n_gpu_layers=int(accel.get("n_gpu_layers", 0)),
                logits_all=True,
                verbose=False,
            )
            model_load_clock.record("rerank", time.perf_counter() - started)
            self._path = path
            # Resolve yes/no token ids
            try:
                self._yes_id = self._model.tokenize(b"yes", add_bos=False)[-1]
                self._no_id = self._model.tokenize(b"no", add_bos=False)[-1]
            except Exception:  # pylint: disable=broad-exception-caught
                self._yes_id = None
                self._no_id = None
            self._last_used = time.monotonic()
            local_llama_runtime._ensure_idle_watcher()  # pylint: disable=protected-access
        return True

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            logger.info("Unloading in-process reranker GGUF to free memory")
            _close_llama_handle(self._model)
            self._model = None
            self._path = None
            self._yes_id = None
            self._no_id = None
            release_accelerator_memory()

    def unload_if_idle(self, limit_seconds: float | None = None) -> bool:
        limit = model_idle_seconds() if limit_seconds is None else limit_seconds
        if limit <= 0:
            return False
        with self._lock:
            if self._model is None:
                return False
            age = time.monotonic() - self._last_used
            if age < limit:
                return False
            logger.info(
                f"Unloading reranker GGUF after {age:.0f}s idle (limit {limit:.0f}s)"
            )
            _close_llama_handle(self._model)
            self._model = None
            self._path = None
            self._yes_id = None
            self._no_id = None
            release_accelerator_memory()
            return True

    def _score_one(self, query: str, document: str) -> float:
        assert self._model is not None
        prompt = (
            f"<|im_start|>system\n{_RERANK_SYSTEM}\n<|im_end|>\n"
            f"<|im_start|>user\n"
            f"<Instruct>: {_RERANK_INSTRUCTION}\n"
            f"<Query>: {query}\n\n"
            f"<Document>: {document}\n"
            f"<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n"
        )
        with self._lock:
            out = self._model(
                prompt,
                max_tokens=1,
                temperature=0.0,
                logprobs=5,
            )
            self._last_used = time.monotonic()
        # Prefer logprob yes vs no when available
        try:
            choice = out["choices"][0]
            logprobs = choice.get("logprobs") or {}
            top = (logprobs.get("top_logprobs") or [{}])[0]
            if isinstance(top, dict) and top:
                # keys may be token strings
                yes_lp = top.get("yes") or top.get("Yes")
                no_lp = top.get("no") or top.get("No")
                if yes_lp is not None and no_lp is not None:
                    import math

                    ey, en = math.exp(yes_lp), math.exp(no_lp)
                    return float(ey / (ey + en + 1e-12))
            text = (choice.get("text") or "").strip().lower()
            if text.startswith("yes"):
                return 0.9
            if text.startswith("no"):
                return 0.1
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return 0.0

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict]:
        if not documents:
            return []
        if not self.ensure_loaded():
            return []
        scored = []
        for i, doc in enumerate(documents):
            score = self._score_one(query, doc)
            # Use relevance_score to match the HTTP local-models service contract
            # (retrieval.py reads r["relevance_score"]).
            scored.append(
                {
                    "index": i,
                    "relevance_score": score,
                    "score": score,
                    "document": doc,
                }
            )
        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        if top_n is not None:
            scored = scored[:top_n]
        return scored


local_gguf_reranker = LocalGgufReranker()
