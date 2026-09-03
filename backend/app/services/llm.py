"""Multi-provider LLM service supporting chat, structured extraction, and ingestion routing."""

# pylint: disable=too-many-lines,wrong-import-order,import-outside-toplevel
import asyncio
import re
from typing import Optional, Type

import instructor
from app.core.config import settings
from app.core.log import get_logger
from google import genai
from google.genai import types
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

logger = get_logger("LLMService")


class LLMService:
    """Multi-provider LLM client supporting structured extraction, generation, and ingestion routing."""

    def __init__(
        self,
        provider: str | None = None,
        *,
        chat_model: str | None = None,
        ingestion_model: str | None = None,
        ingestion_provider: str | None = None,
    ):
        # Declare client attributes upfront so they are always present on the
        # instance regardless of which provider branch _init_clients() takes.
        self.extraction_client = None
        self.chat_client = None
        self.async_chat_client = None

        # Per-KB instances pin their models here; the global service leaves these
        # unset and reads ``settings`` (Setup / Settings page) instead.
        self._chat_model_override = (chat_model or "").strip() or None
        self._ingestion_model_override = (ingestion_model or "").strip() or None
        _ip = (ingestion_provider or "").strip().lower() or None
        if _ip in ("ollama", "lm_studio"):
            _ip = "local"
        self._ingestion_provider_override = _ip

        # Map deprecated sidecar names onto in-process local GGUF.
        raw = (provider or settings.LLM_PROVIDER).lower()
        if raw in ("ollama", "lm_studio"):
            logger.warning(
                "LLM_PROVIDER=%s is deprecated; using in-process llama-cpp-python",
                raw,
            )
            raw = "local"
        self.provider = raw
        # Explicit-provider instances (fallback services) get no fallback of
        # their own, so a failing fallback cannot recurse.
        self.fallback_provider = None if provider else settings.LLM_FALLBACK_PROVIDER
        self._fallback_service: "LLMService | None" = None
        # analyze_query memo: the iterative retrieval loop analyses the original
        # question and hybrid_search re-analyses each sub-query; identical query
        # strings recur within a session (temperature=0 → deterministic), so a
        # small per-day cache avoids repeated chat-model calls (and, for local
        # GGUFs, the model swap they force).
        self._query_analysis_cache: dict[tuple[str, str], dict] = {}

        logger.info(f"Primary LLM Provider: {self.provider.upper()}")
        if self.fallback_provider:
            logger.info(f"Fallback LLM Provider: {self.fallback_provider.upper()}")

        # Initialize provider-specific clients
        self.init_clients()

        # Initialize ingestion-specific clients (may be separate provider/server)
        self._init_ingestion_clients()

    def init_clients(self):  # pylint: disable=too-many-statements
        """Initialize clients for the configured provider."""
        if self.provider == "local":
            # In-process GGUF via llama-cpp-python (content-machine style; no HTTP server)
            from app.services.local_models import local_llama_runtime

            accel = local_llama_runtime.accel
            logger.info(
                f"Initializing in-process llama-cpp-python "
                f"(backend={accel.get('backend')}, model={settings.LLM_MODEL})"
            )
            sync, async_client, extraction = local_llama_runtime.make_chat_clients()
            self.chat_client = sync
            self.async_chat_client = async_client
            self.extraction_client = extraction

        elif self.provider == "openai":
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set in configuration")
            logger.info(f"Initializing OpenAI (Model: {settings.OPENAI_MODEL})")

            self.extraction_client = instructor.patch(
                OpenAI(api_key=settings.OPENAI_API_KEY, timeout=300.0)
            )
            self.chat_client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=300.0)
            # Async client for batch processing
            self.async_chat_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY, timeout=300.0
            )

        elif self.provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY not set in configuration")
            logger.info(f"Initializing Gemini (Model: {settings.GEMINI_MODEL})")

            # Use native Google Gen AI SDK for better rate limits
            # Timeout: 120 seconds per call — long enough for complex extractions, short
            # enough to fail fast rather than appear frozen when the API hangs.
            self.gemini_client = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options=types.HttpOptions(timeout=120000),
            )

            # OpenAI-compatible wrapper so callers that use chat_client still work
            class GeminiChatWrapper:  # pylint: disable=too-few-public-methods
                """OpenAI-compatible wrapper that routes completion requests through the native Gemini SDK."""

                def __init__(self, native_client):
                    self.native_client = native_client
                    self.chat = self

                class Completions:  # pylint: disable=too-few-public-methods
                    """Inner completions namespace mirroring the OpenAI Completions interface."""

                    def __init__(self, native_client):
                        self.native_client = native_client

                    def create(
                        self,
                        model,
                        messages,
                        _max_tokens=None,
                        _extra_body=None,
                        temperature=0.1,
                    ):
                        """Execute a synchronous completion request against the Gemini API."""
                        # Convert OpenAI-style messages to Gemini format
                        # Combine system + user messages into single prompt
                        prompt_parts = []
                        for msg in messages:
                            if msg["role"] == "system":
                                prompt_parts.append(msg["content"])
                            elif msg["role"] == "user":
                                prompt_parts.append(msg["content"])

                        prompt = "\n\n".join(prompt_parts)

                        # Call native Gemini SDK
                        response = self.native_client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                temperature=temperature,
                                thinking_config=types.ThinkingConfig(
                                    thinking_budget=0,  # thinking_level="MINIMAL"
                                ),
                            ),
                        )

                        # Return OpenAI-compatible response structure
                        class Choice:  # pylint: disable=too-few-public-methods
                            """OpenAI-compatible Choice wrapper holding a single candidate message."""

                            def __init__(self, text):
                                self.message = type("Message", (), {"content": text})()

                        class Response:  # pylint: disable=too-few-public-methods
                            """OpenAI-compatible response wrapper containing a list of choices."""

                            def __init__(self, text):
                                self.choices = [Choice(text)]

                        return Response(response.text)

                @property
                def completions(self):
                    """Return the inner Completions object for OpenAI-style access."""
                    return self.Completions(self.native_client)

            self.chat_client = GeminiChatWrapper(self.gemini_client)

        elif self.provider == "anthropic":
            if not settings.ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY not set in configuration")
            logger.info(f"Initializing Anthropic (Model: {settings.ANTHROPIC_MODEL})")

            # Anthropic uses instructor for structured outputs (prompt engineering mode)
            from anthropic import Anthropic

            self.anthropic_client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

            # Create OpenAI-compatible wrapper for backward compatibility
            # Note: Anthropic doesn't have native structured outputs, so we use instructor
            self.extraction_client = instructor.from_anthropic(
                self.anthropic_client,
                mode=instructor.Mode.ANTHROPIC_JSON,
            )
            self.chat_client = self.anthropic_client

        elif self.provider == "huggingface":
            if not settings.HUGGINGFACE_API_KEY:
                raise ValueError("HUGGINGFACE_API_KEY not set in configuration")
            if not settings.HUGGINGFACE_MODEL:
                raise ValueError("HUGGINGFACE_MODEL not set in configuration")
            base_url = "https://router.huggingface.co/v1"
            logger.info(
                f"Initializing HuggingFace Inference API "
                f"(Model: {settings.HUGGINGFACE_MODEL})"
            )
            self.chat_client = OpenAI(
                base_url=base_url,
                api_key=settings.HUGGINGFACE_API_KEY,
                timeout=300.0,
                max_retries=3,
            )
            self.async_chat_client = AsyncOpenAI(
                base_url=base_url,
                api_key=settings.HUGGINGFACE_API_KEY,
                timeout=300.0,
                max_retries=3,
            )
            # instructor in MD_JSON mode for structured extraction
            self.extraction_client = instructor.patch(
                OpenAI(
                    base_url=base_url,
                    api_key=settings.HUGGINGFACE_API_KEY,
                    timeout=300.0,
                    max_retries=3,
                ),
                mode=instructor.Mode.MD_JSON,
            )

        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def _init_ingestion_clients(self):  # pylint: disable=too-many-statements
        """
        Set up a separate set of clients for ingestion (extraction/entity reasoning).

        If INGESTION_PROVIDER is not set, the ingestion clients simply alias the
        main chat clients so there is zero overhead.
        """
        raw_provider = (
            getattr(self, "_ingestion_provider_override", None)
            or settings.INGESTION_PROVIDER
            or ""
        ).strip().lower()
        if raw_provider in ("ollama", "lm_studio"):
            logger.warning(
                "INGESTION_PROVIDER=%s is deprecated; using in-process local",
                raw_provider,
            )
            raw_provider = "local"
        self.ingestion_provider = raw_provider or self.provider

        # Alias main clients when ingestion uses the same provider.
        same_provider = self.ingestion_provider == self.provider

        if same_provider:
            # Alias main clients — no extra connections needed
            self.i_chat_client = getattr(self, "chat_client", None)
            self.i_async_chat_client = getattr(self, "async_chat_client", None)
            self.i_extraction_client = getattr(self, "extraction_client", None)
            self.i_gemini_client = getattr(self, "gemini_client", None)
            self.i_anthropic_client = getattr(self, "anthropic_client", None)
            logger.info(
                f"Ingestion LLM: shared with main ({self.ingestion_provider.upper()})"
            )
            return

        logger.info(
            f"Ingestion LLM: separate provider {self.ingestion_provider.upper()}"
        )

        if self.ingestion_provider == "local":
            from app.services.local_models import local_llama_runtime

            sync, async_client, extraction = local_llama_runtime.make_chat_clients()
            self.i_chat_client = sync
            self.i_async_chat_client = async_client
            self.i_extraction_client = extraction
            self.i_gemini_client = None
            self.i_anthropic_client = None

        elif self.ingestion_provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise ValueError(
                    "GEMINI_API_KEY required for INGESTION_PROVIDER=gemini"
                )
            self.i_gemini_client = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options=types.HttpOptions(timeout=120000),
            )
            self.i_chat_client = None
            self.i_async_chat_client = None
            self.i_extraction_client = None
            self.i_anthropic_client = None

        elif self.ingestion_provider == "openai":
            if not settings.OPENAI_API_KEY:
                raise ValueError(
                    "OPENAI_API_KEY required for INGESTION_PROVIDER=openai"
                )
            self.i_chat_client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=300.0)
            self.i_async_chat_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY, timeout=300.0
            )
            self.i_extraction_client = instructor.patch(
                OpenAI(api_key=settings.OPENAI_API_KEY, timeout=300.0)
            )
            self.i_gemini_client = None
            self.i_anthropic_client = None

        elif self.ingestion_provider == "anthropic":
            if not settings.ANTHROPIC_API_KEY:
                raise ValueError(
                    "ANTHROPIC_API_KEY required for INGESTION_PROVIDER=anthropic"
                )
            from anthropic import Anthropic

            self.i_anthropic_client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            self.i_extraction_client = instructor.from_anthropic(
                self.i_anthropic_client, mode=instructor.Mode.ANTHROPIC_JSON
            )
            self.i_chat_client = None
            self.i_async_chat_client = None
            self.i_gemini_client = None

        elif self.ingestion_provider == "huggingface":
            if not settings.HUGGINGFACE_API_KEY:
                raise ValueError(
                    "HUGGINGFACE_API_KEY required for INGESTION_PROVIDER=huggingface"
                )
            base_url = "https://router.huggingface.co/v1"
            self.i_chat_client = OpenAI(
                base_url=base_url,
                api_key=settings.HUGGINGFACE_API_KEY,
                timeout=300.0,
                max_retries=3,
            )
            self.i_async_chat_client = AsyncOpenAI(
                base_url=base_url,
                api_key=settings.HUGGINGFACE_API_KEY,
                timeout=300.0,
                max_retries=3,
            )
            self.i_extraction_client = instructor.patch(
                OpenAI(
                    base_url=base_url,
                    api_key=settings.HUGGINGFACE_API_KEY,
                    timeout=300.0,
                    max_retries=3,
                ),
                mode=instructor.Mode.MD_JSON,
            )
            self.i_gemini_client = None
            self.i_anthropic_client = None

        else:
            raise ValueError(
                f"Unsupported INGESTION_PROVIDER: {self.ingestion_provider}"
            )

    def _with_keep_alive(self, extra_body: dict | None = None) -> dict:
        """Pass-through body for chat completions (in-process local ignores keep_alive)."""
        return dict(extra_body or {})

    def _with_ingestion_keep_alive(self, extra_body: dict | None = None) -> dict:
        """Pass-through body for ingestion chat completions."""
        return dict(extra_body or {})

    def _local_json_response_format(
        self, _schema: dict | None = None, _schema_name: str = "response"
    ) -> dict:
        """Force json_object mode for structured local extraction."""
        return {"type": "json_object"}

    def _local_text_response_format(self) -> dict:
        """Fallback when the backend rejects json_object."""
        return {"type": "text"}

    def _local_response_format_candidates(  # pylint: disable=unused-argument
        self, schema: dict | None = None, schema_name: str = "response"
    ) -> list[dict]:
        """Prefer json_object; fall back to text for compatibility."""
        mode = settings.LLM_RESPONSE_FORMAT.lower().strip()
        json_object = {"type": "json_object"}
        text = {"type": "text"}
        if mode == "text":
            return [text]
        return [json_object, text]

    def _clean_json(self, json_str: str) -> str:
        """
        Uses json_repair to robustly fix malformed JSON from LLMs.
        Also strips markdown code blocks, sanitizes control characters,
        and normalizes smart/curly quotes to straight quotes.
        """
        # 1. Unwrap markdown (Common failure mode)
        if "```" in json_str:
            match = re.search(r"```(?:json)?(.*?)```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1)

        # 2. Remove control characters (except allowed ones: \n \r \t inside strings are handled by json_repair)
        # This handles \u0000-\u001F that break JSON parsing
        json_str = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", json_str)

        # 3. Normalize smart/curly quotes to straight quotes
        # Single quotes: ' ' ‛ → '
        json_str = re.sub(r"[\u2018\u2019\u201B]", "'", json_str)
        # Double quotes: " " „ → "
        json_str = re.sub(r"[\u201C\u201D\u201E]", '"', json_str)

        try:
            from json_repair import repair_json

            return repair_json(json_str)
        except ImportError:
            logger.warning("json_repair not installed! Falling back to raw string.")
            return json_str

    def extract_structured(  # pylint: disable=too-many-return-statements
        self,
        prompt: str,
        response_model: Type[BaseModel],
        temperature: float = 0.1,
        model: str | None = None,
    ) -> Optional[BaseModel]:
        """
        Provider-agnostic structured extraction with native schema enforcement.
        Supports: local (in-process GGUF), OpenAI, Gemini, Anthropic (with fallback).

        Args:
            model: Optional model override. When set, uses this model instead of
                   the default LLM_MODEL (e.g. pass settings.INGESTION_LLM_MODEL
                   from ingestion callers to use the lighter extraction model).
        """
        try:
            if self.provider == "local":
                return self._extract_local(
                    prompt, response_model, temperature, model=model
                )
            if self.provider == "openai":
                return self._extract_openai(prompt, response_model, temperature)
            if self.provider == "gemini":
                return self._extract_gemini(
                    prompt, response_model, temperature, model=model
                )
            if self.provider == "anthropic":
                return self._extract_anthropic(prompt, response_model, temperature)
            if self.provider == "huggingface":
                return self._extract_huggingface(prompt, response_model, temperature)
            raise ValueError(f"Unsupported provider: {self.provider}")

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(f"Extraction failed with {self.provider}: {e}")

            # Try fallback provider if configured. Uses a dedicated service
            # instance — mutating the shared singleton's provider/clients would
            # race with concurrent chat/ingestion calls.
            if self.fallback_provider:
                logger.info(f"Attempting fallback to {self.fallback_provider}")
                try:
                    if self._fallback_service is None:
                        self._fallback_service = LLMService(
                            provider=self.fallback_provider
                        )
                    return self._fallback_service.extract_structured(
                        prompt, response_model, temperature
                    )
                except (
                    Exception
                ) as fallback_error:  # pylint: disable=broad-exception-caught
                    logger.error(f"Fallback extraction failed: {fallback_error}")

            # Fail closed — empty Extraction would silently corrupt the graph.
            return None

    def _extract_openai(
        self, prompt: str, response_model: Type[BaseModel], temperature: float
    ) -> BaseModel:
        """OpenAI extraction with native structured outputs."""
        model = settings.OPENAI_MODEL
        logger.info(f"[OpenAI] Extracting with {model} (structured outputs)")

        # OpenAI's beta structured outputs API
        response = self.chat_client.beta.chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_model,
            temperature=temperature,
        )

        return response.choices[0].message.parsed  # Already validated!

    def _extract_local(
        self,
        prompt: str,
        response_model: Type[BaseModel],
        temperature: float,
        model: str | None = None,
        _client=None,
    ) -> BaseModel:
        """In-process / OpenAI-compat local extraction via prompt-guided JSON."""
        import json

        model = model or self.get_chat_model()
        logger.info("[Local] Extracting with %s (JSON mode)", model)

        # Keep schema compact to reduce prompt-processing overhead.
        schema_json = json.dumps(
            response_model.model_json_schema(), separators=(",", ":")
        )
        system_prompt = (
            "You are a structured extraction engine. "
            "Return ONLY valid JSON with no markdown fences and no extra text. "
            "The output MUST match this JSON schema exactly:\n"
            f"{schema_json}"
        )

        raw_content = self._extract_local_with_fallback(
            model=model,
            system_prompt=system_prompt,
            prompt=prompt,
            temperature=temperature,
            schema=None,
            schema_name=response_model.__name__,
            _client=_client,
        )
        cleaned_json = self._clean_json(raw_content)
        try:
            return response_model.model_validate_json(cleaned_json)
        except Exception:
            # Some local models wrap result in {"extraction": {...}}.
            data = json.loads(cleaned_json)
            if isinstance(data, dict) and isinstance(data.get("extraction"), dict):
                return response_model.model_validate(data["extraction"])
            raise

    def _extract_local_with_fallback(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        model: str,
        system_prompt: str,
        prompt: str,
        temperature: float,
        schema: dict | None = None,
        schema_name: str = "response",
        _client=None,
    ) -> str:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """
        Try configured response_format strategy with compatibility fallbacks.
        ``_client`` allows routing to an ingestion-specific server.
        """
        client = _client or self.chat_client
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        last_error = None
        for response_format in self._local_response_format_candidates(
            schema=schema, schema_name=schema_name
        ):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    extra_body=self._with_keep_alive(),
                    temperature=temperature,
                )
                return response.choices[0].message.content
            except Exception as e:  # pylint: disable=broad-exception-caught
                last_error = e
                logger.warning(
                    f"[Local] response_format={response_format.get('type')} failed: {e}"
                )

        if last_error:
            raise last_error
        raise RuntimeError(
            "[Local] Extraction failed with no response formats to try"
        )

    def _extract_huggingface(
        self, prompt: str, response_model: Type[BaseModel], temperature: float
    ) -> BaseModel:
        """HuggingFace Inference API extraction via OpenAI-compatible endpoint.

        Uses prompt-guided JSON mode — json_object is attempted first and falls
        back to plain text so the method works across all HF-hosted models.
        """
        import json

        model = settings.HUGGINGFACE_MODEL
        logger.info(f"[HuggingFace] Extracting with {model}")

        schema_json = json.dumps(
            response_model.model_json_schema(), separators=(",", ":")
        )
        system_prompt = (
            "You are a structured extraction engine. "
            "Return ONLY valid JSON with no markdown fences and no extra text. "
            "The output MUST match this JSON schema exactly:\n"
            f"{schema_json}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Try json_object first; fall back to text if the model rejects it.
        last_error = None
        for response_format in [{"type": "json_object"}, {"type": "text"}]:
            try:
                response = self.chat_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    temperature=temperature,
                )
                raw = response.choices[0].message.content
                cleaned = self._clean_json(raw)
                return response_model.model_validate_json(cleaned)
            except Exception as e:  # pylint: disable=broad-exception-caught
                last_error = e
                logger.warning(
                    f"[HuggingFace] format={response_format['type']} failed: {e}"
                )

        raise last_error

    @staticmethod
    def _inline_schema_refs(schema: dict) -> dict:
        """Inline all $ref references in a JSON schema so Gemini can parse it.

        Gemini's response_schema does not support $ref / $defs — any schema
        produced by Pydantic for nested models must be fully flattened first.
        """
        import copy

        schema = copy.deepcopy(schema)
        defs = schema.pop("$defs", {})

        def resolve(obj):
            if not isinstance(obj, dict):
                return obj
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                resolved = copy.deepcopy(defs.get(ref_name, obj))
                return resolve(resolved)
            return {k: resolve(v) for k, v in obj.items()}

        return resolve(schema)

    def _extract_gemini(  # pylint: disable=too-many-locals
        self,
        prompt: str,
        response_model: Type[BaseModel],
        temperature: float,
        model: str | None = None,
        _gemini_client=None,
    ) -> BaseModel:
        """Gemini extraction with native SDK and JSON schema enforcement."""
        import time

        gemini_client = _gemini_client or self.gemini_client
        model = model or settings.GEMINI_MODEL
        logger.info(f"[Gemini] Extracting with {model} (native SDK)")

        _retryable = (
            "504",
            "DEADLINE_EXCEEDED",
            "503",
            "UNAVAILABLE",
            "429",
            "RESOURCE_EXHAUSTED",
        )
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                inlined_schema = self._inline_schema_refs(
                    response_model.model_json_schema()
                )
                response = gemini_client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_schema=inlined_schema,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )

                import json

                response_data = json.loads(response.text)
                # Use model_validate (not **kwargs) so model_validator(mode='before') fires
                return response_model.model_validate(response_data)

            except Exception as e:
                err = str(e)
                if "PROHIBITED_CONTENT" in err or "content_filter" in err.lower():
                    # Fail closed: an empty-but-valid Extraction upstream would
                    # silently erase entities for this chunk in the graph.
                    logger.warning("[Gemini] Content filtered; failing extraction.")
                    raise ValueError("Gemini content filter blocked extraction") from e

                is_retryable = any(code in err for code in _retryable)
                if is_retryable and attempt < max_retries:
                    wait = 2**attempt  # 2s, 4s
                    logger.warning(
                        f"[Gemini] Retryable error (attempt {attempt}/{max_retries}), "
                        f"retrying in {wait}s: {err}"
                    )
                    time.sleep(wait)
                    continue

                logger.error(f"[Gemini] Extraction error: {e}")
                raise

    def _extract_anthropic(
        self, prompt: str, response_model: Type[BaseModel], temperature: float
    ) -> BaseModel:
        """Anthropic extraction with prompt engineering + validation."""
        model = settings.ANTHROPIC_MODEL
        logger.info(f"[Anthropic] Extracting with {model} (prompt-based)")

        # Anthropic doesn't have native schema enforcement, use instructor
        response = self.extraction_client.messages.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
            response_model=response_model,
        )

        return response

    def _reason_step(self, prompt: str) -> tuple[str, str | None]:
        """
        Like reason(), but also returns any model thinking/reasoning content.

        Returns:
            (content, thinking) where thinking is the model's internal chain-of-thought
            (from reasoning_content field or <think>…</think> tags), stripped from content.
            thinking is None if the model produced no separate thinking.
        """
        thinking: str | None = None

        if self.provider == "gemini":
            response = self.gemini_client.models.generate_content(
                model=self.get_chat_model(),
                contents=prompt,
            )
            return response.text or "", None

        if self.provider == "anthropic":
            response = self.chat_client.messages.create(
                model=self.get_chat_model(),
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text or "", None

        # Local / LM Studio / OpenAI
        _model = self.get_chat_model()
        extra_body = self._with_keep_alive()

        response = self.chat_client.chat.completions.create(
            model=_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a deep reasoning engine. Analyze the input carefully. Detect conflicts, subtleties, or hidden connections.",  # pylint: disable=line-too-long
                },
                {"role": "user", "content": prompt},
            ],
            extra_body=extra_body,
        )
        message = response.choices[0].message
        content: str = message.content or ""

        # LM Studio (and some OpenAI-compat servers) expose thinking in reasoning_content
        thinking = getattr(message, "reasoning_content", None) or None

        # Fallback: extract <think>…</think> blocks embedded in content
        if not thinking and "<think>" in content:
            think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
            if think_match:
                thinking = think_match.group(1).strip()
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()

        return content, thinking

    def reason(self, prompt: str, model: str | None = None) -> str:
        """
        Uses the Reasoning Model for complex logic/refinement.
        Returns raw text (Chain-of-Thought + Answer).
        """
        # Select model based on provider
        if self.provider == "gemini":
            response = self.gemini_client.models.generate_content(
                model=model or self.get_chat_model(),
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
                contents=f"{prompt}",
            )
            return response.text
        if self.provider == "anthropic":
            response = self.chat_client.messages.create(
                model=model or self.get_chat_model(),
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        # Ollama/LM Studio/OpenAI
        _model = model or self.get_chat_model()
        extra_body = self._with_keep_alive()

        response = self.chat_client.chat.completions.create(
            model=_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a deep reasoning engine. Analyze the input carefully. Detect conflicts, subtleties, or hidden connections.",  # pylint: disable=line-too-long
                },
                {"role": "user", "content": prompt},
            ],
            extra_body=extra_body,
        )
        return response.choices[0].message.content

    def rewrite_follow_up_query(
        self,
        history: list[dict],
        latest_query: str,
        model: str | None = None,
    ) -> str:
        """Rewrite a follow-up into a standalone retrieval query using recent chat turns."""
        latest = (latest_query or "").strip()
        if not latest or not history:
            return latest

        lines: list[str] = []
        for turn in history[-settings.CHAT_HISTORY_MAX_MESSAGES :]:
            role = (turn.get("role") or "").strip().lower()
            content = (turn.get("content") or "").strip()
            if not content or role not in {"user", "assistant"}:
                continue
            label = "User" if role == "user" else "Assistant"
            if len(content) > 600:
                content = content[:597].rstrip() + "..."
            lines.append(f"{label}: {content}")

        if not lines:
            return latest

        prompt = (
            "You rewrite follow-up questions into standalone search queries for a "
            "personal knowledge base.\n\n"
            "CONVERSATION:\n"
            + "\n".join(lines)
            + "\n\n"
            f"LATEST USER MESSAGE: {latest}\n\n"
            "Return ONE standalone search query that captures what the user is asking "
            "now, resolving pronouns and references from the conversation.\n"
            "Do not answer the question. Do not add explanation.\n"
            "Reply with only the rewritten query."
        )

        try:
            raw = self._reason_step_sync(prompt, model=model)
            rewritten = (raw or "").strip().strip('"').strip("'").strip()
            if rewritten and len(rewritten) <= 300:
                logger.info(
                    f"[LLM] rewrite_follow_up_query: {latest!r} -> {rewritten!r}"
                )
                return rewritten
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[LLM] rewrite_follow_up_query failed: {exc}")
        return latest

    def _reason_step_sync(self, prompt: str, model: str | None = None) -> str:
        """Synchronous lightweight reasoning call for query rewrite."""
        _model = model or self.get_chat_model()
        extra_body = self._with_keep_alive()
        response = self.chat_client.chat.completions.create(
            model=_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise query rewriter. Output only the rewritten query."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            extra_body=extra_body,
        )
        return (response.choices[0].message.content or "").strip()

    def generate_title(self, text: str, model: str | None = None) -> str:
        """
        Generates a concise 3-5 word title for a note.
        """
        if not text or not text.strip():
            return "Untitled Note"

        # Select model based on provider
        if self.provider == "gemini":
            response = self.gemini_client.models.generate_content(
                model=model or self.get_chat_model(),
                contents=f"Generate a concise, descriptive title for this note. Do not use quotes.\n\nNote content:\n{text}\n\nTitle:",  # pylint: disable=line-too-long
            )
            return response.text.strip().replace('"', "")
        if self.provider == "anthropic":
            response = self.chat_client.messages.create(
                model=model or self.get_chat_model(),
                messages=[
                    {
                        "role": "user",
                        "content": f"Generate a concise, descriptive title for this note. Do not use quotes.\n\nNote content:\n{text}\n\nTitle:",  # pylint: disable=line-too-long
                    }
                ],
            )
            return response.content[0].text.strip().replace('"', "")
        # Ollama/LM Studio/OpenAI
        _model = model or self.get_chat_model()
        extra_body = self._with_keep_alive()

        response = self.chat_client.chat.completions.create(
            model=_model,
            messages=[
                {
                    "role": "system",
                    "content": "Generate a concise, descriptive title for the provided note content. Do not use quotes.",  # pylint: disable=line-too-long
                },
                {"role": "user", "content": f"Note content:\n{text}\n\nTitle:"},
            ],
            extra_body=extra_body,
        )
        return response.choices[0].message.content.strip().replace('"', "")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        """Synchronously generate a plain-text response from the LLM.

        Used for tasks that need a free-form text response (e.g. temporal digests)
        rather than the structured extraction used during ingestion.
        """
        if self.provider == "gemini":
            response = self.gemini_client.models.generate_content(
                model=model or self.get_chat_model(),
                contents=f"{system_prompt}\n\n{user_prompt}",
            )
            return response.text.strip()
        if self.provider == "anthropic":
            response = self.chat_client.messages.create(
                model=model or self.get_chat_model(),
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}
                ],
            )
            return response.content[0].text.strip()
        # OpenAI / local (LM Studio, Ollama, vLLM, etc.)
        _model = model or self.get_chat_model()
        response = self.chat_client.chat.completions.create(
            model=_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            extra_body=self._with_keep_alive(),
        )
        return response.choices[0].message.content.strip()

    def analyze_query(self, query: str) -> dict:
        """
        Analyzes user query with structured outputs for better retrieval.
        Uses the same extraction approach as ingestion for consistency.
        """
        from typing import Literal

        from pydantic import Field

        class QueryAnalysis(BaseModel):
            """Structured output schema for query-analysis: sub-questions and search hints."""

            intent: Literal[
                "search", "summarize", "compare", "explain", "list", "recent", "verify"
            ] = Field(description="Primary intent of the query")
            entities: list[str] = Field(
                default_factory=list,
                description="Named entities mentioned in the query",
            )
            concepts: list[str] = Field(
                default_factory=list,
                description="Abstract concepts or topics mentioned",
            )
            keywords: list[str] = Field(
                default_factory=list,
                description="Important keywords for semantic search",
            )
            expected_entity_types: list[str] = Field(
                default_factory=list,
                description="Types of entities the answer should be about: Person, Film, Place, Organization, etc.",
            )
            question_attribute: Optional[str] = Field(
                default=None,
                description="What attribute is being asked about: nationality, occupation, birth_date, location, director, capacity, etc.",  # pylint: disable=line-too-long
            )
            date_filter: Optional[str] = Field(
                default=None,
                description="ISO date (YYYY-MM-DD) if the query asks about a specific calendar day, e.g. '2024-05-24'. Null if the query spans a whole month or is not temporal.",  # pylint: disable=line-too-long
            )
            period_filter: Optional[str] = Field(
                default=None,
                description="ISO year-month (YYYY-MM) if the query asks about a whole month or multi-day period, e.g. 'last month', 'in April'. Mutually exclusive with date_filter. Null if date_filter is set or query is not temporal.",  # pylint: disable=line-too-long
            )

        # Use the same prompt style as ingestion - concrete JSON examples
        from datetime import date as _date

        _today = _date.today().isoformat()  # e.g. "2026-05-25"

        # Date is part of the key because relative-date resolution ("yesterday")
        # depends on today. Only successful analyses are cached.
        _cache_key = (query, _today)
        _cached = self._query_analysis_cache.get(_cache_key)
        if _cached is not None:
            logger.info("Query analysis cache hit — skipping LLM call")
            return dict(_cached)

        try:
            prompt = f"""Analyze the following search query and return a structured JSON object.

            Today's date: {_today}

            QUERY: "{query}"

            Return a JSON object with these fields:

            - "entities": Complete named entities exactly as written — never split multi-word names.
            e.g. "Albert Einstein", "The Great Gatsby", "New York City", "Yale University"

            - "entity_types": The types of entities the answer will involve.
            e.g. ["Person"], ["Film", "Person"], ["Place"], ["Organization", "Person"], ["Venue"]

            - "question_attribute": The specific attribute being asked about.
            e.g. "nationality", "occupation", "director", "location", "capacity", "birth_date", "award"

            - "intent": One of — search / compare / summarize / explain / list

            - "keywords": Important terms to use when searching, excluding named entities already captured above.

            - "date_filter": If the query asks about a specific single calendar day (e.g. "yesterday", "May 24th", "last Tuesday"), return that resolved date as YYYY-MM-DD. Null otherwise.

            - "period_filter": If the query asks about a full month or multi-day period (e.g. "last month", "in April", "this month"), return the resolved year-month as YYYY-MM. Mutually exclusive with date_filter — set at most one. Null otherwise.

            EXAMPLES:

            Query: "Were Albert Einstein and Marie Curie of the same nationality?"
            {{"entities": ["Albert Einstein", "Marie Curie"], "expected_entity_types": ["Person"], "question_attribute": "nationality", "intent": "compare", "keywords": ["nationality"], "date_filter": null, "period_filter": null}}

            Query: "What award did the author of 1984 win?"
            {{"entities": ["1984"], "expected_entity_types": ["Book", "Person"], "question_attribute": "award", "intent": "search", "keywords": ["author", "award"], "date_filter": null, "period_filter": null}}

            Query: "How many seats does Madison Square Garden have?"
            {{"entities": ["Madison Square Garden"], "expected_entity_types": ["Venue"], "question_attribute": "capacity", "intent": "search", "keywords": ["seats", "capacity"], "date_filter": null, "period_filter": null}}

            Query: "Who directed Inception?"
            {{"entities": ["Inception"], "expected_entity_types": ["Film", "Person"], "question_attribute": "director", "intent": "search", "keywords": ["directed"], "date_filter": null, "period_filter": null}}

            Query: "What happened on the 24th of May 2024?"
            {{"entities": [], "expected_entity_types": [], "question_attribute": null, "intent": "search", "keywords": ["happened", "events"], "date_filter": "2024-05-24", "period_filter": null}}

            Query: "What did I write on March 3rd 2023?"
            {{"entities": [], "expected_entity_types": [], "question_attribute": null, "intent": "search", "keywords": ["wrote", "notes"], "date_filter": "2023-03-03", "period_filter": null}}

            Query: "What happened last month?"
            {{"entities": [], "expected_entity_types": [], "question_attribute": null, "intent": "summarize", "keywords": ["happened", "events"], "date_filter": null, "period_filter": "2026-04"}}

            Query: "What did I do in April?"
            {{"entities": [], "expected_entity_types": [], "question_attribute": null, "intent": "summarize", "keywords": ["did", "activities"], "date_filter": null, "period_filter": "2026-04"}}

            Return only the JSON object, no preamble or explanation.
            """

            # Use extract_structured - same as ingestion
            result = self.extract_structured(prompt, QueryAnalysis, temperature=0)
            if result:
                analysis = result.model_dump()
                if len(self._query_analysis_cache) >= 64:
                    self._query_analysis_cache.pop(
                        next(iter(self._query_analysis_cache))
                    )
                self._query_analysis_cache[_cache_key] = dict(analysis)
                return analysis
            raise ValueError("Empty extraction result")

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(f"Query analysis failed: {e}")
            # Return safe defaults
            return {
                "intent": "search",
                "entities": [],
                "concepts": [],
                "keywords": query.split(),
                "expected_entity_types": [],
                "question_attribute": None,
                "date_filter": None,
                "period_filter": None,
            }

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> str:
        """
        Generic text generation - provider-agnostic.

        Use this for simple text generation tasks (alias comparison, classification, etc).

        Args:
            prompt: The prompt to send to the LLM
            temperature: Sampling temperature (0=deterministic, 1=creative)
            max_tokens: Max tokens in response

        Returns:
            Generated text response
        """
        try:
            if self.provider == "gemini":
                _gemini_model = model or self.get_chat_model()
                _gemini_cfg = types.GenerateContentConfig(
                    temperature=temperature,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,  # thinking_level="MINIMAL"
                    ),
                )
                response = await asyncio.to_thread(
                    self.gemini_client.models.generate_content,
                    model=_gemini_model,
                    contents=prompt,
                    config=_gemini_cfg,
                )
                return response.text.strip()

            if self.provider == "anthropic":
                response = await asyncio.to_thread(
                    self.chat_client.messages.create,
                    model=model or self.get_chat_model(),
                    max_tokens=max_tokens if max_tokens is not None else 10240,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text.strip()

            # OpenAI-compatible local or cloud providers
            model = model or self.get_chat_model()
            extra_body = self._with_keep_alive()

            _kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "extra_body": extra_body,
            }
            # No default cap: local sizes output from the context left, cloud
            # providers use their own model maximum.
            if max_tokens is not None:
                _kwargs["max_tokens"] = max_tokens
            response = await asyncio.to_thread(
                self.chat_client.chat.completions.create, **_kwargs
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"[LLM] generate() failed: {e}")
            raise

    async def iterative_step(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
        self,
        original_question: str,
        accumulated_steps: list[dict],
        search_query: str | None,
        docs: list[dict],
        tried_queries: list[str] | None = None,
        conversation_context: str | None = None,
    ) -> dict:  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """
        Single iteration of the iterative retrieval loop.

        Args:
            original_question: The original user question.
            accumulated_steps: Steps taken so far:
                [{query, full_answer, reasoning}, ...]
            search_query: The query used to retrieve ``docs``, or None on the
                first call (before any retrieval).
            docs: Documents retrieved for ``search_query``, or [] on the first
                call.
            tried_queries: All queries already attempted (prevents loop from
                repeating the same search).

        Returns:
            {
                "reasoning":    str,           # reasoning over current docs
                "full_answer":  str,           # contextual answer from docs
                "can_answer":   bool,          # True if question can be answered
                "final_answer": str | None,    # bare answer phrase (if can_answer)
                "next_query":   str | None,    # next search query (if not can_answer)
            }
        """
        _non_answers = {"INSUFFICIENT", "NONE", "N/A", "UNKNOWN", "NOT FOUND"}

        # ── Build prior findings block ────────────────────────────────────────
        prior_block = ""
        if accumulated_steps:
            lines = []
            for i, step in enumerate(accumulated_steps, 1):
                q = step.get("query", "")
                r = step.get("reasoning", "")
                fa = step.get("full_answer", "")
                entry = f"Step {i}: Searched for '{q}'"
                if r:
                    entry += f"\n  Reasoning: {r}"
                entry += f"\n  Finding: {fa or 'Not found'}"
                lines.append(entry)
            prior_block = "PRIOR FINDINGS:\n" + "\n\n".join(lines) + "\n\n"

        # ── Already-tried queries block (prevents repetitive cycling) ─────────
        tried_block = ""
        if tried_queries:
            tried_block = (
                "QUERIES ALREADY TRIED (do NOT repeat these — choose a different angle):\n"
                + "\n".join(f"  - {q}" for q in tried_queries)
                + "\n\n"
            )

        # ── Build current search block ────────────────────────────────────────
        current_block = ""
        if search_query and docs:
            context_lines = []
            for doc in docs:
                text = (doc.get("text") or "").strip()
                if text:
                    context_lines.append(text)
            context = (
                "\n\n---\n\n".join(context_lines)
                if context_lines
                else "(no text found)"
            )
            current_block = (
                f"CURRENT SEARCH: '{search_query}'\n\n"
                f"RETRIEVED DOCUMENTS:\n{context}\n\n"
            )

        # ── Build task instructions ───────────────────────────────────────────
        if search_query and docs:
            if settings.BENCHMARK_MODE:
                task_instructions = (
                    "Assess the current results:\n"
                    "REASONING: <how these documents relate to the question "
                    "and prior findings>\n"
                    "FINDING: <the specific fact(s) extracted from these documents, "
                    "e.g. 'Scott Derrickson is American' or 'Ed Wood was born in 1924'. "
                    "This is NOT the final answer to the original question — just what "
                    "this search found. Always write something; use 'Not found' only "
                    "if nothing in these documents is relevant to the query>\n\n"
                    "Then decide:\n"
                    "  If you have enough information to answer the ORIGINAL QUESTION confidently:\n"
                    "  ANSWER: <the specific answer — see output rules below>\n\n"
                    "  If you need more information:\n"
                    "  NEXT_QUERY: <one specific search query, different from all prior ones>\n\n"
                    f"{self._REASONING_RULES}\n"
                    f"{self._OUTPUT_RULES}"
                )
            else:
                task_instructions = (
                    "Assess the current results:\n"
                    "REASONING: <how these documents relate to the question "
                    "and what you have found so far>\n"
                    "FINDING: <a summary of what is relevant in these documents — "
                    "key facts, entities, relationships. Always write something; "
                    "use 'Not found' only if truly nothing here is relevant>\n\n"
                    "Then decide:\n"
                    "  If you have enough information to answer the ORIGINAL QUESTION:\n"
                    "  ANSWER: <a complete, natural-language answer covering everything "
                    "relevant you found — see output rules below>\n\n"
                    "  If you need more information:\n"
                    "  NEXT_QUERY: <one specific search query, different from all prior ones>\n\n"
                    f"{self._REASONING_RULES_GENERAL}\n"
                    f"{self._OUTPUT_RULES_GENERAL}"
                )
        else:
            task_instructions = (
                "Output the first search query needed to start answering this question:\n"
                "NEXT_QUERY: <one specific search query>\n"
            )

        prompt = (
            "You are a research assistant solving a multi-hop question step by step.\n\n"
            f"{conversation_context or ''}"
            f"ORIGINAL QUESTION: {original_question}\n\n"
            f"{prior_block}"
            f"{tried_block}"
            f"{current_block}"
            f"{task_instructions}"
            "\nReply:"
        )

        try:
            raw, step_thinking = await asyncio.to_thread(self._reason_step, prompt)
            raw = raw or ""
            logger.info(f"[LLM] iterative_step raw response:\n{raw}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(f"[LLM] iterative_step failed: {e}")
            return {
                "reasoning": "",
                "full_answer": "",
                "can_answer": False,
                "final_answer": None,
                "next_query": None,
                "thinking": None,
            }

        # ── Parse response ────────────────────────────────────────────────────
        reasoning = ""
        full_answer = ""
        can_answer = False
        final_answer: str | None = None
        next_query: str | None = None

        # Regex-based section extractor — handles:
        #   * markdown bold: **ANSWER:** or **FINDING:**
        #   * multi-line section content (FINDING:\ntext on next line)
        #   * any mix of the above
        _section_re = re.compile(
            r"\*{0,3}(REASONING|FINDING|FULL_ANSWER|ANSWER|NEXT_QUERY)\*{0,3}\s*:[ \t]*(.*?)"
            r"(?=\*{0,3}(?:REASONING|FINDING|FULL_ANSWER|ANSWER|NEXT_QUERY)\*{0,3}\s*:|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        sections: dict[str, str] = {}
        for m in _section_re.finditer(raw):
            key = m.group(1).upper()
            val = m.group(2).strip()
            if key not in sections:  # first occurrence wins
                sections[key] = val

        def _clean_next_query(value: str) -> str:
            """Keep only the actual search phrase from a NEXT_QUERY section."""
            first_line = next((line.strip() for line in value.splitlines() if line.strip()), "")
            first_line = re.sub(r"^\s*(?:[-*]\s*)+", "", first_line)
            first_line = re.sub(r"^\*{1,3}|\*{1,3}$", "", first_line).strip()
            return first_line.strip().strip('"').strip("'").strip()

        reasoning = sections.get("REASONING", "")
        full_answer = sections.get("FINDING", "") or sections.get("FULL_ANSWER", "")
        answer_val = sections.get("ANSWER", "")
        next_query_val = sections.get("NEXT_QUERY", "")

        if answer_val and answer_val.upper() not in _non_answers:
            can_answer = True
            final_answer = answer_val
        elif next_query_val:
            next_query = _clean_next_query(next_query_val)

        # Some local models occasionally ignore the literal NEXT_QUERY label on
        # the first planning turn and return only a quoted search phrase, e.g.
        # `Reply: "Fido meaning and history"`. Treat that as the next query
        # only before any docs have been retrieved; later turns must use the
        # explicit ANSWER/NEXT_QUERY protocol.
        if not can_answer and not next_query and not docs:
            candidate = raw.strip()
            candidate = re.sub(r"^\s*(?:reply|query)\s*:\s*", "", candidate, flags=re.I)
            candidate = candidate.strip().strip('"').strip("'").strip()
            if (
                candidate
                and "\n" not in candidate
                and len(candidate) <= 200
                and candidate.upper() not in _non_answers
            ):
                logger.info(
                    "[LLM] iterative_step unlabeled NEXT_QUERY fallback: "
                    f"'{candidate}'"
                )
                next_query = candidate

        # ── Post-process ANSWER ───────────────────────────────────────────────
        # Fallback: if the LLM committed FULL_ANSWER but gave neither ANSWER nor
        # NEXT_QUERY, it found the information but forgot to switch to the
        # terminating format.  Treat FULL_ANSWER as the final answer.
        _not_found_vals = {"not found", "none", "insufficient", "n/a", "unknown"}
        if (
            not can_answer
            and not next_query
            and full_answer
            and full_answer.lower().strip() not in _not_found_vals
        ):
            logger.info(
                "[LLM] iterative_step FULL_ANSWER fallback (no ANSWER/NEXT_QUERY): "
                f"'{full_answer}'"
            )
            can_answer = True
            final_answer = full_answer

        return {
            "reasoning": reasoning,
            "full_answer": full_answer,
            "can_answer": can_answer,
            "final_answer": final_answer,
            "next_query": next_query,
            "thinking": step_thinking,
        }

    # Reasoning rules ported from benchmark v4/v5 (the 0.74-scoring pipeline).
    # Enforces correct answer-type discipline, comparison direction, specificity, and past/present.
    _REASONING_RULES = """
        REASONING RULES — apply these before writing your answer:

        CHAIN TRACING
        - For multi-hop questions, trace findings in order. The bridge entity (answer to an 
        intermediate step) is not the final answer — use it to reach what was actually asked.
        - Explicitly name the bridge entity first, then derive the final answer from it.

        COMPARISON & YES/NO
        - For yes/no comparisons: extract the relevant value per entity, compare, then output 
        YES or NO — never the compared value itself.
        - For "which of X or Y is more/older/greater": output the winner's full name, not the 
        metric. Older = earlier birth year.
        - If the question asks whether two entities BOTH share a property: verify each 
        separately. YES only if both are confirmed.
        - "Was X founded by the person who did Y?" → output the name, not YES.
        Only output YES/NO for explicit comparisons or shared-property questions.
        - For yes/no questions: your ANSWER line must be YES or NO — never an intermediate 
        value like a nationality, number, or name. Derive the YES/NO conclusion yourself 
        from the evidence before writing ANSWER.

        ANSWER TYPE
        - Match exactly what the question asks for.
        Common traps: song ≠ person; show ≠ character; position ≠ person holding it; 
        city ≠ building; number ≠ demonym; animal ≠ person named after it.
        - Before writing your answer, verify it matches the type asked for.

        SPECIFICITY & SCOPE
        - Use the most specific value the evidence supports. Do not broaden:
        neighborhood → city, city → country, person → organization.
        - If two entities share a parent region, output the parent. Only list sub-locations 
        when they differ.
        - One answer only. Do not list alternatives or add caveats.

        EXACT EXTRACTION
        - Do NOT add: parent geography, org prefix, or qualifiers not implied by the question.
        - Do NOT strip: first names, suffixes, units, or qualifiers that are part of the answer.
        - For time spans: copy the exact phrase from the source including connectives 
        (from/until/through/between). Do not normalize — if the source says "until", keep "until".
        - For qualified quantities (e.g. "net", "peak", "opening", "seat"): use the figure 
        carrying that exact qualifier, not a broader or unqualified figure.

        TEMPORAL
        - If the question asks for a former or historical value, use the value from that period.
        """

    _OUTPUT_RULES = """
        OUTPUT RULES:
        - Return only the specific fact the question asks for — nothing else
        - Yes/no question → YES or NO
        - Comparison / either-or → exactly one of the options given
        - Name → full name as it appears in the source
        - Number → include units or qualifiers the question implies
        - Date or time range → exact phrase from source, preserving all connectives
        - Role, title, or position → the role/title only, never the person holding it
        - Location → exact place name as it appears in the source
        - Never answer "Neither" or "Both" unless the question explicitly asks for it
        - If you cannot answer yet → output NEXT_QUERY, not ANSWER
        """

    # ── General KB mode rules (BENCHMARK_MODE=False) ──────────────────────────
    # Used for personal knowledge bases where the goal is a thorough,
    # natural-language answer — not a single extracted fact.

    _REASONING_RULES_GENERAL = """
        REASONING RULES:
        - Trace your findings step by step across all prior searches
        - Cover all relevant aspects you have found, not just one fact
        - Note relationships and connections between entities across searches
        - If multiple searches returned related information, synthesise them
        """

    _OUTPUT_RULES_GENERAL = """
        OUTPUT RULES:
        - Write a complete, natural-language answer covering all relevant things you found
        - Be thorough — it is better to include more than to leave something out
        - Organise clearly if there are multiple aspects (e.g. short paragraphs or a list)
        - Stick strictly to what the documents say — do not invent or infer beyond the evidence
        - If something relevant could not be found, acknowledge it briefly
        - Do not pad with filler phrases — every sentence should add information
        - If you cannot answer yet → output NEXT_QUERY, not ANSWER
        """

    def get_chat_model(self) -> str:
        """Return the model to use for chat and generation tasks.

        Per-instance override (per-KB model) wins, then CHAT_MODEL, then the
        provider-specific keys.
        """
        override = getattr(self, "_chat_model_override", None)
        if override:
            return override
        if settings.CHAT_MODEL:
            return settings.CHAT_MODEL
        if self.provider == "local":
            return settings.LLM_MODEL
        provider_model_map = {
            "openai": settings.OPENAI_MODEL,
            "gemini": settings.GEMINI_MODEL,
            "anthropic": settings.ANTHROPIC_MODEL,
            "huggingface": settings.HUGGINGFACE_MODEL,
        }
        return provider_model_map.get(self.provider, settings.LLM_MODEL)

    def get_ingestion_model(self) -> str | None:
        """Return the configured ingestion model for the active ingestion provider.

        Per-instance override wins (ingestion model, else the instance's chat
        model), then INGESTION_MODEL, then the provider-specific keys.
        """
        override = getattr(self, "_ingestion_model_override", None) or getattr(
            self, "_chat_model_override", None
        )
        if override:
            return override
        if settings.INGESTION_MODEL:
            return settings.INGESTION_MODEL
        p = getattr(self, "ingestion_provider", self.provider)
        _local = settings.INGESTION_LLM_MODEL or settings.LLM_MODEL or None
        ingestion_model_map = {
            "local": _local,
            "ollama": _local,  # deprecated alias
            "lm_studio": _local,  # deprecated alias
            "gemini": settings.INGESTION_GEMINI_MODEL or settings.GEMINI_MODEL or None,
            "openai": settings.OPENAI_MODEL or None,
            "anthropic": settings.ANTHROPIC_MODEL or None,
            "huggingface": settings.HUGGINGFACE_MODEL or None,
        }
        return ingestion_model_map.get(p)

    # ── Ingestion-specific generation ─────────────────────────────────────────

    async def ingestion_generate(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str:
        """
        Like ``generate()`` but always routes to the ingestion provider/server.
        Use this for all LLM calls inside the ingestion pipeline.
        """
        content, _meta = await self.ingestion_generate_with_meta(
            prompt, temperature=temperature, max_tokens=max_tokens
        )
        return content

    async def ingestion_generate_with_meta(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> tuple[str, dict]:
        """``ingestion_generate`` plus ``{"finish_reason", "truncated"}``.

        ``truncated`` is True when the provider stopped at its output limit —
        callers that parse JSON must treat that as an incomplete result, not
        as a malformed one to retry blindly.
        """
        model = self.get_ingestion_model()
        try:
            if self.ingestion_provider == "gemini":
                _gemini_model = model or settings.GEMINI_MODEL
                _gemini_cfg = types.GenerateContentConfig(
                    temperature=temperature,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                )
                response = await asyncio.to_thread(
                    self.i_gemini_client.models.generate_content,
                    model=_gemini_model,
                    contents=prompt,
                    config=_gemini_cfg,
                )
                reason = None
                try:
                    reason = str(response.candidates[0].finish_reason or "")
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                return (response.text or "").strip(), {
                    "finish_reason": reason,
                    "truncated": bool(reason and "MAX_TOKENS" in reason.upper()),
                }

            if self.ingestion_provider == "anthropic":
                response = await asyncio.to_thread(
                    self.i_anthropic_client.messages.create,
                    model=settings.ANTHROPIC_MODEL,
                    # Anthropic requires an explicit cap.
                    max_tokens=max_tokens if max_tokens is not None else 16384,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                reason = getattr(response, "stop_reason", None)
                return response.content[0].text.strip(), {
                    "finish_reason": reason,
                    "truncated": reason == "max_tokens",
                }

            # local / openai / huggingface
            _kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "extra_body": self._with_ingestion_keep_alive(),
            }
            if max_tokens is not None:
                _kwargs["max_tokens"] = max_tokens
            response = await asyncio.to_thread(
                self.i_chat_client.chat.completions.create, **_kwargs
            )
            choice = response.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                raise ValueError(
                    "Local LLM returned empty content (0 output tokens). "
                    "Possible causes: context overflow, KV cache pressure, or "
                    "model crash. Check local GGUF / API server logs."
                )
            reason = getattr(choice, "finish_reason", None)
            return content.strip(), {
                "finish_reason": reason,
                "truncated": reason == "length",
            }

        except Exception as e:
            logger.error(f"[LLM] ingestion_generate() failed: {e}")
            raise

    def ingestion_count_tokens(self, text: str) -> int:
        """Token estimate for ``text`` on the ingestion model (no model load)."""
        if getattr(self, "ingestion_provider", self.provider) == "local":
            try:
                from app.services.local_models import local_llama_runtime

                return local_llama_runtime.count_tokens(text)
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        return len(text or "") // 4 + 1

    def ingestion_context_tokens(self) -> int:
        """Context window of the ingestion model, for input chunk sizing."""
        if getattr(self, "ingestion_provider", self.provider) == "local":
            try:
                from app.services.local_models import _default_chat_n_ctx

                return int(_default_chat_n_ctx())
            except Exception:  # pylint: disable=broad-exception-caught
                return 16384
        # Cloud models are far larger; output limits, not context, bind there.
        return 128000

    def ingestion_extract_structured(  # pylint: disable=too-many-return-statements
        self,
        prompt: str,
        response_model: Type[BaseModel],
        temperature: float = 0.1,
    ):
        """
        Like ``extract_structured()`` but always routes to the ingestion provider/server.
        Use this for all structured extraction inside the ingestion pipeline.
        """
        model = self.get_ingestion_model()
        try:
            if self.ingestion_provider == "local":
                return self._extract_local(
                    prompt,
                    response_model,
                    temperature,
                    model=model,
                    _client=self.i_chat_client,
                )
            if self.ingestion_provider == "gemini":
                return self._extract_gemini(
                    prompt,
                    response_model,
                    temperature,
                    model=model,
                    _gemini_client=self.i_gemini_client,
                )
            if self.ingestion_provider == "openai":
                # Reuse main OpenAI extraction (uses i_chat_client via beta parse)
                return self._extract_openai(prompt, response_model, temperature)
            if self.ingestion_provider == "anthropic":
                return self._extract_anthropic(prompt, response_model, temperature)
            if self.ingestion_provider == "huggingface":
                return self._extract_huggingface(prompt, response_model, temperature)
            raise ValueError(
                f"Unsupported ingestion provider: {self.ingestion_provider}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                f"Ingestion extraction failed ({self.ingestion_provider}): {e}"
            )
            try:
                return response_model()
            except Exception:  # pylint: disable=broad-exception-caught
                return None


class _LazyLLMService:
    """Proxy that defers LLMService construction until first attribute access.

    This keeps torch and all LLM provider clients out of the process until
    something actually calls into the service, saving ~150-200 MB of idle RAM.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_real", None)

    def _get_real(self) -> "LLMService":
        real = object.__getattribute__(self, "_real")
        if real is None:
            real = LLMService()
            object.__setattr__(self, "_real", real)
        return real

    def __getattr__(self, name: str):
        return getattr(self._get_real(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._get_real(), name, value)


llm_service = _LazyLLMService()
