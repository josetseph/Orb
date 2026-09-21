"""Multi-provider LLM service supporting chat, generation, and ingestion routing."""

# pylint: disable=wrong-import-order,import-outside-toplevel
import asyncio
import functools
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.log import get_logger
from app.services import model_output, trace
from app.services.credentials import (
    InvalidEndpointError,
    credentials,
    endpoint_credential_id,
    get_api_key,
    normalize_base_url,
    request_base_url,
)
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, model_validator

logger = get_logger("LLMService")


def describe_call_failure(exc: Exception) -> str:
    """One line naming what actually failed, not just the SDK's summary.

    The OpenAI SDK collapses every transport problem into "Connection error."
    — DNS, TLS, refused, reset and dropped connections all read identically,
    which makes a real outage indistinguishable from a misconfigured endpoint.
    The useful detail is in the exception chain.
    """
    parts = [f"{type(exc).__name__}: {exc}".strip()]
    seen = {id(exc)}
    cause = exc.__cause__ or exc.__context__
    while cause is not None and id(cause) not in seen and len(parts) < 4:
        seen.add(id(cause))
        text = str(cause).strip()
        parts.append(f"{type(cause).__name__}{': ' + text if text else ''}")
        cause = cause.__cause__ or cause.__context__
    return " ← ".join(parts)


# Output is never capped by a number chosen here: local runtimes size each
# answer from the context left after the prompt, and cloud endpoints run to
# their own limit. Anthropic is the one API that refuses a request without
# ``max_tokens``, so it gets this single high value everywhere.
ANTHROPIC_MAX_OUTPUT_TOKENS = 16384


class _ResearchStep(BaseModel):
    """One turn of the iterative research loop, exactly as the prompt specifies it."""

    reasoning: str
    finding: str
    answer: str | None
    next_query: str | None

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> "_ResearchStep":
        if bool(self.answer) == bool(self.next_query):
            raise ValueError("set exactly one of answer / next_query")
        return self


class LLMService:
    """Multi-provider LLM client supporting generation and ingestion routing."""

    def __init__(
        self,
        provider: str | None = None,
        *,
        chat_model: str | None = None,
        ingestion_model: str | None = None,
        ingestion_provider: str | None = None,
        base_url: str | None = None,
    ):
        # Per-KB instances pin their models here; the global service leaves these
        # unset and reads ``settings`` (Setup / Settings page) instead.
        self._chat_model_override = (chat_model or "").strip() or None
        self._ingestion_model_override = (ingestion_model or "").strip() or None
        # OpenAI-compatible endpoint for this instance (per-KB), if any.
        self._base_url_override = (base_url or "").strip() or None
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
        logger.info(f"Primary LLM Provider: {self.provider.upper()}")

        # Credential version the clients were built against; a key change
        # bumps the store's version and invalidates cached per-KB services.
        self.credentials_version = credentials.version

        self.init_clients()

    def init_clients(self):
        """Build the chat clients for the main provider and the ingestion provider."""
        self.chat_client, self.gemini_client, self.anthropic_client = self._build_clients(self.provider)

        self.ingestion_provider = self._ingestion_provider_override or self.provider
        if self.ingestion_provider == self.provider:
            # Alias main clients — no extra connections needed.
            self.i_chat_client, self.i_gemini_client, self.i_anthropic_client = (
                self.chat_client, self.gemini_client, self.anthropic_client,
            )
            logger.info("Ingestion LLM: shared with main (%s)", self.ingestion_provider.upper())
        else:
            logger.info("Ingestion LLM: separate provider %s", self.ingestion_provider.upper())
            self.i_chat_client, self.i_gemini_client, self.i_anthropic_client = self._build_clients(
                self.ingestion_provider
            )

    def _build_clients(self, provider: str) -> tuple:  # pylint: disable=too-many-return-statements
        """``(chat_client, gemini_client, anthropic_client)`` for ``provider``; unused slots are None."""
        if provider == "local":
            # In-process GGUF via llama-cpp-python (no HTTP server)
            from app.services.local_models import local_llama_runtime

            logger.info(
                "Initializing in-process llama-cpp-python (backend=%s, model=%s)",
                local_llama_runtime.accel.get("backend"),
                settings.LLM_MODEL,
            )
            return local_llama_runtime.make_chat_client(), None, None

        if provider == "openai_compat":
            # Any OpenAI-compatible server: OpenRouter, Groq, Together, vLLM,
            # LM Studio, llama-server, Ollama's /v1 … The endpoint URL is the
            # credential identity, so each server carries its own key.
            base_url = self.get_base_url()
            if not base_url:
                raise ValueError("No endpoint URL set. Add one in Settings -> AI provider.")
            logger.info("Initializing OpenAI-compatible endpoint at %s", base_url)
            client = OpenAI(
                base_url=request_base_url(base_url),
                api_key=self.get_endpoint_key(base_url),
                timeout=300.0,
                max_retries=2,
            )
            return client, None, None

        if provider == "openai":
            if not get_api_key("openai"):
                raise ValueError("No OpenAI API key. Add one in Settings -> AI provider.")
            logger.info("Initializing OpenAI (Model: %s)", settings.OPENAI_MODEL)
            return OpenAI(api_key=get_api_key("openai"), timeout=300.0), None, None

        if provider == "gemini":
            if not get_api_key("gemini"):
                raise ValueError("No Gemini API key. Add one in Settings -> AI provider.")
            logger.info("Initializing Gemini (Model: %s)", settings.GEMINI_MODEL)
            # 120s per call: long enough for big prompts, short enough to fail
            # fast rather than appear frozen when the API hangs.
            client = genai.Client(
                api_key=get_api_key("gemini"),
                http_options=types.HttpOptions(timeout=120000),
            )
            return None, client, None

        if provider == "anthropic":
            if not get_api_key("anthropic"):
                raise ValueError("No Anthropic API key. Add one in Settings -> AI provider.")
            logger.info("Initializing Anthropic (Model: %s)", settings.ANTHROPIC_MODEL)
            from anthropic import Anthropic

            return None, None, Anthropic(api_key=get_api_key("anthropic"))

        if provider == "huggingface":
            if not get_api_key("huggingface"):
                raise ValueError("No Hugging Face API key. Add one in Settings -> AI provider.")
            if not settings.HUGGINGFACE_MODEL:
                raise ValueError("HUGGINGFACE_MODEL not set in configuration")
            logger.info("Initializing HuggingFace Inference API (Model: %s)", settings.HUGGINGFACE_MODEL)
            client = OpenAI(
                base_url="https://router.huggingface.co/v1",
                api_key=get_api_key("huggingface"),
                timeout=300.0,
                max_retries=3,
            )
            return client, None, None

        raise ValueError(f"Unsupported LLM provider: {provider}")

    def _chat(self, messages: list[dict], **kwargs) -> tuple[str, dict]:
        """``_chat_provider`` behind the experiment cache (``LLM_CALL_CACHE_DIR``; off when unset).

        The key is everything that decides the reply: provider, endpoint, model, the exact
        messages and the generation parameters. Change a prompt, a model or a note and the key
        changes with it, so there are no versions to maintain, and a stage whose inputs did not
        change costs nothing to run again. The reply is stored as the model wrote it.
        """
        started = time.perf_counter()
        ingestion = kwargs.get("ingestion", False)
        model = kwargs.get("model") or (self.get_ingestion_model() if ingestion else self.get_chat_model())
        path = None
        if settings.LLM_CALL_CACHE_DIR:
            key = hashlib.sha256(json.dumps([
                self.ingestion_provider if ingestion else self.provider, self.get_base_url(), model, messages,
                kwargs.get("temperature"), kwargs.get("max_tokens"), kwargs.get("json_mode", False),
                settings.LLAMA_REPEAT_PENALTY, settings.LLAMA_MAX_TOKENS,
            ], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            path = Path(settings.LLM_CALL_CACHE_DIR) / key[:2] / f"{key}.json"
            if path.is_file():
                hit = json.loads(path.read_text(encoding="utf-8"))
                trace.record("llm_call", model=model, ingestion=ingestion, cached=True, seconds=0.0)
                return hit["text"], hit["meta"]
        text, meta = self._chat_provider(messages, **kwargs)
        trace.record("llm_call", model=model, ingestion=ingestion, cached=False,
                     seconds=round(time.perf_counter() - started, 2))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"model": model, "text": text, "meta": meta}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        return text, meta

    def _chat_provider(  # pylint: disable=too-many-locals,too-many-branches
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        ingestion: bool = False,
        json_mode: bool = False,
    ) -> tuple[str, dict]:
        """One chat completion on the main (or ingestion) provider.

        Returns ``(text, meta)`` with ``meta = {"finish_reason", "truncated",
        "thinking"}``. ``thinking`` is the model's chain-of-thought when the
        server exposes it (``reasoning_content`` or ``<think>`` tags), stripped
        from ``text``; ``truncated`` is True when the provider stopped at its
        output limit.

        ``json_mode`` asks the provider for a JSON object structurally where it
        can (OpenAI-style ``response_format``, Gemini ``response_mime_type``,
        llama.cpp's JSON grammar). The reply is parsed as written (``model_output.parse``).
        """
        provider = self.ingestion_provider if ingestion else self.provider
        model = model or (self.get_ingestion_model() if ingestion else self.get_chat_model())
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n\n".join(m["content"] for m in messages if m["role"] == "user")
        if json_mode and "JSON" not in system + user:
            # OpenAI rejects response_format unless the prompt mentions JSON.
            messages = [dict(m) for m in messages]
            last = next(m for m in reversed(messages) if m["role"] == "user")
            last["content"] += "\n\nRespond with a JSON object."
            user += "\n\nRespond with a JSON object."
        try:
            if provider == "gemini":
                client = self.i_gemini_client if ingestion else self.gemini_client
                response = client.models.generate_content(
                    model=model,
                    contents=f"{system}\n\n{user}" if system else user,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                        response_mime_type="application/json" if json_mode else None,
                    ),
                )
                candidates = getattr(response, "candidates", None) or []
                reason = str(getattr(candidates[0], "finish_reason", "") or "") if candidates else ""
                return (response.text or "").strip(), {
                    "finish_reason": reason or None,
                    "truncated": "MAX_TOKENS" in reason.upper(),
                    "thinking": None,
                }

            if provider == "anthropic":
                # json_mode is prompt-driven here: a `{` assistant prefill
                # returns 400 on every Claude model since 4.6, and structured
                # output (output_config.format) needs a full JSON schema.
                # ponytail: thread a pydantic schema through when a caller needs it.
                client = self.i_anthropic_client if ingestion else self.anthropic_client
                kwargs = {"system": system} if system else {}
                if temperature is not None:
                    kwargs["temperature"] = temperature
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens if max_tokens is not None else ANTHROPIC_MAX_OUTPUT_TOKENS,
                    messages=[{"role": "user", "content": user}],
                    **kwargs,
                )
                reason = getattr(response, "stop_reason", None)
                return response.content[0].text.strip(), {
                    "finish_reason": reason,
                    "truncated": reason == "max_tokens",
                    "thinking": None,
                }

            # local / openai / openai_compat / huggingface. No default max_tokens:
            # local sizes output from the context left, cloud uses its own limit.
            client = self.i_chat_client if ingestion else self.chat_client
            kwargs = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            choice = client.chat.completions.create(model=model, messages=messages, **kwargs).choices[0]
            text = choice.message.content or ""
            # LM Studio and some OpenAI-compat servers expose thinking in
            # reasoning_content; others embed <think>…</think> in the content.
            # An unclosed <think> is reasoning the output limit cut off.
            thinking = getattr(choice.message, "reasoning_content", None) or None
            if not thinking and "<think>" in text:
                # The thinking channel, not the answer: split it off, change nothing in either.
                before, _, rest = text.partition("<think>")
                thought, _, after = rest.partition("</think>")
                thinking = thought.strip() or None
                text = before + after
            reason = getattr(choice, "finish_reason", None)
            return text.strip(), {
                "finish_reason": reason,
                "truncated": reason == "length",
                "thinking": thinking,
            }
        except Exception as e:
            logger.error(
                "[LLM] %s call failed via %s: %s",
                provider,
                self.get_base_url() or provider,
                describe_call_failure(e),
            )
            raise

    _REASONING_SYSTEM = (
        "You are a deep reasoning engine. Analyze the input carefully. "
        "Detect conflicts, subtleties, or hidden connections."
    )

    def _reason_step(self, prompt: str, *, json_mode: bool = False) -> tuple[str, str | None]:
        """Like reason(), but also returns the model's thinking (None if it produced none)."""
        text, meta = self._chat(
            [
                {"role": "system", "content": self._REASONING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=json_mode,
        )
        return text, meta["thinking"]

    def reason(self, prompt: str, model: str | None = None) -> str:
        """Uses the Reasoning Model for complex logic/refinement. Returns raw text."""
        text, _ = self._chat(
            [
                {"role": "system", "content": self._REASONING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            model=model,
        )
        return text

    def rewrite_follow_up_query(
        self,
        history: list[dict],
        latest_query: str,
        model: str | None = None,
    ) -> str:
        """Rewrite a follow-up into a standalone retrieval query using recent chat turns."""
        from app.schemas.chat import render_history

        latest = (latest_query or "").strip()
        if not latest or not history:
            return latest

        rendered = render_history(history, 600)
        if not rendered:
            return latest

        prompt = (
            "You rewrite follow-up questions into standalone search queries for a "
            "document collection.\n\n"
            "CONVERSATION:\n"
            + rendered
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

    def summarize_conversation(
        self, existing: str | None, turns: list[dict], model: str | None = None
    ) -> str:
        """Fold ``turns`` (messages that left the history window) into the
        running summary of a long chat. One call; the summary stays short."""
        from app.schemas.chat import render_history

        prompt = (
            "Update the running summary of a conversation between a user and Orb, "
            "their notes assistant. Keep every fact, name, decision and open "
            "question the user may refer back to; drop pleasantries. Plain prose, "
            "under 200 words.\n\n"
            f"CURRENT SUMMARY:\n{existing or '(none)'}\n\n"
            "NEW TURNS:\n" + render_history(turns, 1500) + "\n\nUPDATED SUMMARY:"
        )
        text, _ = self._chat(
            [
                {
                    "role": "system",
                    "content": "You maintain a concise running summary. Output only the summary.",
                },
                {"role": "user", "content": prompt},
            ],
            model=model,
        )
        return (text or "").strip() or (existing or "")

    def _reason_step_sync(self, prompt: str, model: str | None = None) -> str:
        """Synchronous lightweight reasoning call for query rewrite."""
        text, _ = self._chat(
            [
                {"role": "system", "content": "You are a precise query rewriter. Output only the rewritten query."},
                {"role": "user", "content": prompt},
            ],
            model=model,
        )
        return text

    def generate_title(self, text: str, model: str | None = None) -> str:
        """Generates a concise 3-5 word title for a note."""
        if not text or not text.strip():
            return "Untitled Note"
        title = self.generate_text(
            "Generate a concise, descriptive title for the provided note content. Do not use quotes.",
            f"Note content:\n{text}\n\nTitle:",
            model=model,
        )
        return title.replace('"', "")

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        """Synchronously generate a plain-text response from the LLM."""
        text, _ = self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
        )
        return text

    def analyze_query(self, query: str) -> dict:
        """Analyzes user query with structured outputs for better retrieval."""
        from datetime import date

        # The iterative retrieval loop analyses the original question and
        # hybrid_search re-analyses each sub-query; identical query strings recur
        # within a session (temperature=0 → deterministic), so a small per-day
        # cache avoids repeated chat-model calls (and, for local GGUFs, the model
        # swap they force). The date is part of the key because relative-date
        # resolution ("yesterday") depends on today; failures raise, so only
        # successful analyses are cached.
        try:
            return dict(self._analyze_query_cached(query, date.today().isoformat()))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(f"Query analysis failed: {e}")
            return {
                "intent": "search",
                "entities": [],
                "keywords": query.split(),
                "expected_entity_types": [],
                "question_attribute": None,
                "date_filter": None,
                "period_filter": None,
            }

    @functools.lru_cache(maxsize=64)
    def _analyze_query_cached(self, query: str, today: str) -> dict:
        from typing import Literal

        from pydantic import Field

        class QueryAnalysis(BaseModel):
            """Query analysis exactly as the prompt specifies it: every key present, nulls where it says null."""

            intent: Literal[
                "search", "summarize", "compare", "explain", "list", "recent", "verify"
            ] = Field(description="Primary intent of the query")
            entities: list[str] = Field(
                description="Named entities mentioned in the query",
            )
            keywords: list[str] = Field(
                description="Important keywords for semantic search",
            )
            expected_entity_types: list[str] = Field(
                description="Types of entities the answer should be about: Person, Film, Place, Organization, etc.",
            )
            question_attribute: Optional[str] = Field(
                description="What attribute is being asked about: nationality, occupation, birth_date, location, director, capacity, etc.",  # pylint: disable=line-too-long
            )
            date_filter: Optional[str] = Field(
                description="ISO date (YYYY-MM-DD) if the query asks about a specific calendar day, e.g. '2024-05-24'. Null if the query spans a whole month or is not temporal.",  # pylint: disable=line-too-long
            )
            period_filter: Optional[str] = Field(
                description="ISO year-month (YYYY-MM) if the query asks about a whole month or multi-day period, e.g. 'last month', 'in April'. Mutually exclusive with date_filter. Null if date_filter is set or query is not temporal.",  # pylint: disable=line-too-long
            )

        # Same prompt style as ingestion - concrete JSON examples
        prompt = f"""Analyze the following search query and return a structured JSON object.

            Today's date: {today}

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

        raw, _ = self._chat([{"role": "user", "content": prompt}], temperature=0, json_mode=True)
        if not raw:
            raise ValueError("Empty extraction result")
        return model_output.parse(raw, QueryAnalysis, stage="query analysis", model=self.get_chat_model()).model_dump()

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> str:
        """Generic text generation - provider-agnostic (alias comparison, classification, etc)."""
        text, _ = await asyncio.to_thread(
            self._chat,
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return text

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
        if settings.BENCHMARK_MODE:
            answer_desc = "the specific fact the ORIGINAL QUESTION asks for"
            reasoning_rules, output_rules = self._REASONING_RULES, self._OUTPUT_RULES
        else:
            answer_desc = "a complete, natural-language answer to the ORIGINAL QUESTION covering everything relevant you found"
            reasoning_rules, output_rules = self._REASONING_RULES_GENERAL, self._OUTPUT_RULES_GENERAL
        if search_query and docs:
            task_instructions = (
                "Assess the current results and reply with one JSON object with exactly these keys:\n"
                '{"reasoning": "how these documents relate to the question and what you have found so far",\n'
                ' "finding": "what is relevant in these documents — key facts, entities, relationships. '
                "Always write something; 'Not found' only if truly nothing here is relevant\",\n"
                f' "answer": "{answer_desc} — see output rules below — or null if you need more information",\n'
                ' "next_query": "one specific search query, different from all prior ones, or null if you answered"}\n'
                "Set exactly one of answer / next_query.\n\n"
                f"{reasoning_rules}\n"
                f"{output_rules}"
            )
        else:
            task_instructions = (
                "Reply with one JSON object: "
                '{"reasoning": "", "finding": "", "answer": null, '
                '"next_query": "the first specific search query needed to start answering this question"}\n'
            )

        prompt = (
            "You are a research assistant solving a multi-hop question step by step.\n\n"
            f"{conversation_context or ''}"
            f"ORIGINAL QUESTION: {original_question}\n\n"
            f"{prior_block}"
            f"{tried_block}"
            f"{current_block}"
            f"{task_instructions}"
        )

        # Runtime/provider errors (PromptTooLongError, a missing GGUF, an
        # outage) propagate so the chat job reports the real cause; only an
        # unparseable reply is a no-op step.
        raw, step_thinking = await asyncio.to_thread(self._reason_step, prompt, json_mode=True)
        logger.info(f"[LLM] iterative_step raw response:\n{raw}")
        try:
            step = model_output.parse(raw, _ResearchStep, stage="research step", model=self.get_chat_model())
        except model_output.ModelOutputError as e:  # counted; the step produced nothing
            logger.warning(f"[LLM] iterative_step: unusable reply: {e}")
            return {
                "reasoning": "",
                "full_answer": "",
                "can_answer": False,
                "final_answer": None,
                "next_query": None,
                "thinking": None,
            }

        return {
            "reasoning": step.reasoning,
            "full_answer": step.finding,
            "can_answer": step.answer is not None,
            "final_answer": step.answer,
            "next_query": step.next_query,
            "thinking": step_thinking,
        }

    # Benchmark-mode rules (BENCHMARK_MODE=True): the HotPotQA/MuSiQue answer
    # discipline from the 0.74-scoring pipeline — answer type, comparison
    # direction, specificity, exact extraction, past/present.
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
        - For yes/no questions: "answer" must be YES or NO — never an intermediate
        value like a nationality, number, or name. Derive the YES/NO conclusion yourself
        from the evidence before writing it.

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
        - "answer" is only the specific fact the question asks for — nothing else
        - Yes/no question → YES or NO
        - Comparison / either-or → exactly one of the options given
        - Name → full name as it appears in the source
        - Number → include units or qualifiers the question implies
        - Date or time range → exact phrase from source, preserving all connectives
        - Role, title, or position → the role/title only, never the person holding it
        - Location → exact place name as it appears in the source
        - Never answer "Neither" or "Both" unless the question explicitly asks for it
        - If you cannot answer yet → set "next_query", leave "answer" null
        """

    # ── General KB mode rules (BENCHMARK_MODE=False) ──────────────────────────
    # Used when the goal is a thorough, natural-language answer rather than a
    # single extracted fact. A collection may be personal notes, course
    # material, or company documents — the rules stay source-neutral so the
    # model does not narrate someone's meeting minutes as a diary.

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
        - If you cannot answer yet → set "next_query", leave "answer" null
        """

    def get_base_url(self) -> str | None:
        """Endpoint for OpenAI-compatible providers: instance override, then Settings."""
        raw = self._base_url_override or settings.LLM_BASE_URL
        if not raw:
            return None
        try:
            return normalize_base_url(raw)
        except InvalidEndpointError:
            logger.warning("Ignoring malformed endpoint URL %r", raw)
            return None

    @staticmethod
    def get_endpoint_key(base_url: str) -> str:
        """Stored key for an endpoint. Servers that need no auth get a placeholder."""
        try:
            key = get_api_key(endpoint_credential_id(base_url))
        except InvalidEndpointError:
            key = None
        # Local servers (llama-server, LM Studio, Ollama) accept any token; the
        # OpenAI SDK refuses to construct without one.
        return key or "not-needed"

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
            "openai_compat": settings.LLM_MODEL,
            "openai": settings.OPENAI_MODEL,
            "gemini": settings.GEMINI_MODEL,
            "anthropic": settings.ANTHROPIC_MODEL,
            "huggingface": settings.HUGGINGFACE_MODEL,
        }
        return provider_model_map.get(self.provider, settings.LLM_MODEL)

    def get_ingestion_model(self) -> str | None:
        """The model ingestion runs on: the model selected for chat.

        One selection drives both. The only exception is a workspace that ticked
        "use a different model for note ingestion" (the per-instance override).
        """
        return getattr(self, "_ingestion_model_override", None) or self.get_chat_model()

    # ── Ingestion-specific generation ─────────────────────────────────────────

    async def ingestion_generate(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """
        Like ``generate()`` but always routes to the ingestion provider/server.
        Use this for all LLM calls inside the ingestion pipeline.
        """
        content, _meta = await self.ingestion_generate_with_meta(
            prompt, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
        return content

    async def ingestion_generate_with_meta(
        self,
        prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> tuple[str, dict]:
        """``ingestion_generate`` plus ``{"finish_reason", "truncated"}``.

        ``truncated`` is True when the provider stopped at its output limit —
        callers that parse JSON must treat that as an incomplete result, not
        as a malformed one to retry blindly.
        """
        text, meta = await asyncio.to_thread(
            self._chat,
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            ingestion=True,
            json_mode=json_mode,
        )
        if not text:
            raise ValueError(
                "LLM returned empty content (0 output tokens). Possible causes: "
                "context overflow, KV cache pressure, or model crash. Check local "
                "GGUF / API server logs."
            )
        return text, meta

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

    # ── Images ────────────────────────────────────────────────────────────────

    # Metadata comes before the transcription so a long screenshot that hits
    # the output cap loses the tail of its text, not the entities.
    IMAGE_DESCRIBE_PROMPT = (
        "Describe this image for a personal knowledge base, as plain text in "
        "this order: (1) one or two sentences on what it shows and what kind of "
        "image it is (photo, screenshot, poster, chart, document); (2) the "
        "people, places, organisations, dates and events it refers to; (3) ALL "
        "visible text transcribed verbatim, keeping line order — headings, "
        "dates, names, prices, links. No commentary."
    )
    # No output cap: a transcription is cut short only by what the provider
    # itself allows, never by a number chosen here.

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
