"""Resource-aware local model catalog (chat + Qwen3 embed + Qwen3 reranker)."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Literal

Role = Literal["chat", "embed", "reranker"]
Family = Literal["gemma4", "qwen35", "qwen36", "qwen3-embed", "qwen3-rerank"]


@dataclass(frozen=True)
class ModelOption:
    id: str
    role: Role
    family: Family
    label: str
    hf_repo: str
    hf_file: str
    # Approximate peak working-set GB (weights + ctx overhead)
    min_ram_gb: float
    size_gb: float
    params: str
    embedding_dims: int | None = None  # embed models only
    recommended: bool = False

    @property
    def hf_path(self) -> str:
        return f"{self.hf_repo}/{self.hf_file}"


# ── Catalog (Q4_K_M unless noted) ─────────────────────────────────────────────

CHAT_MODELS: list[ModelOption] = [
    ModelOption(
        "gemma4-e2b-q4",
        "chat",
        "gemma4",
        "Gemma 4 E2B (Q4)",
        "bartowski/google_gemma-4-E2B-it-GGUF",
        "google_gemma-4-E2B-it-Q4_K_M.gguf",
        min_ram_gb=5,
        size_gb=2.8,
        params="E2B",
    ),
    ModelOption(
        "gemma4-e4b-q4",
        "chat",
        "gemma4",
        "Gemma 4 E4B (Q4)",
        "bartowski/google_gemma-4-E4B-it-GGUF",
        "google_gemma-4-E4B-it-Q4_K_M.gguf",
        min_ram_gb=9,
        size_gb=5.4,
        params="E4B",
        recommended=True,
    ),
    ModelOption(
        "gemma4-12b-q4",
        "chat",
        "gemma4",
        "Gemma 4 12B (Q4)",
        "bartowski/gemma-4-12B-it-GGUF",
        "gemma-4-12B-it-Q4_K_M.gguf",
        # Q4 weights ~8 GB; Metal unified memory runs this comfortably on 16 GB+
        min_ram_gb=11,
        size_gb=7.7,
        params="12B",
    ),
    ModelOption(
        "gemma4-31b-q4",
        "chat",
        "gemma4",
        "Gemma 4 31B (Q4)",
        "bartowski/google_gemma-4-31B-it-GGUF",
        "google_gemma-4-31B-it-Q4_K_M.gguf",
        min_ram_gb=28,
        size_gb=18.0,
        params="31B",
    ),
    ModelOption(
        "qwen35-0.8b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 0.8B (Q4)",
        "bartowski/Qwen_Qwen3.5-0.8B-GGUF",
        "Qwen_Qwen3.5-0.8B-Q4_K_M.gguf",
        min_ram_gb=2.5,
        size_gb=0.6,
        params="0.8B",
    ),
    ModelOption(
        "qwen35-2b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 2B (Q4)",
        "bartowski/Qwen_Qwen3.5-2B-GGUF",
        "Qwen_Qwen3.5-2B-Q4_K_M.gguf",
        min_ram_gb=3.5,
        size_gb=1.4,
        params="2B",
    ),
    ModelOption(
        "qwen35-4b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 4B (Q4)",
        "bartowski/Qwen_Qwen3.5-4B-GGUF",
        "Qwen_Qwen3.5-4B-Q4_K_M.gguf",
        min_ram_gb=5,
        size_gb=2.5,
        params="4B",
    ),
    ModelOption(
        "qwen35-9b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 9B (Q4)",
        "bartowski/Qwen_Qwen3.5-9B-GGUF",
        "Qwen_Qwen3.5-9B-Q4_K_M.gguf",
        min_ram_gb=10,
        size_gb=5.5,
        params="9B",
    ),
    ModelOption(
        "qwen35-27b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 27B (Q4)",
        "bartowski/Qwen_Qwen3.5-27B-GGUF",
        "Qwen_Qwen3.5-27B-Q4_K_M.gguf",
        min_ram_gb=28,
        size_gb=18.0,
        params="27B",
    ),
    ModelOption(
        "qwen35-35b-a3b-q4",
        "chat",
        "qwen35",
        "Qwen 3.5 35B-A3B MoE (Q4)",
        "bartowski/Qwen_Qwen3.5-35B-A3B-GGUF",
        "Qwen_Qwen3.5-35B-A3B-Q4_K_M.gguf",
        min_ram_gb=26,
        size_gb=20.8,
        params="35B-A3B",
    ),
    ModelOption(
        "qwen36-27b-q4",
        "chat",
        "qwen36",
        "Qwen 3.6 27B (Q4)",
        "bartowski/Qwen_Qwen3.6-27B-GGUF",
        "Qwen_Qwen3.6-27B-Q4_K_M.gguf",
        min_ram_gb=22,
        size_gb=16.7,
        params="27B",
    ),
    # Qwen 3.6 ships only at 27B and 35B-A3B, so Q3 is how the newest family
    # fits a 24 GB machine at all — a smaller quant of a larger model
    # generally beats a larger quant of a much smaller one.
    ModelOption(
        "qwen36-27b-q3",
        "chat",
        "qwen36",
        "Qwen 3.6 27B (Q3)",
        "bartowski/Qwen_Qwen3.6-27B-GGUF",
        "Qwen_Qwen3.6-27B-Q3_K_M.gguf",
        min_ram_gb=17,
        size_gb=13.8,
        params="27B",
    ),
    ModelOption(
        "qwen36-35b-a3b-q4",
        "chat",
        "qwen36",
        "Qwen 3.6 35B-A3B MoE (Q4)",
        "bartowski/Qwen_Qwen3.6-35B-A3B-GGUF",
        "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
        min_ram_gb=26,
        size_gb=20.8,
        params="35B-A3B",
    ),
    # Only ~3B parameters are active per token, so this generates far faster
    # than a dense 27B while all experts stay resident.
    ModelOption(
        "qwen36-35b-a3b-q3",
        "chat",
        "qwen36",
        "Qwen 3.6 35B-A3B MoE (Q3)",
        "bartowski/Qwen_Qwen3.6-35B-A3B-GGUF",
        "Qwen_Qwen3.6-35B-A3B-Q3_K_M.gguf",
        min_ram_gb=19,
        size_gb=15.9,
        params="35B-A3B",
    ),
]

EMBED_MODELS: list[ModelOption] = [
    ModelOption(
        "qwen3-embed-0.6b-q8",
        "embed",
        "qwen3-embed",
        "Qwen3 Embedding 0.6B (Q8)",
        "Qwen/Qwen3-Embedding-0.6B-GGUF",
        "Qwen3-Embedding-0.6B-Q8_0.gguf",
        min_ram_gb=2,
        size_gb=0.6,
        params="0.6B",
        embedding_dims=1024,
        recommended=True,
    ),
    ModelOption(
        "qwen3-embed-4b-q4",
        "embed",
        "qwen3-embed",
        "Qwen3 Embedding 4B (Q4)",
        "Qwen/Qwen3-Embedding-4B-GGUF",
        "Qwen3-Embedding-4B-Q4_K_M.gguf",
        min_ram_gb=5,
        size_gb=2.5,
        params="4B",
        embedding_dims=2560,
    ),
    ModelOption(
        "qwen3-embed-8b-q4",
        "embed",
        "qwen3-embed",
        "Qwen3 Embedding 8B (Q4)",
        "Qwen/Qwen3-Embedding-8B-GGUF",
        "Qwen3-Embedding-8B-Q4_K_M.gguf",
        min_ram_gb=8,
        size_gb=4.8,
        params="8B",
        embedding_dims=4096,
    ),
]

RERANK_MODELS: list[ModelOption] = [
    ModelOption(
        "qwen3-rerank-0.6b-q4",
        "reranker",
        "qwen3-rerank",
        "Qwen3 Reranker 0.6B (Q4)",
        "mradermacher/Qwen3-Reranker-0.6B-GGUF",
        "Qwen3-Reranker-0.6B.Q4_K_M.gguf",
        min_ram_gb=2,
        size_gb=0.4,
        params="0.6B",
        recommended=True,
    ),
    ModelOption(
        "qwen3-rerank-4b-q4",
        "reranker",
        "qwen3-rerank",
        "Qwen3 Reranker 4B (Q4)",
        "mradermacher/Qwen3-Reranker-4B-GGUF",
        "Qwen3-Reranker-4B.Q4_K_M.gguf",
        min_ram_gb=6,
        size_gb=2.5,
        params="4B",
    ),
    ModelOption(
        "qwen3-rerank-8b-q4",
        "reranker",
        "qwen3-rerank",
        "Qwen3 Reranker 8B (Q4)",
        "mradermacher/Qwen3-Reranker-8B-GGUF",
        "Qwen3-Reranker-8B.Q4_K_M.gguf",
        min_ram_gb=10,
        size_gb=5.0,
        params="8B",
    ),
]

ALL_MODELS: dict[str, ModelOption] = {
    m.id: m for m in (*CHAT_MODELS, *EMBED_MODELS, *RERANK_MODELS)
}


def get_option(model_id: str) -> ModelOption | None:
    return ALL_MODELS.get(model_id)


# ── Hardware ──────────────────────────────────────────────────────────────────


def total_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "tp", "ap", "tv", "av", "ave")
                ]

            status = _MemStatus(dwLength=ctypes.sizeof(_MemStatus))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.total / (1024**3)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except Exception:  # pylint: disable=broad-exception-caught
        return 8.0  # conservative fallback


def detect_accel_backend() -> dict:
    """Lightweight accel detect (avoids importing local_models)."""
    from app.core.config import settings

    forced = (settings.LLAMA_BACKEND or "auto").lower().strip()
    env_layers = settings.LLAMA_N_GPU_LAYERS
    n_gpu = -1 if env_layers is None else int(env_layers)
    if forced in ("cpu", "metal", "cuda", "vulkan"):
        return {
            "backend": forced,
            "n_gpu_layers": 0 if forced == "cpu" else n_gpu if n_gpu != -1 else -1,
            "reason": f"forced in Settings: {forced}",
        }
    if sys.platform == "darwin":
        return {
            "backend": "metal",
            "n_gpu_layers": n_gpu,
            "reason": f"macOS {platform.machine()}: prefer Metal",
        }
    if shutil.which("nvidia-smi"):
        return {
            "backend": "cuda",
            "n_gpu_layers": n_gpu,
            "reason": "NVIDIA GPU detected",
        }
    return {"backend": "cpu", "n_gpu_layers": 0, "reason": "CPU fallback"}


def hardware_profile() -> dict:
    ram = round(total_ram_gb(), 1)
    accel = detect_accel_backend()
    # Apple Silicon / CUDA share VRAM with the OS more efficiently than the old
    # 25% haircut assumed. Keep a modest OS + Electron + search reserve.
    if accel.get("backend") in ("metal", "cuda"):
        usable = max(4.0, ram * 0.88)
    else:
        usable = max(3.0, ram * 0.75)
    return {
        "ram_gb": ram,
        "usable_model_gb": round(usable, 1),
        "platform": sys.platform,
        "machine": platform.machine(),
        "accel": accel,
    }


def _fits(opt: ModelOption, budget: float) -> bool:
    # Prefer on-disk size (+ small runtime headroom) over pessimistic min_ram.
    return min(opt.min_ram_gb, opt.size_gb * 1.35) <= budget


def pick_embed_for_budget(total_ram: float) -> ModelOption:
    """Qwen3 embed tier from total system RAM (original design, sized up)."""
    if total_ram >= 48:
        return next(m for m in EMBED_MODELS if m.id == "qwen3-embed-8b-q4")
    if total_ram >= 24:
        return next(m for m in EMBED_MODELS if m.id == "qwen3-embed-4b-q4")
    return next(m for m in EMBED_MODELS if m.id == "qwen3-embed-0.6b-q8")


def pick_rerank_for_budget(total_ram: float) -> ModelOption:
    """Qwen3 reranker tier from total system RAM (original design, sized up)."""
    if total_ram >= 48:
        return next(m for m in RERANK_MODELS if m.id == "qwen3-rerank-8b-q4")
    if total_ram >= 24:
        return next(m for m in RERANK_MODELS if m.id == "qwen3-rerank-4b-q4")
    return next(m for m in RERANK_MODELS if m.id == "qwen3-rerank-0.6b-q4")


def chat_options_for_budget(
    usable: float, embed: ModelOption, rerank: ModelOption
) -> list[ModelOption]:
    # Embed/rerank are not fully resident at chat peak the same way — reserve a
    # modest concurrent slice instead of subtracting their full sizes.
    reserve = min(2.5, embed.size_gb * 0.5 + rerank.size_gb * 0.5)
    budget = max(usable - reserve, usable * 0.7)
    fits = [m for m in CHAT_MODELS if _fits(m, budget)]
    if fits:
        return fits
    # Last resort: offer the smallest chat even if tight
    smallest = min(CHAT_MODELS, key=lambda m: m.min_ram_gb)
    return [smallest]


def recommend_chat(fits: list[ModelOption]) -> ModelOption | None:
    if not fits:
        return None
    # Prefer Gemma 4 E4B when it fits (historical Orb quality default);
    # then 12B when the machine clearly has room.
    for preferred in (
        "gemma4-e4b-q4",
        "gemma4-12b-q4",
        "gemma4-e2b-q4",
        "qwen36-35b-a3b-q3",
        "qwen36-27b-q3",
        "qwen35-4b-q4",
        "qwen35-9b-q4",
        "qwen36-27b-q4",
    ):
        for m in fits:
            if m.id == preferred:
                return m
    return max(fits, key=lambda m: m.min_ram_gb)


def chat_model_downloaded(opt: ModelOption) -> bool:
    """True when this catalog GGUF is complete on disk (usable per-KB without a download)."""
    try:
        from app.core.paths import resolve_models_dir
        from app.services.local_models import _gguf_looks_complete

        return _gguf_looks_complete(resolve_models_dir() / "gguf" / opt.hf_file, opt.hf_path)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def downloaded_chat_models() -> list[ModelOption]:
    return [m for m in CHAT_MODELS if chat_model_downloaded(m)]


def recommend_stack(chat_id: str | None = None) -> dict:
    """Return hardware + filtered options + auto embed/rerank + suggested chat."""
    hw = hardware_profile()
    usable = hw["usable_model_gb"]
    total = hw["ram_gb"]
    embed = pick_embed_for_budget(total)
    rerank = pick_rerank_for_budget(total)
    chats = chat_options_for_budget(usable, embed, rerank)
    fit_ids = {c.id for c in chats}
    # Always expose the full catalog so power users can pick larger models
    # (e.g. Gemma 12B) even if the conservative budget excludes them.
    all_chats: list[dict] = []
    for c in CHAT_MODELS:
        row = asdict(c)
        row["fits_budget"] = c.id in fit_ids
        row["downloaded"] = chat_model_downloaded(c)
        all_chats.append(row)
    suggested = None
    if chat_id and get_option(chat_id):
        suggested = get_option(chat_id)
    else:
        suggested = recommend_chat(chats)

    # Prefer the user's last saved Setup selection over the hardware suggestion.
    selected = None
    try:
        from app.services.local_models import load_manifest

        saved_id = ((load_manifest().get("selection") or {}).get("chat_id") or "").strip()
        if saved_id:
            selected = get_option(saved_id)
    except Exception:  # pylint: disable=broad-exception-caught
        selected = None

    def _ser(m: ModelOption | None) -> dict | None:
        return asdict(m) if m else None

    return {
        "hardware": hw,
        "embed": _ser(embed),
        "reranker": _ser(rerank),
        # Primary list: budget fitters + any explicitly selected id
        "chat_options": all_chats,
        "suggested_chat": _ser(suggested),
        "selected_chat": _ser(selected),
        "budget_note": (
            f"Usable ~{usable:.0f} GB of {total:.0f} GB RAM "
            f"(backend={hw['accel'].get('backend')}). "
            "Qwen3 embed + reranker are sized automatically (required for search, not chat). "
            "Only the chat model you select is downloaded. "
            "Models marked “may be tight” can still be chosen if you know they run on this machine."
        ),
    }
