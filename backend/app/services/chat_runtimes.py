"""Chat backends for local models that are not GGUF.

llama-cpp-python covers GGUF on every platform. These two cover the other
layouts a user can point Orb at:

* :class:`MlxChatRuntime` — ``mlx_lm`` on Apple Silicon. Runs on the GPU with
  far less memory pressure than the PyTorch path.
* :class:`TransformersChatRuntime` — ``AutoModelForCausalLM``. Works anywhere
  torch does, and reuses the multimedia extras Orb already installs.

Both obey the same rule as the GGUF runtime: **one heavy model resident at a
time**. Loading either evicts the GGUF chat/embed models, the reranker and the
multimodal stack, and loading any of those evicts these.

Both expose the subset of ``LocalLlamaRuntime`` that ``llm.py`` uses, so the
OpenAI-compatible shim does not know or care which backend answered.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app.core.log import get_logger
from app.services.model_formats import ModelFormat

logger = get_logger("LocalModels")

# Generation budget mirrors the GGUF path: size the answer to the context left
# rather than a fixed cap that truncates long extractions mid-JSON.
_GEN_SAFETY_MARGIN = 32
_MIN_OUTPUT_TOKENS = 256
_FALLBACK_CONTEXT = 8192


def _release_memory() -> None:
    from app.services.local_models import release_accelerator_memory

    release_accelerator_memory()


def _chat_response(text: str, model_id: str, finish_reason: str = "stop") -> dict:
    """The llama-cpp-python response shape the OpenAI shim already understands."""
    return {
        "id": "local-chat",
        "model": model_id,
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
                "index": 0,
            }
        ],
    }


class _BaseChatRuntime:
    """Shared residency bookkeeping for the non-GGUF chat backends."""

    kind: str = "base"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model: Any = None
        self._tokenizer: Any = None
        self._path: Path | None = None
        self._last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def path(self) -> Path | None:
        return self._path

    def _evict_peers(self) -> None:
        """Drop every other heavy model before taking accelerator memory."""
        from app.services.local_models import local_gguf_reranker, local_llama_runtime

        for unload in (
            local_llama_runtime.unload,
            local_gguf_reranker.unload,
        ):
            try:
                unload()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("Peer unload before %s load skipped: %s", self.kind, exc)
        unload_chat_runtimes(keep=self)

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            logger.info("Unloading in-process %s model", self.kind)
            self._model = None
            self._tokenizer = None
            self._path = None
            _release_memory()

    def unload_if_idle(self, limit_seconds: float) -> bool:
        if limit_seconds <= 0:
            return False
        with self._lock:
            if self._model is None:
                return False
            age = time.monotonic() - self._last_used
            if age < limit_seconds:
                return False
            logger.info(
                "Unloading %s model after %.0fs idle (limit %.0fs)",
                self.kind,
                age,
                limit_seconds,
            )
            self.unload()
            return True

    def _touch(self) -> None:
        self._last_used = time.monotonic()

    # -- prompt handling ---------------------------------------------------

    def _render_prompt(self, messages: list[dict]) -> str:
        """Apply the model's own chat template, or a readable fallback."""
        tokenizer = self._tokenizer
        try:
            if tokenizer is not None and getattr(tokenizer, "chat_template", None):
                return tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Chat template failed (%s); using a plain prompt", exc)
        parts = [
            f"{(m.get('role') or 'user').capitalize()}: {m.get('content') or ''}"
            for m in messages
        ]
        parts.append("Assistant:")
        return "\n\n".join(parts)

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        tokenizer = self._tokenizer
        if tokenizer is not None:
            try:
                return len(tokenizer.encode(text))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return len(text) // 4 + 1

    def context_length(self) -> int:
        config = getattr(self._model, "config", None)
        for attr in ("max_position_embeddings", "max_seq_len", "n_positions"):
            value = getattr(config, attr, None)
            if isinstance(value, int) and value > 0:
                return value
        return _FALLBACK_CONTEXT

    def _output_budget(self, prompt: str, max_tokens: int | None) -> int:
        from app.services.local_models import PromptTooLongError

        remaining = self.context_length() - self.count_tokens(prompt) - _GEN_SAFETY_MARGIN
        if remaining < _MIN_OUTPUT_TOKENS:
            raise PromptTooLongError(
                f"Prompt is ~{self.count_tokens(prompt)} tokens; the model's context "
                f"is {self.context_length()}. Fewer than {_MIN_OUTPUT_TOKENS} tokens "
                "would remain for the answer — split the input."
            )
        return min(max_tokens, remaining) if max_tokens else remaining


class MlxChatRuntime(_BaseChatRuntime):
    """Chat through ``mlx_lm`` on Apple Silicon."""

    kind = "mlx"

    def ensure_loaded(self, model_path: Path) -> None:
        with self._lock:
            if self._model is not None and self._path == model_path:
                self._touch()
                return
            try:
                from mlx_lm import load  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "This is an MLX model, which needs mlx-lm: "
                    "pip install 'mlx-lm>=0.20'  (Apple Silicon only)"
                ) from exc
            self._evict_peers()
            self.unload()
            logger.info("Loading MLX chat model: %s", model_path)
            started = time.perf_counter()
            self._model, self._tokenizer = load(str(model_path))
            self._path = model_path
            from app.services.local_models import model_load_clock

            model_load_clock.record("chat", time.perf_counter() - started)
            self._touch()

    def create_chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        from mlx_lm import generate  # type: ignore
        from mlx_lm.sample_utils import make_sampler  # type: ignore

        assert self._model is not None
        with self._lock:
            prompt = self._render_prompt(messages)
            budget = self._output_budget(prompt, max_tokens)
            text = generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=budget,
                sampler=make_sampler(temp=temperature),
                verbose=False,
            )
            self._touch()
        return _chat_response((text or "").strip(), model or "local-mlx")


class TransformersChatRuntime(_BaseChatRuntime):
    """Chat through ``transformers`` for plain Hugging Face checkpoints."""

    kind = "transformers"

    def ensure_loaded(self, model_path: Path) -> None:
        with self._lock:
            if self._model is not None and self._path == model_path:
                self._touch()
                return
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as exc:
                raise RuntimeError(
                    "This is a safetensors model, which needs the multimedia "
                    "extras: pip install -r backend/requirements-multimodal.txt"
                ) from exc
            self._evict_peers()
            self.unload()

            from app.core.inference_device import resolve_torch_device

            device = resolve_torch_device()
            logger.info("Loading safetensors chat model on %s: %s", device, model_path)
            started = time.perf_counter()
            self._tokenizer = AutoTokenizer.from_pretrained(str(model_path))
            self._model = (
                AutoModelForCausalLM.from_pretrained(
                    str(model_path), low_cpu_mem_usage=True
                )
                .to(device)
                .eval()
            )
            self._device = device
            self._path = model_path
            from app.services.local_models import model_load_clock

            model_load_clock.record("chat", time.perf_counter() - started)
            self._touch()

    def create_chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        import torch

        assert self._model is not None and self._tokenizer is not None
        with self._lock:
            prompt = self._render_prompt(messages)
            budget = self._output_budget(prompt, max_tokens)
            inputs = self._tokenizer(prompt, return_tensors="pt").to(
                getattr(self, "_device", "cpu")
            )
            with torch.no_grad():
                generated = self._model.generate(
                    **inputs,
                    max_new_tokens=budget,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-5),
                    pad_token_id=self._tokenizer.pad_token_id
                    or self._tokenizer.eos_token_id,
                )
            # Only the continuation, not the prompt echoed back.
            new_tokens = generated[0][inputs["input_ids"].shape[-1] :]
            text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)
            finish = "length" if len(new_tokens) >= budget else "stop"
            self._touch()
        if finish == "length":
            logger.warning("Generation hit max_new_tokens=%s — output is truncated", budget)
        return _chat_response(text.strip(), model or "local-transformers", finish)


mlx_chat_runtime = MlxChatRuntime()
transformers_chat_runtime = TransformersChatRuntime()

_RUNTIME_FOR_FORMAT = {
    ModelFormat.MLX: mlx_chat_runtime,
    ModelFormat.TRANSFORMERS: transformers_chat_runtime,
}


def runtime_for(model_format: ModelFormat) -> _BaseChatRuntime | None:
    """The runtime that serves a non-GGUF layout, if any."""
    return _RUNTIME_FOR_FORMAT.get(model_format)


def unload_chat_runtimes(keep: object | None = None) -> None:
    """Free every non-GGUF chat model except ``keep`` (exclusive residency)."""
    for runtime in (mlx_chat_runtime, transformers_chat_runtime):
        if runtime is keep:
            continue
        try:
            runtime.unload()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Unload of %s runtime skipped: %s", runtime.kind, exc)


def any_loaded() -> bool:
    return mlx_chat_runtime.loaded or transformers_chat_runtime.loaded


def unload_if_idle(limit_seconds: float) -> bool:
    freed = False
    for runtime in (mlx_chat_runtime, transformers_chat_runtime):
        freed = runtime.unload_if_idle(limit_seconds) or freed
    return freed
