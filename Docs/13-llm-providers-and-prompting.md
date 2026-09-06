# LLM Providers and Prompting

**What this covers.** The `LLMService` abstraction in `backend/app/services/llm.py` — provider clients (in-process `local`, OpenAI, Gemini, Anthropic, Hugging Face router, and the OpenAI-compatible shape they all present), the separate ingestion client set, chat/ingestion model-name resolution including the per-KB override layer (`kb_registry.effective_llm_config`, `KBContext.llm`, `/api/v1/kb/{id}/llm`), structured-output strategies (OpenAI parse, Gemini `response_schema`, instructor for Anthropic/HF, prompt-guided JSON + `json_repair` for local), retries/timeouts/fallback provider, thinking extraction, output-truncation metadata, the AI gate (`ai_gate.py`, `AI_SETUP_MODE`), the runtime settings API, and a catalogue of **every prompt** in the backend with its call site, inputs and expected output format. The local GGUF runtime that backs `provider=local` is documented in [12-local-models-and-inference.md](12-local-models-and-inference.md).

**Related docs:** [Local models & inference](12-local-models-and-inference.md) · [Backend core & configuration](06-backend-core-and-configuration.md) · [API reference](07-api-reference.md) · [Knowledge bases & vaults](08-knowledge-bases-and-vaults.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Graph storage](14-graph-storage-kuzu.md) · [Retrieval & chat](16-retrieval-and-chat.md) · [Finance](17-finance-firefly.md) · [Configuration reference](21-configuration-reference.md) · [Decisions & constraints](26-decisions-and-constraints.md)

---

## 1. Responsibilities and boundaries

`LLMService` **owns**: constructing provider SDK clients from `settings`; presenting one call surface (`generate`, `generate_text`, `reason`, `generate_title`, `extract_structured`, `analyze_query`, `iterative_step`, `rewrite_follow_up_query`, `ingestion_generate[_with_meta]`, `ingestion_extract_structured`, `ingestion_count_tokens`, `ingestion_context_tokens`) to every caller; choosing the model name per provider and per KB; JSON cleaning/repair; retry/fallback policy for extraction; parsing the iterative-step protocol; the query-analysis cache; and the prompt text for the calls it makes itself (query analysis, iterative step, title, rewrite, reasoning system prompt, local extraction system prompt).

It does **not** own: the GGUF runtime, tokenisation or context sizing (doc 12 — it only forwards `model`, `messages`, `temperature`, `max_tokens`); the extraction prompt, chunking and merge policy (`workflows/agents/ingestion_agent.py`, `workflows/extraction_chunking.py`, doc 10); community/digest prompts (`workflows/ingestion.py`, doc 14); the finance prompt (`firefly_service.py`, doc 17); retrieval scoring; the HTTP chat endpoints (doc 16). Those prompts are nevertheless catalogued in §9 of this document because they are the complete inventory of LLM prompts in the backend.

## 2. Files

| Path | Purpose | Key exports |
|---|---|---|
| `backend/app/services/llm.py` | Multi-provider service, ingestion routing, structured extraction, JSON cleaning, iterative-step protocol, query analysis, lazy singleton. | `LLMService`, `_LazyLLMService`, `llm_service` |
| `backend/app/services/ai_gate.py` | "Is AI usable right now?" for a provider / the global mode / a KB; 503 guard. | `provider_is_configured`, `ai_is_configured(kb=None)`, `require_ai(kb=None)` |
| `backend/app/api/settings.py` | `GET/PATCH /api/v1/settings` runtime provider/model/base_url changes. | `LLMSettings`, `get_runtime_settings`, `update_runtime_settings` |
| `backend/app/core/runtime_config.py` | Persists mutable overrides (`provider, model, ingestion_model, base_url, ai_setup_mode`) to `DATA_DIR/runtime_config.json`; applied at startup. | `MUTABLE_KEYS`, `load`, `save`, `apply_to_settings` |
| `backend/app/core/config.py` | Settings fields (`LLM_*`, `CHAT_MODEL`, `INGESTION_*`, `*_API_KEY`, `*_MODEL`, `AI_SETUP_MODE`, `BENCHMARK_MODE`, `CHAT_HISTORY_MAX_MESSAGES`, `MAX_LOOP_ITERATIONS`). | `settings` |
| `backend/app/services/kb_registry.py` | Per-KB LLM override storage (`knowledge_bases.llm_provider/llm_model/llm_ingestion_model`), `effective_llm_config`, `build_kb_llm_service`, `KBContext.llm`. | `LLM_PROVIDERS`, `effective_llm_config`, `build_kb_llm_service`, `KBRegistry.set_llm_config`, `KBRegistry.effective_llm` |
| `backend/app/api/kb.py` | `GET/PATCH /api/v1/kb/{id}/llm`, `effective_llm` in `GET /api/v1/kb`. | `KBLLMInput`, `get_kb_llm`, `update_kb_llm` |
| `backend/app/models/kb.py` | SQLAlchemy columns for the override. | `KnowledgeBase.llm_*` |
| `backend/app/workflows/agents/ingestion_agent.py` | Knowledge-Architect extraction prompt, chunked extraction, batched image titling, garbage-name rename prompt. | `_build_extraction_prompt`, `_extract_chunk`, `_extract_with_chunking`, `_batch_image_titles` |
| `backend/app/workflows/extraction_chunking.py` | Pure chunk/merge helpers for long notes. | `chunk_token_budget`, `split_for_extraction`, `merge_extractions`, `MIN_SPLIT_TOKENS` |
| `backend/app/workflows/ingestion.py` | Community name/summary prompts, roll-up prompt, temporal digest prompt, title fallback. | `_build_community_summary`, `_build_rollup_summary`, `build_temporal_digests` |
| `backend/app/workflows/chat.py` | Passes per-KB `llm` to retrieval; follow-up rewrite call; timing lines. | `ChatWorkflow` |
| `backend/app/services/retrieval.py` | Calls `analyze_query` and `iterative_step`; builds `conversation_context`. | `RetrievalService._llm` |
| `backend/app/services/firefly_service.py` | Finance answer prompt (`answer_finance_question`). | — |
| `backend/app/services/multimedia.py` | Cloud vision fallback prompt ("Describe this image briefly."). | `_describe_image_cloud` |
| `backend/app/services/local_models.py` | Reranker system/instruction strings; `LocalOpenAICompat` shim consumed here. | see doc 12 |
| `backend/app/services/embedding.py` | Qwen3 query instruction. | see doc 12 |
| `backend/tests/unit/test_llm_json_cleaning.py`, `test_kb_llm_config.py`, `test_ingestion_chunked_extraction.py`, `test_extraction_chunking.py`, `test_chat_context.py` | Unit coverage for `_clean_json`, override resolution, chunked extraction, chat context trimming. | — |
| `backend/.env.example` | Provider quick-start blocks and key documentation. | — |
| `frontend/src/app/settings/page.tsx`, `frontend/src/app/kb/page.tsx` + `_components/`, `frontend/src/lib/api.ts`, `types.ts` | UI for runtime settings and per-KB LLM override (`getKBLLM`, `updateKBLLM`, `KBLLMConfig`, `EffectiveLLM`). | — |

## 3. Architecture

```mermaid
flowchart TB
  subgraph Callers
    RET[RetrievalService\nanalyze_query, iterative_step]
    CHATWF[ChatWorkflow\nrewrite_follow_up_query]
    ING[IngestionAgent / IngestionWorkflow\ningestion_generate(_with_meta), generate_title, reason, generate_text]
    FIN[FireflyService\ngenerate_text]
    SET[api/settings.py, api_desktop start-local-llm\ninit_clients]
  end
  KB[KBContext.llm\n(pinned LLMService or global llm_service)]
  Callers --> KB
  KB --> SVC[LLMService]
  SVC -->|provider=local| LOCAL[LocalOpenAICompat → LocalLlamaRuntime]
  SVC -->|openai| OA[openai.OpenAI / AsyncOpenAI\n(+instructor.patch)]
  SVC -->|gemini| GM[google.genai.Client\n+ GeminiChatWrapper]
  SVC -->|anthropic| AN[anthropic.Anthropic\n+ instructor.from_anthropic]
  SVC -->|huggingface| HF[openai.OpenAI(base_url=router.huggingface.co/v1)\n+ instructor MD_JSON]
  SVC -.fallback on extraction error.-> FB[LLMService(provider=LLM_FALLBACK_PROVIDER)]
```

### 3.1 Client matrix

Every provider must end up exposing three attributes on the service; `LLMService.__init__` pre-declares them as `None` so attribute access never fails:

| Attribute | Purpose | Consumers |
|---|---|---|
| `chat_client` | sync OpenAI-shaped `chat.completions.create` (for Anthropic it is the raw `Anthropic` client; for Gemini an adapter) | `reason`, `_reason_step`, `_reason_step_sync`, `generate_title`, `generate_text`, `generate` (via `to_thread`), `_extract_local_with_fallback`, `_extract_openai` |
| `async_chat_client` | async twin (`AsyncOpenAI` or `AsyncLocalOpenAICompat`) | declared for batch use; **no current caller** |
| `extraction_client` | structured-output client (`instructor`-patched or the local shim) | `_extract_anthropic` only (OpenAI uses `chat_client.beta…parse`, HF uses `chat_client` with json_object) |
| `gemini_client`, `anthropic_client` | native SDK clients (only set for those providers) | all Gemini/Anthropic branches |
| `i_chat_client`, `i_async_chat_client`, `i_extraction_client`, `i_gemini_client`, `i_anthropic_client` | ingestion twins | `ingestion_generate_with_meta`, `ingestion_extract_structured` |

### 3.2 Request lifecycle for a chat turn (what actually happens per provider)

1. `api/chat.py` → `require_ai(kb)` → `kb.get_chat_workflow().chat(...)`.
2. `ChatWorkflow._retrieve_context` → `self._llm.rewrite_follow_up_query(history, query)` (sync, one chat call; blocks the event loop briefly).
3. `RetrievalService.retrieve_with_self_correction` → `self._llm.analyze_query(query)` (sync `extract_structured`, cached per `(query, today)`), then up to `MAX_LOOP_ITERATIONS` (3) × `await self._llm.iterative_step(...)` (each `asyncio.to_thread(self._reason_step, prompt)`).
4. The final answer text and `thinking` bubble back to the API response.

For `provider=local` every one of those calls goes through `LocalOpenAICompat.chat.completions.create` → `LocalLlamaRuntime.create_chat_completion` (doc 12 §8), so a chat turn is 2 + N generations on the resident GGUF.

## 4. Providers and client construction (`init_clients`, `_init_ingestion_clients`)

`LLMService(provider=None, *, chat_model=None, ingestion_model=None, ingestion_provider=None)`:

- `provider` defaults to `settings.LLM_PROVIDER`; `ollama` / `lm_studio` are mapped to `local` with a deprecation warning (the "local alias" — commit `09e35e3` introduced `local` as the unified name for any OpenAI-compatible server; since `3f21e08` it means in-process llama-cpp only).
- `fallback_provider = settings.LLM_FALLBACK_PROVIDER` **only when `provider` was not passed explicitly** — explicit-provider instances (fallback services and per-KB services) get no fallback, so a failing fallback cannot recurse.
- Keyword overrides are stored as `_chat_model_override`, `_ingestion_model_override`, `_ingestion_provider_override` (blank → `None`; `ollama|lm_studio` → `local`). These are what per-KB pinning uses (§5.3).
- `_query_analysis_cache: dict[(query, today_iso) -> dict]`, max 64 entries FIFO.
- Then `init_clients()` and `_init_ingestion_clients()`.

| Provider | Required settings | `chat_client` | `async_chat_client` | `extraction_client` / native | Timeouts / retries at client level |
|---|---|---|---|---|---|
| `local` | GGUFs on disk (lazily) | `LocalOpenAICompat` | `AsyncLocalOpenAICompat` | same sync shim (`extraction = sync`) | none (in-process); llama errors propagate |
| `openai_compat` | endpoint URL (`get_base_url()`: per-instance override → `LLM_BASE_URL`) + the key stored for that URL | `OpenAI(base_url, api_key, timeout=300.0, max_retries=2)` | `AsyncOpenAI(same)` | `instructor.patch(OpenAI(same), mode=MD_JSON)` | 300 s, 2 retries |
| `openai` | `credentials.get("openai")` (raises `ValueError` naming Settings if missing) | `OpenAI(api_key, timeout=300.0)` | `AsyncOpenAI(timeout=300.0)` | `instructor.patch(OpenAI(timeout=300.0))` (unused in practice) | SDK default `max_retries` (2), 300 s |
| `gemini` | `GEMINI_API_KEY` | `GeminiChatWrapper(gemini_client)` — converts `messages` (system+user concatenated with `\n\n`, assistant turns **dropped**) to one `generate_content(contents=prompt, config=GenerateContentConfig(temperature, thinking_budget=0))`; ignores `max_tokens`, `extra_body`, `response_format` | **not set** (`None`) | `gemini_client = genai.Client(api_key, http_options=HttpOptions(timeout=120000))` (120 s per call) | none beyond SDK; extraction has its own 3-attempt loop |
| `anthropic` | `ANTHROPIC_API_KEY` | the raw `Anthropic(api_key)` client (callers use `.messages.create`) | not set | `instructor.from_anthropic(client, mode=ANTHROPIC_JSON)` | SDK defaults |
| `huggingface` | `HUGGINGFACE_API_KEY` **and** `HUGGINGFACE_MODEL` | `OpenAI(base_url="https://router.huggingface.co/v1", timeout=300.0, max_retries=3)` | `AsyncOpenAI(same)` | `instructor.patch(OpenAI(same), mode=MD_JSON)` (unused: `_extract_huggingface` uses `chat_client`) | 300 s, 3 retries |
| anything else | — | `ValueError("Unsupported LLM provider: …")` | | | |

Notes:

- `settings.LLM_BASE_URL` / `LLM_API_KEY` / `LLM_KEEP_ALIVE` are **not read by `init_clients`** for any provider in the current tree. They survive in config, in `ai_gate.ai_is_configured` (cloud/hybrid heuristics) and in `/api/v1/settings` (which will re-init clients when `base_url` changes — with no effect on the constructed clients). `_with_keep_alive` / `_with_ingestion_keep_alive` return the passed `extra_body` unchanged. A generic "OpenAI-compatible server at `LLM_BASE_URL`" provider therefore does **not** exist today despite `.env.example` hinting at it (see §12 discrepancies).
- `_init_ingestion_clients()`: `ingestion_provider = _ingestion_provider_override or settings.INGESTION_PROVIDER or self.provider` (aliases coerced). If equal to the main provider, the `i_*` attributes alias the main clients ("zero overhead"). Otherwise a second client set is built with the same recipes (Gemini gets only `i_gemini_client`; Anthropic only `i_anthropic_client` + `i_extraction_client`; local/openai/HF get `i_chat_client`, `i_async_chat_client`, `i_extraction_client`). Unsupported → `ValueError`.
- `init_clients()` is re-invoked at runtime by `PATCH /api/v1/settings` (provider or base_url change) and `POST /setup/start-local-llm` (forces `provider="local"`). **Neither re-runs `_init_ingestion_clients()`**, so after a runtime provider switch the `i_*` clients still point at the *old* provider's clients until restart (gotcha §11).

### 4.1 `_LazyLLMService`

`llm_service = _LazyLLMService()` is a proxy that constructs the real `LLMService()` on first attribute get/set (`__getattr__`/`__setattr__` forward). Rationale (docstring): keeps torch and provider clients out of the process until needed, ~150–200 MB idle RAM. Consequences: import of `app.services.llm` is cheap; the first LLM call pays client construction (and for `local` the `detect_llama_backend()` in `LocalLlamaRuntime.__init__` — the runtime singleton itself is created at `local_models` import). `isinstance(llm_service, LLMService)` is `False`; tests bypass with `LLMService.__new__`.

## 5. Model-name resolution

### 5.1 Chat model — `get_chat_model()`

```
_chat_model_override            (per-KB instance)          →
settings.CHAT_MODEL             (runtime_config / .env)    →
provider == local: settings.LLM_MODEL  ("local-chat" placeholder = Setup selection; set to the chat catalog id after a download)
else {openai: OPENAI_MODEL, gemini: GEMINI_MODEL, anthropic: ANTHROPIC_MODEL, huggingface: HUGGINGFACE_MODEL}.get(provider, LLM_MODEL)
```

Callers that accept `model=` (`reason`, `generate_title`, `generate_text`, `generate`, `rewrite_follow_up_query`, `extract_structured` for local/gemini) use the argument first. For `provider=local` the resolved string is passed to the runtime, where `resolve_chat_gguf` maps catalog ids / `.gguf` paths to files and treats `local-chat`/unknown names as "Setup selection" (doc 12 §13.2).

### 5.2 Ingestion model — `get_ingestion_model()`

```
_ingestion_model_override or _chat_model_override   (per-KB) →
settings.INGESTION_MODEL                                     →
by ingestion_provider:
  local/ollama/lm_studio: INGESTION_LLM_MODEL (default "local-chat") or LLM_MODEL
  gemini:      INGESTION_GEMINI_MODEL or GEMINI_MODEL
  openai:      OPENAI_MODEL
  anthropic:   ANTHROPIC_MODEL
  huggingface: HUGGINGFACE_MODEL
```

May return `None` (cloud provider with no model configured) — callers pass it straight through as `model=`; the OpenAI SDK then errors. `_extract_openai` / `_extract_anthropic` / `_extract_huggingface` ignore `model=` and always use `settings.<PROVIDER>_MODEL`; `ingestion_generate_with_meta` for Anthropic likewise hard-codes `settings.ANTHROPIC_MODEL`. Only local and Gemini honour a separate ingestion model in the *extraction* path; `ingestion_generate_with_meta` honours it for local/openai/HF/Gemini.

### 5.3 Per-KB overrides (`kb_registry.py`, `api/kb.py`) — uncommitted working tree

Storage: three nullable TEXT columns on `knowledge_bases` (`llm_provider`, `llm_model`, `llm_ingestion_model`), added by `_ensure_optional_columns` (ALTER TABLE on existing DBs), round-tripped by `_save_row`/`_load_from_sqlite`, mirrored on `KBContext.llm_provider/llm_model/llm_ingestion_model`. `NULL`/blank = inherit. Only chat/ingestion are per-KB; embed/rerank/multimodal are global (embed dims are shared — doc 12 invariant 4).

`effective_llm_config(meta) -> {"provider", "model", "ingestion_model", "inherited"}` (pure, no client construction; tested in `test_kb_llm_config.py`):

```
provider        = meta.llm_provider or settings.LLM_PROVIDER or "local"            (lower-cased)
model           = meta.llm_model or _system_model_for(provider, ingestion=False)
ingestion_model = meta.llm_ingestion_model or meta.llm_model
                  or _system_model_for(provider, ingestion=True) or model
inherited       = no override field set
_system_model_for(provider, ingestion):
    is_global = provider == settings.LLM_PROVIDER
    ingestion and is_global and INGESTION_MODEL → INGESTION_MODEL
    is_global and CHAT_MODEL                     → CHAT_MODEL
    provider==local and ingestion and INGESTION_LLM_MODEL not in (None,"local-chat") → INGESTION_LLM_MODEL
    else {local: LLM_MODEL, openai: OPENAI_MODEL, gemini: GEMINI_MODEL, anthropic: ANTHROPIC_MODEL, huggingface: HUGGINGFACE_MODEL}[provider]
```

Note the deliberate asymmetry: a KB that pins only `llm_model` inherits the **system provider**; a KB that pins only `llm_provider` gets that provider's system default model (`CHAT_MODEL` applies only when the pinned provider equals the global one).

`KBContext.llm` (property): returns the global `llm_service` when nothing is pinned; otherwise builds/caches `build_kb_llm_service(provider, model, ingestion_model)` = `LLMService(prov, chat_model=model, ingestion_model=ingestion_model, ingestion_provider=prov)` keyed by `(provider or LLM_PROVIDER, model, ingestion_model)` so a global provider change rebuilds it. `apply_llm_override(...)` resets `_llm`, `retrieval_service`, `ingestion_workflow`, `chat_workflow` so `_ensure_lazy()` re-creates them with `llm=self.llm` (`RetrievalService(llm=…)`, `IngestionWorkflow(llm=…)`, `ChatWorkflow(retrieval, llm=…)`). `RetrievalService._llm` and `ChatWorkflow._llm` / `IngestionWorkflow._llm` fall back to the global singleton when `llm is None`. `FireflyService.answer_finance_question` uses `kb.llm.generate_text` in `asyncio.to_thread` (comment: a pinned KB may swap a multi-GB GGUF here, which would otherwise block `/chat/status` polls).

`KBRegistry.set_llm_config(kb_id, *, provider, model, ingestion_model)`: normalises (`ollama|lm_studio` → `local`; unknown provider → `ValueError`), persists, and calls `apply_llm_override` on the cached context. `KBRegistry.effective_llm(kb_id)` → resolved dict or `None` (default KB with no row → resolved from `{}`).

API (`api/kb.py`):

| Method & path | Body | Behaviour |
|---|---|---|
| `GET /api/v1/kb` | — | each row now carries `effective_llm` |
| `GET /api/v1/kb/{kb_id}/llm` | — | `{kb_id, override:{provider, model, ingestion_model, base_url}, endpoints:[url, ...], effective:{provider, model, ingestion_model, base_url, inherited}, providers:[local, openai_compat, openai, gemini, anthropic, huggingface], local_models:[{id, label, size_gb, source, architecture?, context_length?, warnings[]}]}`; 404 for unknown non-default KB. `local_models` unions curated downloads (`source: "catalog"`) with **any** chat GGUF found on disk (`source: "discovered"`) — see [12](12-local-models-and-inference.md). |
| `PATCH /api/v1/kb/{kb_id}/llm` | `{provider?, model?, ingestion_model?, base_url?}` — `""`, `null`, `"inherit"`, `"system"`, `"default"` all mean *clear* | Validation: provider ∈ `LLM_PROVIDERS` (400); a provider other than `local`/`openai_compat` must have its key (`provider_is_configured`, 400 "No API key configured for … — add one in Settings → Cloud API keys first."). Effective `local` → `model`/`ingestion_model` resolve as a catalog chat id **or** a GGUF path ref validated by `inspect_chat_model` (400 for a missing file, an unreadable GGUF, an embedding model, or a shard continuation). Effective `openai_compat` → an endpoint URL is required and normalised (400 on non-http(s)), and a `model` is required; switching away clears a stale `base_url`. Then `set_llm_config`; then eagerly touches `ctx.llm` so a bad override surfaces now — on exception the override rolls back to inherit and 400 "Could not initialise that model: …". Returns the same payload as GET. |

Frontend: `api.getKBLLM(id)`, `api.updateKBLLM(id, {provider, model, ingestion_model, base_url})`, types `KBLLMConfig`, `EffectiveLLM`, `KnowledgeBase.llm_*`/`effective_llm`; UI under `frontend/src/app/kb/_components/`.

## Credentials and OpenAI-compatible endpoints

`backend/app/services/credentials.py` is the single source of truth for cloud keys; no module reads `settings.*_API_KEY` at runtime any more.

- **Storage.** The Electron shell encrypts keys with `safeStorage` into `<userData>/credentials.enc` — the app's machine-local folder, never `DATA_DIR`, which is commonly synced — and pushes them to `PUT /api/v1/credentials`. `CredentialStore` keeps them **in memory only**.
- **Platform support.** Windows (**DPAPI**) and macOS (**Keychain**) always have a backend once the app is ready; neither can be "missing". Only Linux can lack one. There, `encryptionStatus()` also rejects the `basic_text` backend — Electron falls back to it when no keyring is found, and it derives the key from a hardcoded constant, so treating it as encryption would tell the user their key is protected when it is not.
- **When encryption is unavailable** (Linux, no keyring) the key is *still pushed to the API and works for that session*; only persistence is lost, and the UI says so. An earlier version failed the whole operation, which made cloud providers unusable on those machines. For durable keys there, install gnome-keyring/kwallet or set the environment variable before launching.
- **Environment fallback.** `_seed_from_env_unlocked()` imports `OPENAI_API_KEY` and friends once, for contributors running the backend outside the shell. Those report `source: "env"`; pushed keys report `source: "keychain"` and win over the seed.
- **Live changes.** Every mutation bumps `credentials.version`, which is part of `KBContext.llm`'s cache key, so per-KB clients rebuild on the next call — no restart.
- **Endpoint identity.** An OpenAI-compatible server is keyed by its **URL**, not by a user-invented name: `endpoint_credential_id(url)` → `endpoint:<normalized url>`. `normalize_base_url` lowercases scheme and host, drops a trailing slash, and strips query/fragment, so `HTTPS://Api.Example.com/v1/` and `https://api.example.com/v1` share one key while two different servers cannot. The same normalisation is implemented in `desktop/credentials.js` and both are covered by tests, because a mismatch would file a key under an id the backend never looks up.
- **Model discovery.** `GET /api/v1/llm/endpoint-models?base_url=` proxies the server's `GET /v1/models`. Servers that do not implement it return 502 and the UI falls back to a free-text model name.

## 6. Structured output and JSON cleaning

### 6.1 `extract_structured(prompt, response_model, temperature=0.1, model=None) -> BaseModel | None`

Dispatch by `self.provider`; **any exception → log, try fallback provider once (dedicated `LLMService(provider=fallback)` instance cached in `_fallback_service`), else return `None`** ("Fail closed — empty Extraction would silently corrupt the graph"). Callers: `analyze_query` (all providers). Ingestion uses `ingestion_extract_structured` (same dispatch on `ingestion_provider`, ingestion clients, **no fallback provider**, and on failure returns `response_model()` (an *empty* instance) or `None`) — note this one is *not* fail-closed; the ingestion agent no longer calls it (it uses free-form `ingestion_generate_with_meta` + `_clean_json`), so today it has no caller.

| Provider | Strategy |
|---|---|
| `local` (`_extract_local`) | System prompt: `"You are a structured extraction engine. Return ONLY valid JSON with no markdown fences and no extra text. The output MUST match this JSON schema exactly:\n<compact model_json_schema()>"`; user = prompt. `_extract_local_with_fallback` tries each `response_format` from `_local_response_format_candidates()` — `LLM_RESPONSE_FORMAT=text` (default) → `[{"type":"text"}]`, otherwise `[json_object, text]` — via `client.chat.completions.create(model, messages, response_format, extra_body, temperature)` (the local shim ignores `response_format` anyway). Result → `_clean_json` → `model_validate_json`; if that fails and the JSON is `{"extraction": {...}}` unwrap it. |
| `openai` (`_extract_openai`) | `chat_client.beta.chat.completions.parse(model=OPENAI_MODEL, messages=[user], response_format=response_model, temperature)` → `.parsed` (native structured outputs, schema enforced server-side). |
| `gemini` (`_extract_gemini`) | Native SDK: `generate_content(model, contents=prompt, config=GenerateContentConfig(temperature, response_mime_type="application/json", response_schema=_inline_schema_refs(schema), thinking_budget=0, automatic_function_calling disabled))` → `json.loads(response.text)` → `model_validate` (so `model_validator(mode="before")` fires). `_inline_schema_refs` flattens `$defs/$ref` because Gemini rejects refs. Retry loop: 3 attempts, `time.sleep(2**attempt)` (2 s, 4 s) on `504 / DEADLINE_EXCEEDED / 503 / UNAVAILABLE / 429 / RESOURCE_EXHAUSTED`; `PROHIBITED_CONTENT` / `content_filter` → immediate `ValueError` (fail closed). |
| `anthropic` (`_extract_anthropic`) | `extraction_client.messages.create(model=ANTHROPIC_MODEL, temperature, messages=[user], response_model=response_model)` — instructor `ANTHROPIC_JSON` mode (prompt-engineered JSON + pydantic validation + instructor's own retries, default `max_retries=1`). No `max_tokens` is passed — instructor supplies a default. |
| `huggingface` (`_extract_huggingface`) | Same system prompt as local; tries `json_object` then `text` on `chat_client`; `_clean_json` → `model_validate_json`. |

### 6.2 `_clean_json(json_str) -> str` (pure; tested in `test_llm_json_cleaning.py`)

1. If the text contains ```` ``` ````, keep only the first fenced block (`re.search(r"```(?:json)?(.*?)```", DOTALL)`).
2. Strip control characters `[\x00-\x08\x0b\x0c\x0e-\x1f]` (keeps `\t`, `\n`, `\r`).
3. Normalise smart quotes: `‘ ’ ‛` → `'`, `“ ” „` → `"`.
4. `json_repair.repair_json(...)` (fixes missing braces/commas, single quotes, trailing text); if `json_repair` is not installed, return the string as-is with a warning.

Used by: `_extract_local`, `_extract_huggingface`, and — as `llm._clean_json` — by the ingestion agent for the extraction JSON. Callers still `json.loads` / `model_validate_json` afterwards; `repair_json` can return `""` for hopeless input, which then raises in the caller.

### 6.3 Truncation metadata — `ingestion_generate_with_meta`

Returns `(content, {"finish_reason", "truncated"})`: Gemini `candidates[0].finish_reason` contains `MAX_TOKENS`; Anthropic `stop_reason == "max_tokens"`; OpenAI-compatible/local `finish_reason == "length"`. `ingestion_generate` is a thin wrapper returning only `content`. The ingestion agent treats `truncated=True` as "split the chunk and re-extract" (doc 10) rather than as malformed JSON to retry. Empty content from an OpenAI-compatible/local response raises `ValueError("Local LLM returned empty content (0 output tokens). …")`.

`max_tokens` policy (working tree): `generate` and `ingestion_generate_with_meta` **no longer default to 10240** — for local the runtime sizes output to the remaining context, for cloud the provider's model maximum applies; Anthropic requires a cap and gets `16384` (ingestion) / `10240` (`generate`) when none is given; `generate_text` passes `max_tokens=1024` to Anthropic only.

## 7. Generation methods, thinking, and the iterative protocol

| Method | Sync/async | Provider paths | System prompt | Returns |
|---|---|---|---|---|
| `generate(prompt, temperature=0.1, max_tokens=None, model=None)` | async (`to_thread`) | gemini native (thinking_budget 0) / anthropic `messages.create` / OpenAI-shaped `chat_client` | none (single user message) | stripped text; re-raises errors |
| `generate_text(system_prompt, user_prompt, model=None)` | sync | gemini: `system\n\nuser` concatenated; anthropic: same concatenation as one user message, `max_tokens=1024`; else system+user messages | caller's | stripped text |
| `reason(prompt, model=None)` | sync | gemini (thinking_budget 0) / anthropic / OpenAI-shaped | `"You are a deep reasoning engine. Analyze the input carefully. Detect conflicts, subtleties, or hidden connections."` (OpenAI-shaped only) | raw text |
| `_reason_step(prompt) -> (content, thinking)` | sync (called via `to_thread` from `iterative_step`) | as `reason`, but extracts thinking (below) | same | `(content, thinking|None)` |
| `_reason_step_sync(prompt, model=None)` | sync | OpenAI-shaped **only** (no gemini/anthropic branch — on those providers `chat_client.chat` raises and `rewrite_follow_up_query` swallows it) | `"You are a precise query rewriter. Output only the rewritten query."` | stripped text |
| `generate_title(text, model=None)` | sync | all three shapes | `"Generate a concise, descriptive title for the provided note content. Do not use quotes."` | title with `"` removed; `"Untitled Note"` for blank input |
| `rewrite_follow_up_query(history, latest_query, model=None)` | sync | via `_reason_step_sync` | — | rewritten query if non-empty and ≤ 300 chars, else the original |
| `analyze_query(query)` | sync | via `extract_structured` (temperature 0) | — | dict (see §9) or safe defaults `{"intent":"search","entities":[],"concepts":[],"keywords":query.split(),…}` |
| `iterative_step(...)` | async (`to_thread(_reason_step)`) | via `_reason_step` | — | protocol dict (below) |
| `ingestion_generate[_with_meta]` | async | ingestion clients | none | text (+meta) |

### 7.1 Thinking extraction

Only `_reason_step` (and therefore `iterative_step`) surfaces thinking:

1. `message.reasoning_content` if the OpenAI-shaped response carries it (LM-Studio-style servers; the local shim never sets it — the local runtime folds `reasoning_content` deltas into `content`).
2. Else, if `<think>` appears in `content`: `thinking = first <think>…</think> block (stripped)`, and **all** `<think>…</think>` blocks are removed from `content`.
3. Gemini and Anthropic branches return `thinking=None` (Gemini `_reason_step` calls `generate_content` **without** `thinking_budget=0`, unlike `reason`/`generate`).

`iterative_step` returns `"thinking": step_thinking`; `RetrievalService.retrieve_with_self_correction` returns the last non-empty thinking as the third tuple element; `ChatWorkflow.chat/retrieve_for_query` put it in `"thinking"`; the chat API persists/returns it on the message (see doc 16 / `schemas/chat.py`). Gemma 4 GGUFs via llama.cpp's chat template do not emit `<think>` unless prompted, so `thinking` is usually `None` on the desktop path; Qwen 3.5/3.6 GGUFs do emit it and it is stripped here.

### 7.2 The iterative-step protocol (`iterative_step`)

Inputs: `original_question`, `accumulated_steps [{query, full_answer, reasoning}]`, `search_query`, `docs [{text,…}]`, `tried_queries`, `conversation_context` (a pre-formatted block from retrieval built from the last `CHAT_HISTORY_MAX_MESSAGES`=24 turns; see doc 16). Output:

```python
{"reasoning": str, "full_answer": str, "can_answer": bool,
 "final_answer": str | None, "next_query": str | None, "thinking": str | None}
```

Parsing: a regex extracts labelled sections `REASONING | FINDING | FULL_ANSWER | ANSWER | NEXT_QUERY` (tolerating `**bold**` labels and multi-line values; first occurrence wins). `ANSWER` not in `{INSUFFICIENT, NONE, N/A, UNKNOWN, NOT FOUND}` → `can_answer`. Else `NEXT_QUERY` (first non-empty line, bullets/asterisks/quotes stripped). Two fallbacks: (a) on the **first** planning turn (no docs) a bare one-line quoted phrase (optionally prefixed `Reply:`/`Query:`, ≤ 200 chars) is accepted as `next_query`; (b) if there is a `FINDING`/`FULL_ANSWER` but neither `ANSWER` nor `NEXT_QUERY` and the finding is not a "not found" token, the finding becomes the final answer. On any exception → all-empty dict with `can_answer=False`. `settings.BENCHMARK_MODE` selects the strict HotPotQA-style rules (`_REASONING_RULES`/`_OUTPUT_RULES`) instead of the personal-KB rules (`_REASONING_RULES_GENERAL`/`_OUTPUT_RULES_GENERAL`).

## 8. AI gating, runtime settings and persistence

### 8.1 `ai_gate.py`

```python
provider_is_configured(provider) -> bool
    local|ollama|lm_studio : gguf_paths_if_present() is not None       # chat+embed GGUFs on disk
    openai|gemini|anthropic|huggingface : bool(settings.<PROVIDER>_API_KEY)
    anything else : False

ai_is_configured(kb=None) -> bool
    if kb.llm_provider is pinned → provider_is_configured(kb.llm_provider)   # KB usable whenever its provider is
    # AI_SETUP_MODE is NOT read — readiness is derived from what exists
    _local_models_present()          → gguf_paths_if_present() is not None
    or any(credentials.has(p) for p in CLOUD_PROVIDERS)
    or _endpoint_is_configured()     → bool(LLM_BASE_URL)   # local servers need no key
    else False

chat_is_local_only() -> bool         # LLM_PROVIDER in (local, ollama, lm_studio, none, "")
derived_setup_mode() -> "local" | "cloud" | "none"          # display only, nothing gates on it

require_ai(kb=None)  → HTTPException 503 {"error": "ai_not_configured", "message": "AI is not configured. Notes, wikilinks, and finance still work. Open Setup to enable local models or a cloud provider."}
```

Used by `POST /api/v1/chat`, `POST /api/v1/chat/start`, `POST /api/v1/notes/reingest-vault`, note ingestion endpoints (`api/notes.py`), admin re-ingest (`api/admin.py`) — all pass the resolved `KBContext` so a KB pinned to a configured provider works regardless of what is set globally. `/setup/status.ai_configured` calls it with no KB.

`AI_SETUP_MODE` is still written by `POST /setup/paths` (into `paths.json`, `runtime_config.json` and live `settings`) and forwarded by the desktop supervisor, because the shell wizard and `resolveBootPath` read it. **Nothing in the backend gates on it** — Setup no longer asks for a mode, since choosing a model on the Models page already answers the question. See [12](12-local-models-and-inference.md).

### 8.2 `GET/PATCH /api/v1/settings` (`api/settings.py`) and `runtime_config.py`

- `GET` → `{"provider": settings.LLM_PROVIDER, "model": llm_service.get_chat_model() or LLM_MODEL, "ingestion_model": llm_service.get_ingestion_model() or LLM_MODEL, "base_url": LLM_BASE_URL}` — touching `llm_service` **constructs the real service** (lazy proxy), so the first Settings page load builds provider clients.
- `PATCH {provider?, model?, ingestion_model?, base_url?}` → merges into `runtime_config.load()`, mutates `settings.LLM_PROVIDER / CHAT_MODEL / INGESTION_MODEL / LLM_BASE_URL`, `runtime_config.save(...)`, and if provider **or** base_url changed: `llm_service.provider = LLM_PROVIDER.lower(); llm_service.init_clients()`. Model-only changes need no re-init because `get_chat_model()` reads `settings` on every call. API keys are never accepted (`.env` only). Provider switch to a cloud provider whose key is missing makes `init_clients()` raise `ValueError` → 500, leaving `settings.LLM_PROVIDER` already changed (and persisted) — the next startup will fail the same way until fixed (gotcha §11).
- `runtime_config.json` (in `DATA_DIR`, fallback `<repo>/data/`): only `MUTABLE_KEYS = {provider, model, ingestion_model, base_url, ai_setup_mode}` are read/written; `apply_to_settings` maps `provider→LLM_PROVIDER`, `model→CHAT_MODEL`, `ingestion_model→INGESTION_MODEL`, `base_url→LLM_BASE_URL`, `ai_setup_mode→AI_SETUP_MODE`. `main.startup_event` applies it before `sync_embedding_infrastructure()`.
- Frontend `settings/page.tsx`: loads `GET /settings`, shows a provider select (`LOCAL_PROVIDERS` set hides model fields for local — "Model name fields only apply to cloud providers"), sends only changed fields via `api.updateLLMSettings(patch)`, and reports whether a restart is needed based on the response. What takes effect without restart: provider/model/base_url for the **chat** clients; what does not: ingestion clients (`_init_ingestion_clients` is not re-run), per-KB pinned services (rebuilt lazily only if their key changes — a global provider change does change the key when the KB inherits the provider), `EmbeddingService` (unaffected — embeddings are always local).

## 9. Prompt catalogue (every LLM prompt in the backend)

Legend — *Call*: which `LLMService` method / client; *Temp*: temperature; *Format*: expected output and how it is parsed. Prompts are quoted verbatim where short; long ones are summarised with their exact skeleton.

### 9.1 Knowledge extraction ("Knowledge Architect") — `workflows/agents/ingestion_agent.py::_build_extraction_prompt(extraction_content)`

- **Purpose**: turn one note (or one chunk of a long note) into `Extraction{title, nodes[{name,type,type_reasoning,isolated_context}], relationships[{source_name,target_name,relationship_type,natural_language,reasoning}]}`.
- **Call**: `llm.ingestion_generate_with_meta(prompt, temperature=0.1)` (free-form generate + `llm._clean_json` + `Extraction.model_validate_json`; comment: grammar-constrained sampling "small models often empty out for nested relationship arrays"). Up to `_MAX_EXTRACTION_ATTEMPTS=3` with `asyncio.sleep(30*(attempt+1))` between attempts ("KV-cache pressure … lets Metal/CPU recover"). If `meta.truncated` and the chunk is > `MIN_SPLIT_TOKENS` (400) and depth < 3 → split in half (`split_for_extraction`) and `merge_extractions`.
- **Inputs**: `extraction_content` = optional `# {user title}\n\n` + note body after multimedia enrichment. Chunking: `chunk_token_budget(llm.ingestion_context_tokens(), overhead=count(_build_extraction_prompt("")))` = `max(400, min(ORB_EXTRACTION_CHUNK_TOKENS|4000, (ctx - overhead - 64)/3.5))`; paragraphs → lines → sentences → words → chars.
- **Skeleton** (≈1.9 k tokens of instructions; all braces doubled in source because it is an f-string):

  ```
  You are a precision knowledge extraction engine. Your sole function is to decompose any input note into a fully structured knowledge graph — extracting every entity, every relationship, and generating an isolated contextual description for each entity as it exists *within the note only*.
  ## CORE RULES  (the note may be personal notes, course material, company/technical docs, meeting records — extract what the text says, do not assume the reader wrote it; extract every entity; no outside knowledge; no hallucinated relationships; mandatory co-reference resolution; directional relationships -> / <->; canonical names)
  ## STEP-BY-STEP PROCESS
  ### STEP 1 — Entity Extraction  (name, type ∈ examples Person/Place/Organization/Event/Work/Thing/Concept/Time Period — "not an exhaustive list", type_reasoning)
  ### STEP 2 — Relationship Extraction (source_name, target_name, snake_case relationship_type, natural_language, reasoning citing the text)
  ### STEP 3 — Node Context Generation (entity-centric, complete sentences, only from the note, unlimited length)
  ## OUTPUT FORMAT  {"title": …, "nodes": [{name,type,type_reasoning,isolated_context}], "relationships": [{source_name,target_name,relationship_type,natural_language,reasoning}]}
  ## WORKED EXAMPLE  ("Ama and Kofi are friends…" → 6 nodes, 8 relationships)
  Now apply this entire process to the following note and return only the JSON output, nothing else:

  {extraction_content}
  ```
- **Model-specific**: none in the prompt; local vs cloud differ only in chunk budget (`ingestion_context_tokens`: `ORB_LLAMA_N_CTX` for local, 128000 for cloud) and truncation detection.


**Source-neutral framing.** Orb is used for personal notes, course material, company and technical documentation and meeting records, so no prompt describes the corpus as the reader's own. The extraction prompt's first CORE RULE says so explicitly ("Do not assume the reader wrote it, and do not describe it as someone's personal knowledge"), the query rewriter says "document collection", and the temporal-digest summariser says "documents … without assuming who wrote them or why". Keep new prompts neutral: a model told it is reading a personal knowledge base narrates a company's meeting minutes as if they were a diary.
### 9.2 Batched image titling — `ingestion_agent.py::_batch_image_titles(llm, items)`

- **Purpose**: name each Florence-described image once per note so the enrichment block reads `[Image: <title>]` (placeholder tokens `{{ORB_IMAGE_TITLE_n}}` are substituted; filename fallback). Batched deliberately: calling the chat GGUF per image evicted Florence each time.
- **Call**: `llm.ingestion_generate(prompt, temperature=0.0)`.
- **Prompt**: `"For each numbered image description below, give a concise, specific title (2–6 words) that would serve as a unique entity name.\n\n{lines}\n\nReturn ONLY a JSON array: [{\"index\": 1, \"title\": \"...\"}, ...]"` where each line is `N. (file: <filename>) "<description[:600]>"`.
- **Format**: first `[...]` via regex → `json.loads`; titles kept if `1 < len ≤ 80`; any failure → `{}`.

### 9.3 Garbage-name recovery — `ingestion_agent.py::extraction_node` (`rename_prompt`)

- **Purpose**: nodes whose `name` ∈ `{untitled, none, unknown, ""}` but that have `isolated_context` get a specific name; unnamed context-less nodes are dropped.
- **Call**: `llm.ingestion_generate(rename_prompt, temperature=0.0)`.
- **Prompt**: `"For each numbered excerpt below, provide the most specific descriptive name for the node it describes (1–5 words each).\n\nReturn null if an excerpt has insufficient information to name specifically.\n\nReturn ONLY a JSON array: [{\"index\": 1, \"name\": \"Name Here\"}, {\"index\": 2, \"name\": null}, ...]\n\n{batch_lines}\n\nReturn ONLY: [{\"index\": 1, \"name\": \"...\"}, ...]"`, lines `N. (type=<type>) "<isolated_context>"`.
- **Format**: first non-greedy `[...]`; accepted names `2 < len ≤ 80` and not garbage; then de-duplicated case-insensitively against clean nodes.

### 9.4 Note title fallback — `LLMService.generate_title(text, model)`

- **Purpose**: title when the extraction produced none (`IngestionWorkflow._write_ontology`, with `model=get_ingestion_model()`).
- **Prompts**: OpenAI-shaped: system `"Generate a concise, descriptive title for the provided note content. Do not use quotes."`, user `"Note content:\n{text}\n\nTitle:"`. Gemini/Anthropic: single message `"Generate a concise, descriptive title for this note. Do not use quotes.\n\nNote content:\n{text}\n\nTitle:"`.
- **Format**: plain text; `"` stripped. Blank input → `"Untitled Note"` without a call.

### 9.5 Community name + summary — `workflows/ingestion.py::_build_community_summary(member_rows, level, strict_naming)`

- **Purpose**: Leiden community (L2) naming/summarisation from member nodes.
- **Call**: `self._llm.reason(prompt, model=get_ingestion_model())` (system prompt "deep reasoning engine" on OpenAI-shaped providers).
- **Prompt skeleton**: `"The following entities are closely related based on how they appear across a knowledge graph.\n\nCluster level: {level}  [L2 = most fine-grained → L1 = mid-level → L0 = broadest]\nEntities ({n} total):\n{- name (Label): ctx | ctx …}\n\nGenerate:\n1. A short descriptive name …\n2. A thorough summary … Write as many sentences as needed.\n\nName rules:\n- Use plain, natural language\n- Anchor the name in concrete topics …\n- Do NOT use meta-labels like 'Node Cluster', 'Isolated Group', 'Cluster L2-14', or any variant\n- Do NOT use the words isolated / node / cluster / community / group as the central theme\n\nReply in EXACTLY this format — no preamble, no trailing text:\nNAME: <name>\nSUMMARY: <summary>"` + (strict retry) `"\n\nThis is a retry because the previous name was too generic. The NAME must be specific and user-facing."`.
- **Format**: `NAME:` / `SUMMARY:` lines parsed by `_parse_name_summary`; generic names (token list in `_is_generic_community_name`) trigger one strict retry, then `_derive_fallback_community_name`. Community summaries are embedded with `embed_documents` (doc 12/14).

### 9.6 Community roll-up (L1/L0) — `workflows/ingestion.py::_build_rollup_summary(child_rows, level, strict_naming)`

- Same call/format as 9.5; prompt: `"The following are descriptions of {n} related groups that together form a broader connected topic.\n\nCluster level: {level} …\n\nGroups:\n{- name: summary}\n\nSynthesize:\n1. A short descriptive name capturing the overarching theme across all groups\n2. A thorough summary covering the shared themes, what connects the groups, major patterns, and the big-picture significance. …\n\nName rules: (same)\n\nReply in EXACTLY this format …\nNAME: <name>\nSUMMARY: <summary>"`.

### 9.7 Temporal digest — `workflows/ingestion.py::build_temporal_digests`

- **Purpose**: per-period (month/week/year, `TEMPORAL_DIGEST_PERIOD`) summaries of note contexts when `TEMPORAL_DIGESTS_ENABLED`.
- **Call**: `self._llm.generate_text(system_prompt, user_prompt)`.
- **System**: `"You are a knowledge synthesis assistant. Summarize the main topics, events, and themes from the provided …"` (continues with instructions to produce a digest for the labelled period). **User**: the period label (`"May 2024"`, `"Week 12, 2024"`, `"2024"`) plus contexts joined with `\n---\n`, truncated at 12 000 characters + `"\n...[truncated]"`.
- **Format**: free text stored as the digest node summary.

### 9.8 Query analysis — `LLMService.analyze_query(query)` (structured)

- **Purpose**: retrieval planning — entities, expected entity types, question attribute, intent, keywords, date/period filters.
- **Call**: `extract_structured(prompt, QueryAnalysis, temperature=0)`; cached per `(query, today)`; safe defaults on failure.
- **Schema** (`QueryAnalysis`): `intent ∈ {search, summarize, compare, explain, list, recent, verify}`, `entities[]`, `concepts[]`, `keywords[]`, `expected_entity_types[]`, `question_attribute?`, `date_filter? (YYYY-MM-DD)`, `period_filter? (YYYY-MM)`.
- **Prompt skeleton**: `"Analyze the following search query and return a structured JSON object.\n\nToday's date: {today}\n\nQUERY: \"{query}\"\n\nReturn a JSON object with these fields:\n- \"entities\": Complete named entities exactly as written — never split multi-word names. …\n- \"entity_types\" …\n- \"question_attribute\" …\n- \"intent\": One of — search / compare / summarize / explain / list\n- \"keywords\" …\n- \"date_filter\" …\n- \"period_filter\" …\n\nEXAMPLES:\n(8 query → JSON pairs, e.g. Einstein/Curie nationality, 1984 award, Madison Square Garden capacity, Inception director, 24 May 2024, March 3rd 2023, last month, in April)\n\nReturn only the JSON object, no preamble or explanation."`
- **Model-specific**: the prompt text says `"entity_types"` while the schema field is `expected_entity_types` (the examples use the schema name); the intent list in the prose omits `recent`/`verify` that the schema allows. Local providers additionally get the generic JSON-schema system prompt from `_extract_local`. The two example month answers are hard-coded to `2026-04`.

### 9.9 Follow-up rewrite — `LLMService.rewrite_follow_up_query(history, latest_query)`

- **Purpose**: turn a follow-up into a standalone retrieval query (pronoun resolution) before retrieval.
- **Call**: `_reason_step_sync` (system `"You are a precise query rewriter. Output only the rewritten query."`), OpenAI-shaped providers only.
- **Prompt**: `"You rewrite follow-up questions into standalone search queries for a document collection.e.\n\nCONVERSATION:\n{User:/Assistant: lines, last 24 turns, each content truncated to 600 chars}\n\nLATEST USER MESSAGE: {latest}\n\nReturn ONE standalone search query that captures what the user is asking now, resolving pronouns and references from the conversation.\nDo not answer the question. Do not add explanation.\nReply with only the rewritten query."`
- **Format**: single line; quotes stripped; accepted if ≤ 300 chars; else original query. Skipped entirely when there is no history. Tested in `test_chat_context.py`.

### 9.10 Iterative research step — `LLMService.iterative_step(...)`

- **Purpose**: one hop of the retrieval loop (plan first query → assess docs → `ANSWER` or `NEXT_QUERY`).
- **Call**: `_reason_step` (system `"You are a deep reasoning engine. …"` on OpenAI-shaped providers; Gemini/Anthropic get the prompt alone).
- **Prompt assembly**:

  ```
  You are a research assistant solving a multi-hop question step by step.

  {conversation_context}            # from retrieval: "CONVERSATION SO FAR:\n" + "User: …"/"Assistant: …" lines (last 24 turns) + "\n\n"
  ORIGINAL QUESTION: {original_question}

  PRIOR FINDINGS:\nStep i: Searched for '<q>'\n  Reasoning: <r>\n  Finding: <fa or 'Not found'>   (when accumulated_steps)
  QUERIES ALREADY TRIED (do NOT repeat these — choose a different angle):\n  - q …            (when tried_queries)
  CURRENT SEARCH: '<search_query>'\n\nRETRIEVED DOCUMENTS:\n<doc texts joined by \n\n---\n\n>   (when docs)
  <task instructions>
  Reply:
  ```

  Task instructions, first turn (no docs): `"Output the first search query needed to start answering this question:\nNEXT_QUERY: <one specific search query>\n"`. With docs, general mode: `"Assess the current results:\nREASONING: <…>\nFINDING: <a summary of what is relevant … Always write something; use 'Not found' only if truly nothing here is relevant>\n\nThen decide:\n  If you have enough information to answer the ORIGINAL QUESTION:\n  ANSWER: <a complete, natural-language answer …>\n\n  If you need more information:\n  NEXT_QUERY: <one specific search query, different from all prior ones>\n\n{_REASONING_RULES_GENERAL}\n{_OUTPUT_RULES_GENERAL}"`. `BENCHMARK_MODE=True` swaps in the strict single-fact rules (`_REASONING_RULES`: chain tracing, comparison/yes-no discipline, answer type, specificity, exact extraction, temporal; `_OUTPUT_RULES`: bare fact only, YES/NO, one option, exact phrase…).
- **Format**: labelled sections parsed as in §7.2.

### 9.11 Local structured-extraction system prompt — `LLMService._extract_local` / `_extract_huggingface`

`"You are a structured extraction engine. Return ONLY valid JSON with no markdown fences and no extra text. The output MUST match this JSON schema exactly:\n{compact JSON schema}"` — prepended to any `extract_structured` prompt on local/HF (today: `analyze_query`).

### 9.12 Reasoning system prompt — `reason`, `_reason_step`

`"You are a deep reasoning engine. Analyze the input carefully. Detect conflicts, subtleties, or hidden connections."` (OpenAI-shaped providers only).

### 9.13 Finance answer — `firefly_service.py::answer_finance_question`

- **Call**: `await asyncio.to_thread(kb.llm.generate_text, system, user)`.
- **System**: `"You answer finance questions for Orb using two sources:\n1. Firefly III ledger data (balances, transactions, reports) — treat as authoritative for numbers.\n2. Relevant knowledge-base notes — use for budgets, plans, context, and explanations.\nBlend both when helpful. Be concise and explicit about date ranges. Do not invent transactions or balances. If notes conflict with ledger data, prefer the ledger."`
- **User**: `"User question:\n{query}"` + optional `"Rewritten retrieval query:\n{rq}"` + `"Firefly finance context JSON:\n{json.dumps({workspace, summary(30 days)}, indent=2)}"` + `"Relevant note passages from KB \"{name}\":\n[Title] text[:1200] …"` or `"No relevant note passages were retrieved from KB \"{name}\"."`, joined by `\n\n`.
- **Format**: free text.

### 9.14 Cloud vision fallback — `multimedia.py::_describe_image_cloud`

Used only when local Florence is unavailable and `OPENAI_API_KEY` or `GEMINI_API_KEY` is set: OpenAI `chat.completions.create(model=OPENAI_MODEL or "gpt-4o-mini", messages=[{text:"Describe this image briefly.", image_url:data URL}], max_tokens=400)` or Gemini `generate_content(model=GEMINI_MODEL or "gemini-2.0-flash", contents=["Describe this image briefly.", image part])`. Bypasses `LLMService` entirely.

### 9.15 Non-LLM model prompts (for completeness)

- **Florence-2 task token**: `<MORE_DETAILED_CAPTION>` (doc 12 §12.3).
- **Qwen3 reranker**: system `"Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\"."`, instruct `"Given a question, retrieve relevant passages that answer the question"`, ChatML with empty `<think>` (doc 12 §10).
- **Qwen3 embedding query instruction**: `"Instruct: Given a question, retrieve relevant context.\nQuery: "` (doc 12 §9.2); documents get no instruction (`workflows/ingestion.py` comment: "Documents are embedded without any instruction prefix").
- **Whisper**: `language="en", task="transcribe"`; **Marlin**: `model.caption(video_path)` (remote-code prompt).

## 10. How to add a provider

Files to touch, in order:

1. `backend/app/core/config.py` — add `<NAME>_API_KEY: str | None`, `<NAME>_MODEL: str | None` (and an ingestion-specific model key if needed). Document them in `backend/.env.example`.
2. `backend/app/services/llm.py`:
   - `init_clients()` — new `elif self.provider == "<name>":` branch that sets `chat_client` (OpenAI-shaped if at all possible — every generation method assumes `chat.completions.create(...).choices[0].message.content`), `async_chat_client`, `extraction_client` (or a native client attribute).
   - `_init_ingestion_clients()` — mirror the branch for the `i_*` attributes.
   - `get_chat_model()` / `get_ingestion_model()` maps.
   - `extract_structured()` + `ingestion_extract_structured()` dispatch and a `_extract_<name>()` (reuse `_extract_huggingface`'s prompt-guided JSON approach for any OpenAI-compatible endpoint).
   - If the provider is *not* OpenAI-shaped, add branches to `generate`, `generate_text`, `reason`, `_reason_step`, `_reason_step_sync`, `generate_title`, `ingestion_generate_with_meta` (return `truncated` correctly), as the Gemini/Anthropic branches do.
3. `backend/app/services/ai_gate.py` — add to `_CLOUD_KEYS` so `provider_is_configured` and the KB PATCH validation know about it.
4. `backend/app/services/kb_registry.py` — add to `LLM_PROVIDERS` and to `_system_model_for`'s map.
5. Frontend: `settings/page.tsx` `PROVIDERS` / `CLOUD_MODEL_HINTS`, and the KB model panel provider list comes from the API (`providers`).
6. `backend/requirements.txt` — SDK pin.
7. Tests: extend `test_kb_llm_config.py` (resolution) and add a `_svc()`-style unit test for the new `_extract_<name>` parsing.

For a plain OpenAI-compatible HTTP server (vLLM, LM Studio, Ollama's OpenAI endpoint) the smallest change is a new branch that builds `OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY or "local")` and routes extraction through `_extract_local` with `_client=` — `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_RESPONSE_FORMAT` and the `_local_response_format_candidates` machinery already exist for exactly that shape but are currently unreachable.

## 11. Invariants, gotchas and failure modes

Invariants / locked decisions:

- **`local` = in-process llama-cpp only**; `ollama`/`lm_studio` are aliases, not providers. No HTTP sidecar for chat.
- **Extraction fails closed**: `extract_structured` returns `None` on error (after one fallback attempt); Gemini content-filter errors are never retried. Callers must treat `None` as "do not write anything".
- **Fallback services and per-KB services never get a fallback of their own** (no recursion).
- **Per-KB overrides cover chat + ingestion only**; embed/rerank/multimodal stay global.
- **Only downloaded local chat GGUFs may be pinned**; the API validates and the runtime raises if that ever diverges.
- **Query analysis is cached per day**; the cache key includes today's date because relative dates resolve differently.
- **Model-only settings changes must not require client re-init** (`get_chat_model` reads settings live) — keep that property when adding fields.

Gotchas:

- `llm_service` is a lazy proxy; `isinstance` checks fail and the first attribute access (including `GET /api/v1/settings`) constructs clients.
- `PATCH /settings` re-runs `init_clients()` but not `_init_ingestion_clients()`; ingestion keeps the previous provider's clients until restart. If `init_clients()` raises (missing key) the persisted provider is already switched.
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_KEEP_ALIVE` are inert for client construction; the Settings UI still shows `base_url`.
- Gemini `chat_client` adapter drops assistant turns and `max_tokens`; `async_chat_client` is `None` for Gemini/Anthropic — never call it without a guard.
- `_reason_step_sync` (query rewrite) has no Gemini/Anthropic branch → rewrite silently falls back to the raw query on those providers.
- `_extract_openai/_extract_anthropic/_extract_huggingface` ignore `model=`; `ingestion_extract_structured` on OpenAI uses the **main** `chat_client`, not `i_chat_client` (comment says otherwise).
- `ingestion_extract_structured` returns an *empty* `response_model()` on failure (not `None`) — the opposite of `extract_structured`. It currently has no callers; do not reintroduce it for graph writes without changing that.
- `analyze_query` prompt/schema field-name mismatch (`entity_types` vs `expected_entity_types`) — the schema wins because the examples use the schema name.
- The extraction prompt is an f-string: every literal `{`/`}` must be doubled.
- `generate_title` strips all `"` characters, including ones inside the title.
- Thinking is only extracted in `_reason_step`; `reason()` returns `<think>` blocks verbatim (community prompts parse `NAME:`/`SUMMARY:` after them, which works only if the model puts the labels after the think block).
- The local runtime raises `PromptTooLongError` / `RuntimeError` for too-long prompts or missing per-KB GGUFs; `iterative_step` swallows all exceptions into an empty result (the loop then ends with "couldn't find enough information"), while `generate`/`ingestion_generate` re-raise.
- `ai_is_configured` for `cloud`/`hybrid` is effectively always true once the mode is set (non-empty `LLM_BASE_URL` default).

Failure modes:

| Situation | Behaviour |
|---|---|
| Missing API key for configured provider | `ValueError` at first `llm_service` access → 500s on chat/ingest; `/setup/status.ai_configured` may still say true for cloud mode |
| Gemini 429/503/504 | 3 attempts with 2 s / 4 s backoff (extraction only); other calls fail immediately |
| Gemini content filter | `ValueError`, no fallback within `_extract_gemini` (but `extract_structured`'s outer fallback provider still applies) |
| Local model missing / not downloaded | `RuntimeError` from runtime → extraction `None` / chat error; per-KB pinned missing model → explicit "not downloaded" error |
| Truncated ingestion output | `truncated=True` → chunk split (doc 10); for chat loop the answer is simply cut |
| Malformed JSON | `_clean_json` + `json_repair`; still-invalid → exception → retry (ingestion, 3× with 30/60 s waits) or `None` (query analysis → safe defaults) |
| Empty local output | `ValueError("Local LLM returned empty content…")` → retry path |

## 12. Discrepancies noticed and history

Discrepancies (code vs docs/comments):

- `.env.example` and `desktop/binaries/README.md` describe `LLM_BASE_URL` / OpenAI-compatible HTTP as a supported path; `init_clients` has no such branch.
- `ingestion_extract_structured` docstring says OpenAI "uses i_chat_client via beta parse" — it calls `_extract_openai`, which uses `self.chat_client`.
- `analyze_query` prose lists five intents, schema allows seven; prose says `entity_types`, schema `expected_entity_types`.
- `EmbeddingService.is_qwen3` staleness (doc 12 §18) affects query embeddings used by this layer's retrieval.
- README-era mention of `OPENAI_MODEL_REASONING` was removed in `28ea18e`; no reasoning-model split remains — `reason()` uses the chat model.

History:

- `09e35e3` (2026-05-13): "local" alias for OpenAI-compatible servers, `INGESTION_*` keys, separate ingestion clients (`_init_ingestion_clients`), `ingestion_generate`, `ingestion_extract_structured`, `CHAT_MODEL`/`INGESTION_MODEL` precedence.
- `28ea18e` (2026-05-20): `get_chat_model`/`get_ingestion_model`/`init_clients` consolidation, `_LazyLLMService`, removal of `OPENAI_MODEL_REASONING`, deferred heavy imports.
- `a8587e6` (2026-06-12): default torch device CPU in containers; HTTP sidecars (since removed).
- `3f21e08` (2026-08-02): local = in-process llama-cpp; `ollama`/`lm_studio` deprecated aliases; `_with_keep_alive` became a pass-through.
- `fbcafe7` (2026-08-03): `api/settings.py` router, `runtime_config.py` shape.
- `f8f527f` (2026-08-06): fallback isolated into a dedicated `LLMService` instance ("mutating the shared singleton's provider/clients would race with concurrent chat/ingestion calls").
- `8de5cda` (2026-08-07): `_query_analysis_cache` (per-day memo; avoids repeated model swaps on local).
- Uncommitted working tree (2026-09): per-KB overrides (`LLMService` kwargs, `kb_registry.effective_llm_config`, `KBContext.llm`, `/kb/{id}/llm`), `ai_gate.provider_is_configured` + `require_ai(kb)`, `ingestion_generate_with_meta` with truncation metadata, no default `max_tokens`, `ingestion_count_tokens`/`ingestion_context_tokens`, chunked extraction + batched image titling in the ingestion agent, `[Timing]` lines via `services/timing.py`.
