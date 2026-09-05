# Configuration reference

**What this covers.** Every configuration knob the Orb backend and desktop shell read: pydantic `Settings` fields (env vars / `backend/.env`), the `ORB_*`/`LIVEOS_*` environment variables read directly via `os.environ`, the two JSON bootstrap files (`paths.json`, `DATA_DIR/runtime_config.json`), the per-KB LLM override columns, the desktop port variables, and the layer that sets each value (first-run wizard, Electron supervisor, `.env`, runtime config, Docker compose, or hard-coded). It also states the precedence rules and the differences between desktop, Docker and bare-uvicorn runs. The narrative explanation of how `Settings` is built lives in [Backend core](06-backend-core-and-configuration.md); this file is the lookup table.

**Related docs:** [Backend core and configuration](06-backend-core-and-configuration.md) · [Desktop shell](04-desktop-shell.md) · [Packaging, build and release](05-packaging-build-and-release.md) · [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Local models and inference](12-local-models-and-inference.md) · [LLM providers and prompting](13-llm-providers-and-prompting.md) · [Search indexes](15-search-indexes-qdrant-meilisearch.md) · [Retrieval and chat](16-retrieval-and-chat.md) · [Finance / Firefly](17-finance-firefly.md) · [Frontend architecture](18-frontend-architecture.md) · [Data directory layout](22-data-directory-layout.md) · [Logging and observability](23-logging-and-observability.md) · [Development guide](27-development-guide.md)

## 1. How to read the tables

| Column | Meaning |
|---|---|
| Name | Environment variable / `Settings` field. Legacy aliases in parentheses. |
| Type / default | Python type as declared, and the code default (not the `.env.example` value when they differ). |
| Read in | File and function that consumes the value. "`Settings`" means it is a pydantic field on `backend/app/core/config.py::Settings`; "env" means read directly with `os.environ` / `_env_first`. |
| Effect | What changes. |
| Set by | Which layer normally provides the value: **wizard** (`paths.json` written by the Electron first-run wizard or `POST /api/v1/setup/paths`), **supervisor** (`desktop/supervisor.js` injects it into the uvicorn child), **.env** (`backend/.env`), **runtime** (`DATA_DIR/runtime_config.json` via `PATCH /api/v1/settings` / Setup), **manifest** (`MODELS_DIR/models_manifest.json` selection written by Setup), **compose** (`docker-compose.yml`), **code** (hard-coded default; not normally overridden). |

Markers: **unused** = declared but never read in `backend/app`; **overridden** = whatever you set is replaced by code.

## 2. Sources and precedence

### 2.1 For `Settings` fields

pydantic-settings resolves each field as: **process environment** > `backend/.env` (absolute path `BACKEND_DIR / ".env"`, `extra="ignore"`) > **field default** (some defaults are themselves computed from `paths.json`). Then, in this order, code mutates the live object:

1. `config.py` bottom: `KUZU_DB_PATH := <DATA_DIR>/kuzu/kuzu_graph`, `MODELS_PATH := MODELS_DIR` (always).
2. `main.startup_event`: `runtime_config.apply_to_settings()` — `LLM_PROVIDER`, `CHAT_MODEL`, `INGESTION_MODEL`, `LLM_BASE_URL`, `AI_SETUP_MODE` from `runtime_config.json` **win over env/.env**.
3. `main.startup_event`: `local_models.sync_embedding_infrastructure()` — `EMBEDDING_DIMENSIONS`, `EMBEDDING_MODEL`, `MODEL_RERANKER_LOCAL` from the models manifest **win over env/.env**.
4. Later, at user action: `PATCH /api/v1/settings`, `POST /api/v1/setup/paths`, Setup model selection (`ensure_chat_and_embed_models` also sets `LLM_MODEL`).

So for the provider/model axis the effective order is: **per-KB override (row in `knowledge_bases`) > `runtime_config.json` > env > `.env` > default**; for embedding dims/model: **manifest > env > `.env` > default**; for paths: **env > `paths.json` > repo fallback**.

### 2.2 For directly-read env vars

`paths.py` and `local_models.py` use `_env_first(...)`, which returns the first variable in the list that is non-empty (`paths._env_first` treats any truthy string as set; `local_models._env_first` also ignores whitespace-only). The `ORB_*` name is always listed before its `LIVEOS_*` alias, so `ORB_*` wins when both are set. These are read **at call time** (each model load, each download), except `CHAT_MODEL_ID` / `EMBED_MODEL_ID` / `RERANK_MODEL_ID` (`ORB_*_GGUF`) which are module constants evaluated once at import.

### 2.3 Layers per run mode

```mermaid
flowchart LR
    subgraph desktop["Desktop (Electron)"]
        W[Wizard → paths.json] --> S[supervisor.js env injection]
        S --> U[uvicorn app.main:app :17401]
        RC[DATA_DIR/runtime_config.json] --> U
        MF[MODELS_DIR/models_manifest.json] --> U
        ENV[backend/.env - optional, for API keys] --> U
    end
    subgraph docker["docker-compose (contributors)"]
        CE[compose environment: block] --> B[uvicorn :8000 → host 8700]
        EF[env_file backend/.env] --> B
    end
    subgraph bare["bare uvicorn (dev)"]
        SH[shell env] --> D[uvicorn any port]
        E2[backend/.env] --> D
        PJ[paths.json in App Support, if present] --> D
    end
```

| Aspect | Desktop | Docker compose | Bare uvicorn |
|---|---|---|---|
| `DATA_DIR` | `ORB_DATA_DIR` from `paths.json` (default `~/Library/Application Support/Orb/data`) | not set → `paths.json` if the container has one (it does not) → `/app/../data` = `/data` volume mount | env, or `paths.json` from a previous desktop run (!), or `<repo>/data` |
| `MODELS_DIR` | `ORB_MODELS_DIR` from `paths.json` | `/app/models` fallback | env / `paths.json` / `backend/models` |
| DB | SQLite `DATA_DIR/orb.db` (`DATABASE_BACKEND=sqlite` injected) | Postgres via `DATABASE_BACKEND=postgres` + `DATABASE_TRANSACTION_POOLER_URL=postgresql://user:password@postgres:5432/orb` | SQLite unless both Postgres vars set |
| Qdrant | `127.0.0.1:17433` (injected) | `qdrant:6333` | default `127.0.0.1:6333` |
| Meili | `127.0.0.1:17470`, key from `DATA_DIR/meili_master_key` | `meilisearch:7700`, `orb-dev-key` | `127.0.0.1:7700`, `orb-dev-key` |
| Firefly | `FIREFLY_BASE_URL=http://127.0.0.1:17412`, `FIREFLY_RUNTIME_FILE=DATA_DIR/firefly/runtime.json` | not configured (finance unavailable) | not configured unless set |
| AI mode | derived from configuration (`ai_gate.derived_setup_mode()`); `AI_SETUP_MODE` is persisted but not read | same | same |
| CORS | `http://127.0.0.1:17400,http://localhost:17400` | default 3700/3701 list (frontend served on 3700) | default |
| Logs | `DATA_DIR/logs/*.log` + supervisor `backend.log` | `/data/logs` inside container + docker logs | `DATA_DIR/logs` + terminal |

A dev gotcha: a bare `uvicorn` run on a machine that also has the desktop app installed will **pick up the desktop's `paths.json`** (because `_default_app_support()` finds it) and therefore share the desktop's `orb.db`, vaults and models unless `ORB_DATA_DIR`/`ORB_MODELS_DIR` (or `ORB_PATHS_FILE` pointing at a scratch file) are exported.

## 3. Reference tables by area

### 3.1 Paths and bootstrap

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `ORB_PATHS_FILE` (`LIVEOS_PATHS_FILE`) | path / `<App Support>/Orb/paths.json` (falls back to `LifeOS`/`LiveOS` dirs that already have one) | env — `paths.paths_json_location()`; also `desktop/main.js`, `supervisor.js loadPaths()` | Location of the bootstrap JSON | supervisor (always injected) |
| `ORB_DATA_DIR` (`LIVEOS_DATA_DIR`, `DATA_DIR`) | path / `paths.json.data_dir` → `<repo>/data` | env — `paths.resolve_data_dir()`; `Settings.DATA_DIR` default is that result | Root for `orb.db`, `kuzu/`, `qdrant/`, `meilisearch/`, `logs/`, `vaults/`, `bin/`, `firefly/`, `runtime_config.json`, `meili_master_key` | wizard → supervisor |
| `ORB_MODELS_DIR` (`LIVEOS_MODELS_DIR`, `MODELS_DIR`) | path / `paths.json.models_dir` → `backend/models` | env — `paths.resolve_models_dir()`; `Settings.MODELS_DIR` | Root for `gguf/`, HF snapshots (`florence-2-large`, …), `models_manifest.json` | wizard → supervisor |
| `MODELS_PATH` | str / `"models"` → **overridden** to `MODELS_DIR` | `Settings` (config.py bottom, `paths.sync_settings_paths`) | Alias only | code |
| `KUZU_DB_PATH` | str / **overridden** to `<DATA_DIR>/kuzu/kuzu_graph` | `Settings`; `kb_registry` default KB, `graph.GraphService` default | Default KB's Kuzu file; per-KB files are `<DATA_DIR>/kuzu/<slug>/kuzu_graph` | code |
| `ORB_DEFAULT_VAULT` (`LIVEOS_DEFAULT_VAULT`) | path / none | env — `paths.resolve_default_vault_path()` **after** `paths.json.default_vault_path` | Default KB vault folder when the file has none; else `<DATA_DIR>/vaults/default` | rarely (dev) |
| `ORB_HF_STAGING`, `ORB_DOWNLOAD_STAGING` (`LIVEOS_HF_STAGING`, `LIVEOS_DOWNLOAD_STAGING`) | path / macOS `~/Library/Caches/Orb/model-downloads`, Windows `%LOCALAPPDATA%/Orb/model-downloads`, Linux `~/.cache/orb/model-downloads` | env — `paths.local_download_staging_dir()` | Local SSD staging dir for GGUF/HF downloads destined for a network `MODELS_DIR` | rarely |
| `ORB_FORCE_DOWNLOAD_STAGING` | any non-empty | env — `local_models.download_file` | Always stage downloads locally even when `MODELS_DIR` is not a network volume | rarely |
| `APPDATA`, `LOCALAPPDATA` | OS | env — `paths._default_app_support`, `local_download_staging_dir` (Windows) | Windows base dirs | OS |
| `PYTHONPATH` | `<backendDir>` | Python | Makes `app.*` importable when cwd ≠ `backend/` | supervisor |

### 3.2 Database

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `DATABASE_BACKEND` | str / `"sqlite"` | `Settings`; `database.py` (import), `api_desktop.setup_status` | `postgres` **and** a pooler URL → asyncpg pool; anything else → SQLite `sqlite+aiosqlite:///<DATA_DIR>/orb.db` with `NullPool`, `check_same_thread=False` | supervisor (`sqlite`), compose (`postgres`) |
| `DATABASE_TRANSACTION_POOLER_URL` | str \| None / `None` | `Settings`; `database.py` | Postgres DSN (`postgresql://` auto-rewritten to `postgresql+asyncpg://`); pool `size=10, max_overflow=20, timeout=30, pre_ping`, `statement_cache_size=0` | compose |
| `DATABASE_SESSION_POOLER_URL`, `DATABASE_DIRECT_CONNECTION_URL` | str \| None / `None` | `Settings` — **unused** | — | compose (ignored) |
| `STORAGE_BACKEND` | not a `Settings` field | nowhere in backend (`extra="ignore"`) | none; legacy (S3 vs local) | supervisor (`local`), compose (`local`) |
| `FILES_URL` | not a field | nowhere | none; legacy | compose |

### 3.3 LLM — chat/retrieval axis

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `LLM_PROVIDER` | str / `"local"` | `Settings`; `llm.LLMService.__init__` (`ollama`/`lm_studio` → `local` + warning), `ai_gate`, `api/settings.py`, `api_desktop.setup_status`, `kb_registry.effective_llm_config` | Primary provider: `local` (in-process GGUF), `openai`, `gemini`, `anthropic`, `huggingface`; other strings → `ValueError("Unsupported LLM provider")` on first use | runtime (`provider`) > supervisor (`process.env.LLM_PROVIDER \|\| "local"`) > .env |
| `CHAT_MODEL` | str \| None / `None` | `Settings`; `llm.get_chat_model` | Provider-agnostic chat model id; wins over the provider-specific keys | runtime (`model`), .env |
| `LLM_MODEL` | str / `"local-chat"` | `Settings`; `llm.get_chat_model` (local + final fallback), `local_models` (label; set to the selected catalog id by `ensure_chat_and_embed_models`), `api/settings.py` | Local model id / placeholder; `"local-chat"` resolves to the manifest selection at runtime | manifest, .env |
| `OPENAI_MODEL` / `GEMINI_MODEL` / `ANTHROPIC_MODEL` / `HUGGINGFACE_MODEL` | str \| None / `None` | `Settings`; `llm.get_chat_model`, `get_ingestion_model`, provider call sites (`_anthropic_*` always use `ANTHROPIC_MODEL`), `multimedia.py` image captions | Per-provider fallback model when `CHAT_MODEL` unset. `HUGGINGFACE_MODEL` is **required** for `huggingface` (`init_clients` raises) | .env |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `HUGGINGFACE_API_KEY` | str \| None / `None` | **Seed only** — read once by `credentials.CredentialStore._seed_from_env_unlocked`. Runtime reads (`llm.py`, `ai_gate`, `multimedia.py`) all go through `credentials.get()` | Cloud credentials for contributors running outside the desktop shell; report `source: "env"`. End users set keys in **Settings → Cloud API keys**, which the shell encrypts to `DATA_DIR/credentials.enc` and pushes to `PUT /api/v1/credentials` (memory-only in the backend) | keychain; .env as fallback |
| `LLM_FALLBACK_PROVIDER` | str \| None / `None` | `Settings`; `llm.LLMService.__init__` (`fallback_provider`, secondary `LLMService(provider)` built on demand) | Provider tried when the primary call fails; explicit-provider instances get none (no recursion) | .env |
| `LLM_BASE_URL` | str / `http://127.0.0.1:8080` | `Settings`; `llm.get_base_url()` (system default endpoint for `openai_compat`), `ai_gate` (cloud heuristics), `api/settings.py`, `runtime_config` | The endpoint used when `LLM_PROVIDER=openai_compat` and no per-KB `llm_base_url` is set. Normalised by `credentials.normalize_base_url`; a malformed value is ignored with a warning rather than crashing | runtime (`base_url`), .env |
| `LLM_API_KEY` | str / `"local"` | `Settings`; `ai_gate` only | Treated as "real" when not `local`/`lm-studio`/`ollama` | .env |
| `LLM_KEEP_ALIVE` | str / `"10m"` | `Settings` — **unused** (Ollama era) | — | — |
| `LLM_RESPONSE_FORMAT` | str / `"text"` | `Settings`; `llm._local_response_format_candidates` | `text` → only `{"type":"text"}`; any other value → try `json_object` then fall back to `text` | .env |
| `BENCHMARK_MODE` | bool / `False` | `Settings`; `llm.py` answer prompt builder | Short-answer benchmark instructions instead of the conversational ones | .env (benchmarks) |
| `CHAT_HISTORY_MAX_MESSAGES` | int / `24` | `Settings`; `chat_store.recent_turns` limit, `llm.py` + `retrieval.py` history slicing | Max prior turns loaded/injected | .env |

### 3.4 LLM — ingestion axis

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `INGESTION_PROVIDER` | str \| None / `None` | `Settings`; `llm._init_ingestion_clients` (per-KB `ingestion_provider` ctor arg overrides it) | Blank → ingestion aliases the chat clients; set → separate clients for `local`/`gemini`/`openai`/`anthropic`/`huggingface` (keys required) | .env |
| `INGESTION_MODEL` | str \| None / `None` | `Settings`; `llm.get_ingestion_model`, `api/settings.py`, `kb_registry._system_model_for` | Provider-agnostic ingestion model; wins over fallbacks | runtime (`ingestion_model`), .env |
| `INGESTION_LLM_MODEL` | str \| None / `"local-chat"` | `Settings`; `llm.get_ingestion_model` (local branch), `kb_registry._system_model_for` (ignored when equal to the placeholder) | Local ingestion model fallback | .env |
| `INGESTION_GEMINI_MODEL` | str \| None / `None` | `Settings`; `llm.get_ingestion_model` (gemini branch) | Gemini ingestion fallback before `GEMINI_MODEL` | .env |
| `ORB_EXTRACTION_CHUNK_TOKENS` (working tree) | int / `4000` ceiling | env — `workflows/extraction_chunking.chunk_token_budget` | Max input tokens per extraction chunk. Effective budget = `max(400, min(ceiling, (ctx − prompt_overhead − 64) / 3.5))`; values below `MIN_SPLIT_TOKENS=400` are raised to 400; non-int ignored | rarely |
| `INGESTION_PIPELINE_CONCURRENCY` | int / `1` | `Settings`; `workflows/ingestion.IngestionWorkflow` (`asyncio.Semaphore`, captured at construction) | Whole-note pipeline parallelism (1 = FIFO) | .env, compose |
| `MULTIMEDIA_CONCURRENCY` | int / `1` | `Settings`; `workflows/agents/ingestion_agent.py` module-level `asyncio.Semaphore` (import time) | Parallel Florence/Whisper/Marlin jobs | .env, compose |
| `INGESTION_AGENT_CONCURRENCY` | int / `2` | `Settings` — **unused** | — | — |

### 3.5 Embeddings axis

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `EMBEDDING_PROVIDER` | str / `"local"` | `Settings`; `embedding.EmbeddingService.__init__` | Accepted: `local`, `auto`, `""` (and deprecated `ollama`/`lm_studio` → local). **Anything else raises** `ValueError` — `openai` in `.env.example` does not work | supervisor (`local`), .env |
| `EMBEDDING_MODEL` | str / `"local-embed"` | `Settings`; `embedding.py` (`is_qwen3` substring check → query instruction), overwritten by `sync_embedding_infrastructure`/`ensure_chat_and_embed_models` | Embedding model id (catalog id from manifest) | manifest > .env |
| `EMBEDDING_DIMENSIONS` | int / `1024` | `Settings`; `qdrant_service` (vector size for collection create), `local_models` (manifest sync) | Must match the GGUF's output; changed dims trigger Qdrant collection recreation in `sync_embedding_infrastructure` | manifest > .env |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` | str / `http://127.0.0.1:8081`, `"local"` | `Settings` — **unused** | — | — |
| `USE_DYNAMIC_EMBEDDING_INSTRUCTION` | bool / `True` | `Settings` — **unused** | — | — |
| `ORB_EMBED_GGUF` | str / `Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf` | env (import-time constant `local_models.EMBED_MODEL_ID`) | Legacy default GGUF id used when the manifest has no selection (`gguf_paths_if_present` fallback) | rarely |
| `ORB_EMBED_N_CTX` (`LIVEOS_EMBED_N_CTX`) | int / `8192` | env — `local_models.LocalLlamaRuntime._load_embed_unlocked` | `n_ctx` for the embed GGUF | supervisor (`8192`) |

### 3.6 Qdrant

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `QDRANT_HOST` | str / `127.0.0.1` | `Settings`; `qdrant_service.QdrantService.__init__` (captured per KB context) | `QdrantClient(host=…)` | supervisor, compose (`qdrant`) |
| `QDRANT_PORT` | int / `6333` | same | HTTP port (desktop `17433`) | supervisor (`PORTS.qdrant`) |
| `QDRANT_API_KEY` | str \| None / `None` | same | Only for Qdrant Cloud | .env |
| `QDRANT_COLLECTION_NODE_CORES` / `QDRANT_COLLECTION_NODE_RELATIONSHIPS` / `QDRANT_COLLECTION_NODE_ISOLATED_CONTEXTS` | str / `node_cores` / `node_relationships` / `node_isolated_contexts` | `Settings`; `qdrant_service` defaults, `kb_registry._ensure_default_row` | Collection names for the **default KB**; other KBs use `<slug>_node_cores` etc. | code |
| `QDRANT__STORAGE__STORAGE_PATH`, `QDRANT__SERVICE__HTTP_PORT` | Qdrant's own env | Qdrant binary | `DATA_DIR/qdrant`, `17433` | supervisor |

### 3.7 Meilisearch (and Typesense aliases)

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `MEILI_HOST` | str / `127.0.0.1` | `Settings`; `meilisearch_service.MeilisearchService.__init__` (`http://{host}:{port}`) | Meili host | supervisor, compose (`meilisearch`) |
| `MEILI_PORT` | int / `7700` | same | Port (desktop `17470`) | supervisor |
| `MEILI_MASTER_KEY` | str / `orb-dev-key` | same | API key. Desktop: `supervisor.resolveMeiliMasterKey` → `process.env.MEILI_MASTER_KEY`, else `DATA_DIR/meili_master_key` (random base64url for fresh installs, `orb-dev-key` when a Meili DB already exists) | supervisor, compose |
| `MEILI_INDEX_NAME` | str / `orb_nodes` | `Settings`; `meilisearch_service` default, `kb_registry` default row | Index for the default KB; others `<slug>_nodes` | code |
| `TYPESENSE_HOST` / `TYPESENSE_PORT` / `TYPESENSE_API_KEY` / `TYPESENSE_COLLECTION_NAME` | as MEILI | `Settings._apply_typesense_aliases` (copied onto the MEILI field **only when the MEILI field still equals its default**); `TYPESENSE_COLLECTION_NAME` also a fallback in `kb_registry` | Backward compatibility for pre-Meili `.env` files | compose (mirrors MEILI), legacy .env |
| `MEILI_ENV`, `MEILI_DB_PATH` | Meili's own env | Meili container/binary | compose only (`development`, `/meili_data`); desktop passes `--db-path DATA_DIR/meilisearch --master-key …` as CLI args | compose / supervisor |

### 3.8 Kuzu

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `KUZU_DB_PATH` | see §3.1 — **overridden** | — | — | code |

Kuzu has no other knobs; per-KB paths, healing of legacy directory paths (`normalize_kuzu_path`) and the schema are in [14](14-graph-storage-kuzu.md).

### 3.9 Local GGUF runtime (`ORB_LLAMA_*`, `ORB_EMBED_*`, `ORB_RERANK_*`, model ids)

All read in `backend/app/services/local_models.py` (and `model_catalog.py` for the light-weight detector) via `_env_first("ORB_X", "LIVEOS_X", default=…)` **at each model load**, so changing them and reloading the model (idle unload or Setup "select model") takes effect without a restart — except the three `*_GGUF` constants.

| Name (alias) | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `ORB_LLAMA_BACKEND` (`LIVEOS_LLAMA_BACKEND`) | `auto` \| `metal` \| `cuda` \| `vulkan` \| `cpu` / `auto` | `local_models.detect_llama_backend`, `model_catalog.detect_accel_backend` (only `ORB_` name) | Forces the llama.cpp accel path. `auto`: macOS → `metal` (`n_gpu_layers=-1`); Linux/Windows with `nvidia-smi` → `cuda` (-1); else `cpu` (0). Forced `cpu` → 0 layers, forced GPU → -1 | rarely |
| `ORB_LLAMA_N_GPU_LAYERS` (`LIVEOS_…`) | int / per backend (-1 GPU, 0 CPU) | same | Overrides layer offload count | rarely |
| `ORB_LLAMA_N_CTX` (`LIVEOS_LLAMA_N_CTX`) | int / `16384` | `_default_chat_n_ctx` → `_chat_kwargs`, `_remaining_output_budget` fallback, `llm.ingestion_context_tokens` | Chat GGUF context window. Raised automatically to `max_tokens + prompt_reserve` when an explicit `ORB_LLAMA_MAX_TOKENS` is set and `n_ctx` is smaller. Comment: 16k + `swa_full` fits ~24 GB Metal; 32k OOMs | supervisor (`16384`) |
| `ORB_LLAMA_MAX_TOKENS` (`LIVEOS_LLAMA_MAX_TOKENS`) | int \| unset / **unset** (working tree; was `10240`) | `_default_chat_max_tokens` → `create_chat_completion`, `_chat_kwargs` | Explicit output cap. **When unset (new default), the runtime sizes `max_tokens` per call as `n_ctx − prompt_tokens − 32` (`_GEN_SAFETY_MARGIN`), raising `PromptTooLongError` if fewer than 256 tokens (`_MIN_OUTPUT_TOKENS`) would remain.** `0`/non-int → treated as unset. Supervisor now injects it **only if the parent env has it** (committed code injected `"10240"`) | supervisor (pass-through only) |
| `ORB_LLAMA_PROMPT_RESERVE` (`LIVEOS_…`) | int / `4096` | `_chat_kwargs` | Minimum context reserved for the prompt when computing `min_ctx = max_tokens + prompt_reserve` | supervisor (`4096`) |
| `ORB_LLAMA_SWA_FULL` (`LIVEOS_…`) | bool-ish / `true` | `_llama_metal_safe_kwargs` | `swa_full=False` only for `0`/`false`/`no`; otherwise `True` (required for stable Gemma 4 output; compact SWA causes "or the" ordinal loops, detected by `_ORDINAL_LOOP_RE` → `RepetitionLoopError`) | supervisor (`true`) |
| `ORB_LLAMA_FLASH_ATTN` (`LIVEOS_…`) | bool-ish / off | `_llama_metal_safe_kwargs` | `flash_attn=True` for `1`/`true`/`yes` | rarely |
| `ORB_LLAMA_REPEAT_PENALTY` (`LIVEOS_…`) | float / `1.12` | `_default_repeat_penalty` → chat completion kwargs | Sampling repeat penalty | supervisor (`1.12`) |
| `ORB_LLAMA_N_THREADS` (`LIVEOS_…`) | int / llama.cpp default | `_chat_kwargs` | CPU threads | rarely |
| `ORB_EMBED_N_CTX` (`LIVEOS_…`) | int / `8192` | `_load_embed_unlocked` | Embed GGUF context | supervisor (`8192`) |
| `ORB_RERANK_N_CTX` (`LIVEOS_…`) | int / `8192` | `LocalGGUFReranker.ensure_loaded` | Reranker GGUF context | supervisor (`8192`) |
| `ORB_MODEL_IDLE_SECONDS` (`LIVEOS_…`) | float / `300` | `model_idle_seconds` → idle watcher threads for chat/embed and reranker | Seconds of inactivity before in-process GGUFs are unloaded; `0` = never unload; negatives clamp to 0 | rarely |
| `ORB_CHAT_GGUF` | HF `repo/file` / `bartowski/google_gemma-4-E4B-it-GGUF/google_gemma-4-E4B-it-Q4_K_M.gguf` | import-time `CHAT_MODEL_ID` | Legacy default chat GGUF (used only when the manifest has no selection) | rarely |
| `ORB_EMBED_GGUF` | / `Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf` | `EMBED_MODEL_ID` | Legacy default embed GGUF | rarely |
| `ORB_RERANK_GGUF` | / `mradermacher/Qwen3-Reranker-0.6B-GGUF/Qwen3-Reranker-0.6B.Q4_K_M.gguf` | `RERANK_MODEL_ID` → `reranker_gguf_path` fallback | Legacy default reranker GGUF | rarely |
| `ORB_RAM_GB` | float / detected (`sysctl hw.memsize` / `wmic` / `/proc/meminfo`, fallback 8) | `model_catalog.total_ram_gb` | Fakes installed RAM for catalog filtering (which chat GGUFs Setup offers) | tests / rarely |
| `MODEL_RERANKER_LOCAL` | str / `qwen3-reranker-0.6b` | `Settings`; `retrieval.py` (labels only), overwritten from manifest | Display name of the reranker in logs/progress | manifest > .env |

The chat/embed/reranker **files actually loaded** come from `MODELS_DIR/models_manifest.json` (`selection.chat_path`, `embed_path`, `reranker_path`, `embedding_dims`, ids), written by Setup — not from env. See [12](12-local-models-and-inference.md).

### 3.10 Multimodal models (Florence / Whisper / Marlin)

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `MODEL_FLORENCE_HF` / `MODEL_FLORENCE_LOCAL` | str / `microsoft/Florence-2-large` / `florence-2-large` | `Settings`; `multimodal_models.model_ids("florence")` | HF repo to download; folder name under `MODELS_DIR` (supervisor also checks `MODELS_DIR/florence-2-large` exists before pre-warming) | code |
| `MODEL_WHISPER_HF` / `MODEL_WHISPER_LOCAL` | `openai/whisper-large-v3-turbo` / `whisper-large-v3-turbo` | same | Audio transcription model | code |
| `MODEL_MARLIN_HF` / `MODEL_MARLIN_LOCAL` | `lunahr/Marlin-2B-ungated` / `marlin-2b` | same | Video understanding model (Qwen3.5-based) | code |
| `FLORENCE_MAX_IMAGE_PIXELS` | int / `1500000` | `Settings`; `multimodal_runtime` via `getattr(..., 0) or 1_500_000` | Downscale images above this many pixels before Florence. **`0` is not "unlimited"** — it falls back to 1.5 MP | .env |
| `FORCE_QWENVL_VIDEO_READER` | str / `pyav` | `multimodal_runtime` `os.environ.setdefault` (consumed by `qwen-vl-utils`) | Video decoder backend | code (`setdefault` — env wins if pre-set) |
| `VIDEO_MAX_PIXELS` | int / `200704` | same `setdefault` (qwen-vl-utils) | Per-frame pixel budget for Marlin | code / env |
| `FPS` / `FPS_MAX_FRAMES` / `FPS_MIN_FRAMES` | `2.0` / `240` / `4` | same | Frame sampling for Marlin | code / env |
| `PATH` | OS | `multimodal_runtime` (adds `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `~/bin` when searching for `ffmpeg`/`ffprobe`; prepends the found bin dir for pydub); `supervisor.withNativeToolPath` | Finding ffmpeg from a GUI-launched app | OS / supervisor |

### 3.11 PDF knobs (`services/multimedia.py`)

| Name | Type / default | Effect |
|---|---|---|
| `PDF_VISUAL_EXTRACTION_ENABLED` | bool / `True` | Allow Florence rendering of scanned/sparse pages |
| `PDF_VISUAL_EXTRACTION_MAX_PAGES` | int / `0` | Cap of visually processed pages per PDF; `0` = all qualifying pages |
| `PDF_VISUAL_RENDER_DPI` | int / `144` | Render DPI (`max(value, 72)`; zoom = dpi/72) |
| `PDF_VISUAL_TEXT_THRESHOLD` | int / `80` | Pages whose native text length ≥ threshold skip the visual pass |

### 3.12 Retrieval tuning (`services/retrieval.py`)

| Name | Type / default | Effect |
|---|---|---|
| `VECTOR_SIMILARITY_THRESHOLD` | float / `0.50` | Qdrant min score when `RERANKER_ENABLED=false` |
| `VECTOR_PRE_RERANK_THRESHOLD` | float / `0.45` | Qdrant min score when the reranker is on |
| `RERANKER_ENABLED` | bool / `True` | GGUF cross-encoder vs keyword-overlap heuristic; also gates neighbour reranking |
| `RERANKER_TOP_K` | int / `10` | Candidates kept after rerank (both passes) |
| `RERANKER_SCORE_THRESHOLD` | float / `0.05` | Drop reranked candidates below |
| `GRAPH_EXPAND_TOP_NEIGHBORS` | int / `10` | Relationship entries kept per graph expansion |
| `GRAPH_EXPAND_SCORE_THRESHOLD` | float / `0` | Neighbour score filter (only when > 0) |
| `MAX_LOOP_ITERATIONS` | int / `3` | Multi-hop loop iterations |
| `MAX_POTENTIAL_QUESTIONS` | int / `10` | **unused** |
| `COMMUNITY_RECOMPUTE_BATCH_SIZE` | int / `100` | **unused** |

### 3.13 Feature switches

| Name | Type / default | Read in | Effect |
|---|---|---|---|
| `COMMUNITY_DETECTION_ENABLED` | bool / **`False`** (`.env.example` says `true`) | `workflows/ingestion.py`, `services/ingestion_tracker.py` | Post-ingest community detection and the idle-timer auto-recompute; `POST /admin/rebuild-communities` still works manually |
| `TEMPORAL_DIGESTS_ENABLED` | bool / **`False`** | `workflows/ingestion.py` | Debounced digest rebuild after ingest; `build_temporal_digests` no-ops when false |
| `TEMPORAL_DIGEST_PERIOD` | str / `"month"` (`week`/`year` accepted) | `workflows/ingestion.py`, `api/admin.py` | Default granularity |
| `AI_SETUP_MODE` | str / `"none"` (`local` \| `cloud` \| `hybrid` \| `none`/`skip`) | `Settings`; `runtime_config`, desktop shell | **Gates nothing.** `ai_gate` derives readiness from real configuration and `api_desktop` reports `derived_setup_mode()`; the key persists only for the shell wizard. (§14 of [06](06-backend-core-and-configuration.md)); `local`/`none` also disables cloud image captions | runtime > supervisor (from `paths.json.ai_setup_mode` via `desktop/main.js`, default `none`) > .env |

### 3.14 Firefly III

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `FIREFLY_BASE_URL` | str \| None / `None` | `Settings`; `firefly_service.FireflyService.__init__` | Firefly API base (`http://127.0.0.1:17412`); unset → finance endpoints error | supervisor |
| `FIREFLY_RUNTIME_FILE` | str \| None / `None` | same | `DATA_DIR/firefly/runtime.json` (`apiToken`, php/app paths) written by `desktop/firefly-runtime.js` | supervisor |
| `FIREFLY_API_TOKEN` | str \| None / `None` | `firefly_service._token` | Overrides the token from `runtime.json` | .env (rare) |
| `ORB_FIREFLY_VERSION`, `ORB_PHP_BIN_VERSION` | `v6.6.6`, `1.2.0` | `desktop/firefly-runtime.js` | Which Firefly/PHP bundle the shell downloads | rarely |
| `APP_URL`, `HOME`, `USERPROFILE` | — | Firefly PHP child | Set by supervisor for `artisan serve` | supervisor |

### 3.15 CORS, logging, API identity

| Name | Type / default | Read in | Effect | Set by |
|---|---|---|---|---|
| `CORS_ORIGINS` | CSV str / `http://localhost:3700,http://localhost:3701,http://127.0.0.1:3700,http://127.0.0.1:3701` | `main.py` | `allow_origins` list | supervisor (`process.env.CORS_ORIGINS \|\| "http://127.0.0.1:17400,http://localhost:17400"`), .env |
| `CORS_ALLOW_ORIGIN_REGEX` | str \| None / `None` | `main.py` | `allow_origin_regex` | .env |
| `LOG_LEVEL` | str / `INFO` | `Settings`; `log.setup_logging` (`getattr(logging, X.upper(), INFO)` — invalid names silently become INFO) | Root + component logger level; `errors.log` always ERROR | .env |
| `PROJECT_NAME`, `API_V1_STR` | `Orb`, `/api/v1` | **unused** | — | — |

### 3.16 Desktop shell: ports and process env (`desktop/ports.js`, `paths.js`, `main.js`)

| Name (alias) | Default | Read in | Effect |
|---|---|---|---|
| `ORB_UI_PORT` (`LIVEOS_UI_PORT`) | `17400` | `ports.js` | Next.js UI port; also CORS origin |
| `ORB_API_PORT` (`LIVEOS_API_PORT`) | `17401` | `ports.js` | uvicorn port; `NEXT_PUBLIC_API_URL`/proxy targets derive from it |
| `ORB_FIREFLY_PORT` (`LIVEOS_FIREFLY_PORT`) | `17412` | `ports.js` | Firefly `artisan serve` port and `FIREFLY_BASE_URL` |
| `ORB_QDRANT_PORT` (`LIVEOS_QDRANT_PORT`) | `17433` | `ports.js` | Qdrant HTTP port → `QDRANT_PORT` |
| `ORB_MEILI_PORT` (`LIVEOS_MEILI_PORT`) | `17470` | `ports.js` | Meili port → `MEILI_PORT` |
| `ORB_URL` (`LIVEOS_URL`) | `http://127.0.0.1:17400` | `main.js` | Window URL override |
| `ORB_PACKAGED`, `ORB_RESOURCES`, `ORB_ROOT`, `ORB_PYTHON`, `ORB_NODE`, `ORB_FRONTEND_DEV`, `ORB_SKIP_WIZARD`, `ORB_ENABLE_UPDATER` (+ `LIVEOS_` aliases) | see [04](04-desktop-shell.md) §13 | `paths.js`, `main.js` | Layout/binary overrides, wizard skip, auto-updater |
| `ORB_QDRANT_VERSION`, `ORB_MEILI_VERSION` | `v1.18.2`, `v1.49.0` | `download-binaries.js` | Binary versions fetched into `DATA_DIR/bin` |
| `AI_SETUP_MODE`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `CORS_ORIGINS`, `MEILI_MASTER_KEY` | — | `supervisor.js`/`main.js` `process.env.X \|\| default` | If exported in the shell's own environment they pass through to the backend; otherwise defaults `none`/`local`/`local`/ports-derived/per-install key |

Complete backend env block injected by `supervisor.startBackend()` (working tree): `ORB_DATA_DIR`, `ORB_MODELS_DIR`, `ORB_PATHS_FILE`, `PYTHONPATH`, `DATABASE_BACKEND=sqlite`, `STORAGE_BACKEND=local`, `QDRANT_HOST=127.0.0.1`, `QDRANT_PORT`, `MEILI_HOST=127.0.0.1`, `MEILI_PORT`, `MEILI_MASTER_KEY`, `FIREFLY_BASE_URL`, `FIREFLY_RUNTIME_FILE`, `AI_SETUP_MODE`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `CORS_ORIGINS`, `ORB_LLAMA_N_CTX=16384`, `ORB_LLAMA_MAX_TOKENS` (only if present in the parent env), `ORB_LLAMA_SWA_FULL=true`, `ORB_LLAMA_REPEAT_PENALTY=1.12`, `ORB_LLAMA_PROMPT_RESERVE=4096`, `ORB_EMBED_N_CTX=8192`, `ORB_RERANK_N_CTX=8192`, merged over `process.env` with `withNativeToolPath` (PATH augmented). The multimodal pre-warm child gets only `ORB_MODELS_DIR` + `PYTHONPATH` over `process.env`.

### 3.17 Frontend env (`frontend/`)

| Name | Default | Read in | Effect |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | `/api/v1` | `src/lib/api.ts` (build/runtime public) | API base; desktop production uses `/api/v1` (same-origin rewrite), `next dev` uses `http://127.0.0.1:17401/api/v1` |
| `API_PROXY_TARGET` | dev `http://localhost:8700`, prod `http://backend:8000` | `next.config.ts` rewrites | Where `/api/v1/*` is proxied; supervisor sets `http://127.0.0.1:17401` |
| `FILES_PROXY_TARGET` | dev `http://localhost:9000`, prod `http://rustfs:9000` (legacy) | `next.config.ts` | Where `/vault-files/*` is proxied; supervisor sets the API URL |
| `NEXT_PUBLIC_FILES_URL` | `/files/orb-assets` | compose only | legacy |
| `PORT`, `HOSTNAME`, `NODE_ENV` | `17400`, `127.0.0.1`, `production` | `run-server.js` | standalone server bind |

## 4. JSON files

### 4.1 `paths.json`

Location: `ORB_PATHS_FILE` or `<App Support>/Orb/paths.json` (macOS `~/Library/Application Support/Orb/`, Windows `%APPDATA%\Orb\`, Linux `~/.config/Orb/`; `LifeOS`/`LiveOS` folders are used instead if they already contain one).

```json
{
  "data_dir": "/abs/path/Orb/data",
  "models_dir": "/abs/path/Orb/models",
  "default_vault_path": "/abs/path/Vault",
  "ai_setup_mode": "local"
}
```

| Key | Required | Writer | Reader |
|---|---|---|---|
| `data_dir` | yes | wizard (`desktop/main.js save-wizard`), `POST /api/v1/setup/paths` (`paths.save_paths_file`) | `paths.resolve_data_dir` (after env), `supervisor.loadPaths` |
| `models_dir` | yes | same | `paths.resolve_models_dir`, `supervisor.loadPaths` |
| `default_vault_path` | no (preserved if omitted on save) | same | `paths.resolve_default_vault_path` (**before** env), `supervisor.loadPaths` |
| `ai_setup_mode` | no (preserved) | same | `desktop/main.js` → exported as `AI_SETUP_MODE` env; backend never reads the key directly |

All paths are stored absolute (`expanduser().resolve()`). The backend caches the parsed file for the process lifetime (`_PATHS_CACHE`); `save_paths_file` refreshes it, `clear_paths_cache()` drops it. Deleting the file triggers the wizard again on next desktop launch.

### 4.2 `DATA_DIR/runtime_config.json`

```json
{
  "provider": "gemini",
  "model": "gemini-2.5-pro",
  "ingestion_model": "gemini-2.0-flash",
  "base_url": "http://127.0.0.1:8080",
  "ai_setup_mode": "cloud"
}
```

| Key | → `settings` field | Written by |
|---|---|---|
| `provider` | `LLM_PROVIDER` | `PATCH /api/v1/settings` |
| `model` | `CHAT_MODEL` | `PATCH /api/v1/settings` |
| `ingestion_model` | `INGESTION_MODEL` | `PATCH /api/v1/settings` |
| `base_url` | `LLM_BASE_URL` | `PATCH /api/v1/settings` |
| `ai_setup_mode` | `AI_SETUP_MODE` | `POST /api/v1/setup/paths` (Setup wizard) |

Only these five keys survive `load()`/`save()` (`MUTABLE_KEYS`); unknown keys are dropped on the next save. Applied at startup after `init_db`. No API keys, ever. A fallback location `<repo>/data/runtime_config.json` is used only if `paths` cannot be imported.

### 4.3 `MODELS_DIR/models_manifest.json` (pointer)

Not a config file you edit, but it is the third override source: `selection.{chat_id, chat_path, embed_id, embed_path, embedding_dims, reranker_id, reranker_path}` drive `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `MODEL_RERANKER_LOCAL`, `LLM_MODEL` and which GGUF files load. Documented in [12](12-local-models-and-inference.md).

### 4.4 Per-KB LLM override (working tree)

Stored in `knowledge_bases.llm_provider / llm_model / llm_ingestion_model` (SQLite), edited via `PATCH /api/v1/kb/{kb_id}/llm` with body `{"provider"?, "model"?, "ingestion_model"?}` where `""`, `"inherit"`, `"system"`, `"default"` or `null` mean "inherit". Validation: provider ∈ `kb_registry.LLM_PROVIDERS = ("local","openai","gemini","anthropic","huggingface")`; cloud providers need their API key (`ai_gate.provider_is_configured`); local model ids must be known catalog chat models that are already downloaded (`model_catalog.get_option`, `chat_model_downloaded`). The service is constructed immediately to surface bad configs; on failure the override is cleared and 400 returned. Resolution (`effective_llm_config`): `provider = row.llm_provider or LLM_PROVIDER`; `model = row.llm_model or _system_model_for(provider)`; `ingestion_model = row.llm_ingestion_model or row.llm_model or _system_model_for(provider, ingestion=True) or model`.

## 5. The three provider axes and model-key fallback chains

`.env.example` and `LLMService` define three independent axes:

| Axis | Provider key | Model key (generic) | Provider-specific fallbacks |
|---|---|---|---|
| Chat / retrieval | `LLM_PROVIDER` | `CHAT_MODEL` | `LLM_MODEL` (local), `OPENAI_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`, `HUGGINGFACE_MODEL` |
| Ingestion | `INGESTION_PROVIDER` (blank → same as chat) | `INGESTION_MODEL` | `INGESTION_LLM_MODEL` → `LLM_MODEL` (local), `INGESTION_GEMINI_MODEL` → `GEMINI_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `HUGGINGFACE_MODEL` |
| Embeddings | `EMBEDDING_PROVIDER` (`local` only) | `EMBEDDING_MODEL` + `EMBEDDING_DIMENSIONS` | manifest selection |

Reranker and multimodal models have no provider axis: always in-process GGUF / HF snapshots.

### 5.1 `LLMService.get_chat_model()` — exactly as implemented

```
1. self._chat_model_override            (per-KB pinned model; working tree)
2. settings.CHAT_MODEL                  (if truthy)
3. if provider == "local": settings.LLM_MODEL
4. {"openai": OPENAI_MODEL, "gemini": GEMINI_MODEL,
    "anthropic": ANTHROPIC_MODEL, "huggingface": HUGGINGFACE_MODEL}[provider]
   — .get() with default settings.LLM_MODEL for unknown providers;
   NOTE a provider whose *_MODEL is None returns None here (no further fallback)
```

### 5.2 `LLMService.get_ingestion_model()`

```
1. self._ingestion_model_override or self._chat_model_override   (per-KB; working tree)
2. settings.INGESTION_MODEL
3. p = self.ingestion_provider (falls back to self.provider)
   local / ollama / lm_studio : INGESTION_LLM_MODEL or LLM_MODEL or None
   gemini                     : INGESTION_GEMINI_MODEL or GEMINI_MODEL or None
   openai                     : OPENAI_MODEL or None
   anthropic                  : ANTHROPIC_MODEL or None
   huggingface                : HUGGINGFACE_MODEL or None
   other                      : None
```

Caveats visible in the call sites: the Anthropic branches of `generate`/`ingestion_generate` pass `model=settings.ANTHROPIC_MODEL` directly (ignoring `CHAT_MODEL`/`INGESTION_MODEL`); the Gemini ingestion branch uses `model or settings.GEMINI_MODEL`; `init_clients` log lines print the provider-specific key even when `CHAT_MODEL` is what will be used.

### 5.3 Provider selection

```
chat provider      = (ctor provider | settings.LLM_PROVIDER).lower(); ollama/lm_studio → local
ingestion provider = (ctor ingestion_provider | settings.INGESTION_PROVIDER | "").lower()
                     ollama/lm_studio → local; "" → chat provider (clients aliased)
embedding provider = settings.EMBEDDING_PROVIDER: local|auto|"" → local; else ValueError
fallback provider  = settings.LLM_FALLBACK_PROVIDER (only for the global service)
```

Per-KB services are built as `LLMService(prov, chat_model=…, ingestion_model=…, ingestion_provider=prov)` so a pinned KB never mixes providers between chat and ingestion.

### 5.4 `kb_registry._system_model_for(provider, ingestion)` (what the UI shows as "inherited")

```
is_global = provider == settings.LLM_PROVIDER
if ingestion and is_global and INGESTION_MODEL → INGESTION_MODEL
if is_global and CHAT_MODEL                    → CHAT_MODEL
if provider == "local" and ingestion and INGESTION_LLM_MODEL not in (None, "local-chat") → INGESTION_LLM_MODEL
else {"local": LLM_MODEL, "openai": OPENAI_MODEL, "gemini": GEMINI_MODEL,
      "anthropic": ANTHROPIC_MODEL, "huggingface": HUGGINGFACE_MODEL}[provider]
```

## 6. Declared-but-unused and documented-but-ineffective keys

| Key | Status |
|---|---|
| `PROJECT_NAME`, `API_V1_STR` | never read |
| `LLM_KEEP_ALIVE` | never read (`_with_keep_alive` returns the body unchanged) |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `USE_DYNAMIC_EMBEDDING_INSTRUCTION` | never read |
| `INGESTION_AGENT_CONCURRENCY`, `COMMUNITY_RECOMPUTE_BATCH_SIZE`, `MAX_POTENTIAL_QUESTIONS` | never read |
| `DATABASE_SESSION_POOLER_URL`, `DATABASE_DIRECT_CONNECTION_URL` | never read |
| `KUZU_DB_PATH`, `MODELS_PATH` | read but overwritten by code |
| `LLM_BASE_URL`, `LLM_API_KEY` | only affect `ai_is_configured()`; no HTTP client uses them |
| `STORAGE_BACKEND`, `FILES_URL`, `NEXT_PUBLIC_FILES_URL` | not `Settings` fields; ignored by the backend |
| `EMBEDDING_PROVIDER=openai` (from `.env.example` Option C) | raises `ValueError` |
| `FLORENCE_MAX_IMAGE_PIXELS=0` | not "full resolution"; falls back to 1.5 MP |
| `COMMUNITY_DETECTION_ENABLED` / `TEMPORAL_DIGESTS_ENABLED` | `.env.example` implies default `true`; code default `False` |
| `ORB_LLAMA_MAX_TOKENS=10240` comment in `.env.example` | committed supervisor injected it; working tree no longer does (per-call sizing) |

## 7. Adding a knob (checklist)

1. Decide the layer: import-time constant (needs restart), `Settings` field (env/.env), runtime-mutable (`runtime_config.MUTABLE_KEYS` + `apply_to_settings` + `api/settings.LLMSettings`), per-KB (column in `knowledge_bases` + `kb_registry` DDL/`_ensure_optional_columns` + `KBContext`), or `ORB_*` env read at call time (`_env_first("ORB_X", "LIVEOS_X", default=...)`).
2. Add the field/env read, a line in `backend/.env.example`, and — for desktop-relevant values — the injection in `desktop/supervisor.js startBackend()`.
3. Read it where used via `settings.X` at call time; if it must be captured at import, note the restart requirement in the docstring.
4. Update this file and [06](06-backend-core-and-configuration.md) §5.3.
