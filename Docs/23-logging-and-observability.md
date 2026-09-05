# Logging and observability

**What this covers.** How the backend logs (`backend/app/core/log.py`: handlers, per-component file routing, rotation, formats, level handling, wizard-aware directory, third-party suppression), every logger name used in the backend and the file it lands in, request trace ids, the desktop supervisor's per-process log files, the status surfaces the UI polls (setup status, maintenance status, note ingestion status, multimodal status), and a symptom → log file → grep playbook.

**Related docs:** [Backend core and configuration](06-backend-core-and-configuration.md) · [Desktop shell](04-desktop-shell.md) · [API reference](07-api-reference.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Local models and inference](12-local-models-and-inference.md) · [Retrieval and chat](16-retrieval-and-chat.md) · [Finance / Firefly](17-finance-firefly.md) · [Frontend architecture](18-frontend-architecture.md) · [Configuration reference](21-configuration-reference.md) · [Data directory layout](22-data-directory-layout.md) · [Development guide](27-development-guide.md)

## 1. Responsibilities and boundaries

`core/log.py` owns: choosing the logs directory (`DATA_DIR/logs`), the two formatters, the root console handler, the shared `errors.log` handler, one rotating file per component group, and quieting noisy third-party loggers. It does **not** own: structured/JSON logging (none exists), trace-id injection into records (not wired), metrics/tracing exporters (none), the supervisor's `backend.log`/`frontend.log`/`firefly.log` (raw stdio captured by Electron, see §5), or Qdrant/Meili/Firefly internal logs.

## 2. Files

| Path | Purpose |
|---|---|
| `backend/app/core/log.py` | `setup_logging`, `reconfigure_logging`, `get_logger`, `resolve_logs_dir`, `COMPONENT_LOG_FILES`, handler factories |
| `backend/app/main.py` | calls `setup_logging()` before any other import; trace-id middleware |
| `backend/app/api_desktop.py` (`setup_paths`) | calls `reconfigure_logging()` after the wizard changes `DATA_DIR` |
| `desktop/supervisor.js` (`serviceLogPath`, `_spawn`, `waitHttp`, `tailLog`) | per-child stdio log files under `DATA_DIR/logs`, tail-on-failure |
| `backend/app/api/health.py`, `api_desktop.py` (`setup_status`, `multimodal_status`), `api/admin.py` (`maintenance-status`), `api/notes.py` (`/notes/{id}/status`) | status endpoints polled by the UI |
| `frontend/src/components/system-status-indicator.tsx`, `ai-limited-banner.tsx`, `src/app/settings/page.tsx`, `src/app/setup/page.tsx` | UI consumers of those endpoints |

## 3. `backend/app/core/log.py` in full

### 3.1 Formatters

| Name | Format | Used by |
|---|---|---|
| `file_formatter` | `%(asctime)s \| %(name)s \| %(levelname)s \| %(message)s`, `datefmt=%Y-%m-%d %H:%M:%S` | every `RotatingFileHandler` |
| `console_formatter` | `%(levelname)s: %(message)s` | every `StreamHandler(sys.stdout)` |

Example file line: `2026-09-02 10:14:03 | IngestionPipeline | INFO | [Extraction] chunk 2/3 (~3800 tokens)`. No trace id, no module/line, no process/thread id. `extra={...}` dicts passed by callers (e.g. `main.startup_event`, `api/settings.py`) are **not rendered** by either formatter — they are attached to the `LogRecord` but invisible in output.

### 3.2 Logs directory

`resolve_logs_dir()` → `Path(settings.DATA_DIR).expanduser() / "logs"`, created with `mkdir(parents=True, exist_ok=True)` on every call. It reads the **live** `settings.DATA_DIR`, so after `paths.sync_settings_paths()` it returns the new location. `LOGS_DIR = resolve_logs_dir()` is a module constant snapshot kept for back-compat; `setup_logging`/`reconfigure_logging` refresh it (`global LOGS_DIR`). Nothing else in the backend reads `LOGS_DIR` — prefer the function.

Desktop: `DATA_DIR/logs` is the same directory the supervisor uses for `backend.log`, `frontend.log`, `firefly.log`, so one folder holds both the Python component logs and the raw process stdio.

### 3.3 Handler factories

| Factory | Handler | Level | Notes |
|---|---|---|---|
| `get_file_handler(filename, level)` | `RotatingFileHandler(resolve_logs_dir()/filename, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")` | as given | 10 MB per file, 5 backups → at most ~60 MB per component file (`x.log`, `x.log.1` … `x.log.5`). Rotation is per handler instance; two handlers on the same file (never happens with the current table) would rotate independently. |
| `get_console_handler(level)` | `StreamHandler(sys.stdout)` | as given | stdout, not stderr — under the desktop this is what ends up in `backend.log`. |

### 3.4 `COMPONENT_LOG_FILES` routing table

| Logger name | File | Emitting modules |
|---|---|---|
| `API` | `api.log` | `main.py`, `api/health.py`, `api/chat.py`, `api/kb.py`, `api/files.py`, `api/graph.py`, `api/notes.py`, `api/admin.py`, `api/settings.py` |
| `KBRegistry` | `api.log` | `services/kb_registry.py` |
| `RuntimeConfig` | `api.log` | `core/runtime_config.py` |
| `ResetIndex` | `api.log` | *(no current emitter — name reserved)* |
| `InitDB` | `api.log` | *(no current emitter; `database.py` uses `DatabaseService`)* |
| `uvicorn.access` | `api.log` | uvicorn request lines (`"GET /api/v1/notes?kb=default HTTP/1.1" 200`) |
| `uvicorn.error` | `api.log` | uvicorn lifecycle + unhandled exception tracebacks |
| `IngestionPipeline` | `ingestion.log` | `workflows/ingestion.py`, `workflows/agents/ingestion_agent.py` |
| `IngestionTracker` | `ingestion.log` | `services/ingestion_tracker.py` |
| `VaultSync` | `ingestion.log` | `services/vault_sync.py` |
| `VaultWatcher` | `ingestion.log` | `services/vault_watcher.py` |
| `MultimediaService` | `multimedia.log` | `services/multimedia.py` |
| `MultimodalRuntime` | `multimedia.log` | `services/multimodal_runtime.py` |
| `MultimodalModels` | `multimedia.log` | `services/multimodal_models.py` |
| `MultimodalServices` | `multimedia.log` | `services/multimodal_services.py` |
| `ChatWorkflow` | `chat.log` | `workflows/chat.py` |
| `ChatStore` | `chat.log` | `services/chat_store.py` |
| `DatabaseService` | `database.log` | `core/database.py` |
| `GraphService` | `graph.log` | `services/graph.py` |
| `LLMService` | `llm.log` | `services/llm.py` |
| `LocalModels` | `llm.log` | `services/local_models.py` (incl. `[ModelLoad]` clock lines) |
| `InferenceDevice` | `llm.log` | `core/inference_device.py` |
| `RetrievalService` | `retrieval.log` | `services/retrieval.py` |
| `QdrantService` | `retrieval.log` | `services/qdrant_service.py` |
| `MeilisearchService` | `retrieval.log` | `services/meilisearch_service.py` |
| `RerankerService` | `retrieval.log` | `services/reranker.py` |
| `FireflyService` | `finance.log` | `services/firefly_service.py` |

Plus, always: `errors.log` (ERROR and above from **every** logger, root and component) and stdout.

**Logger names used in the backend that are NOT in the table:**

| Logger | Module | Where it lands |
|---|---|---|
| `EmbeddingService` | `services/embedding.py` | Not configured → propagates to the **root** logger → stdout console (`backend.log` under the desktop) and, at ERROR+, `errors.log`. **No dedicated file** — embedding warnings are absent from `llm.log`/`retrieval.log`. |
| root (`logging.info(...)` in `setup_logging`) | `core/log.py` | stdout + `errors.log` |
| any third-party logger (`httpx`, `sqlalchemy`, `llama_cpp`, `transformers`, `watchdog`, `meilisearch`, `qdrant_client`) | libraries | root → stdout / `errors.log` |

`workflows/extraction_chunking.py` (working tree) defines no logger; chunking lines are emitted by `IngestionPipeline`.

### 3.5 `setup_logging()` — exact algorithm

Called once at the top of `main.py` (before importing routers/services) and again by `reconfigure_logging()`.

1. `log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)` — unknown names (`TRACE`, `VERBOSE`, typos) silently become INFO.
2. `LOGS_DIR = resolve_logs_dir()`.
3. Root logger: `setLevel(log_level)`, **`handlers.clear()`** (this discards uvicorn's default handlers because `main.py` is imported after uvicorn configured logging), add console handler.
4. `error_handler = get_file_handler("errors.log", logging.ERROR)` added to root — one shared handler instance.
5. For each `(name, filename)` in `COMPONENT_LOG_FILES`: get the logger, `setLevel(log_level)`, `_strip_rotating_handlers` (removes + closes old `RotatingFileHandler`s so reconfiguration does not leak file descriptors), remove any existing plain `StreamHandler`s (dedupe on reconfigure; note `RotatingFileHandler` is a `StreamHandler` subclass, hence the `not isinstance(..., RotatingFileHandler)` guard), then add: rotating file handler for `filename`, a console handler, and the shared `error_handler`; set **`propagate = False`**.
6. `httpx`, `httpcore`, `asyncio`, `urllib3`, `multipart` → `WARNING` (suppresses per-request DEBUG/INFO chatter). Not suppressed: `sqlalchemy` (only logs at echo), `llama_cpp` (prints via stderr, `verbose=False` is passed), `transformers` (only `transformers.tokenization_utils_base` is set to ERROR, by `services/retrieval.py`).
7. `_configured = True`; root `logging.info("Logging initialized at level %s | Logs dir: %s", ...)`.

Consequences of step 5:

- Component loggers **do not propagate**, so a component record appears exactly once on stdout (via the component's own console handler) and once in its file, plus `errors.log` when ≥ ERROR. Root-attached handlers never see component records.
- Because `uvicorn.access`/`uvicorn.error` are in the table, uvicorn's request lines go to `api.log` and stdout in Orb's format, not uvicorn's coloured default. Uvicorn's `--log-level` flag is not passed by the supervisor; `LOG_LEVEL` governs.
- Multiple `RotatingFileHandler` instances share one `error_handler` object; all component loggers plus root append to the same `errors.log` file through that single handler (safe).
- Level filtering happens at the logger (`setLevel(log_level)`) and again at each handler (same level), except `errors.log` (ERROR). Setting `LOG_LEVEL=DEBUG` therefore enables DEBUG in files *and* on stdout.

### 3.6 `reconfigure_logging()`

`LOGS_DIR = resolve_logs_dir(); if _configured: setup_logging()`. Called by `POST /api/v1/setup/paths` right after `sync_settings_paths(settings)` so that, when the user picks a new `DATA_DIR` in the wizard, subsequent log lines go to `<new DATA_DIR>/logs` without a restart. Old files are closed by `_strip_rotating_handlers`. (The supervisor's `backend.log` fd, opened by Electron, keeps pointing at the old directory until the backend is restarted — which the shell does after the wizard.)

### 3.7 `get_logger(name)`

Plain `logging.getLogger(name)`. Passing a name that is not in `COMPONENT_LOG_FILES` yields an unconfigured logger that propagates to root (stdout + `errors.log` only) — see `EmbeddingService` above. Always pick an existing key, or add a new key to the table.

### 3.8 `LOG_LEVEL` semantics

| Value | Effect |
|---|---|
| `DEBUG` | everything, incl. `"Health check hit"` on `GET /`, Qdrant/Meili per-query details, ingestion tracker idle-timer decisions |
| `INFO` (default) | pipeline stages, model loads/unloads (`[ModelLoad] chat loaded in 12.3s`), retrieval loop iterations, startup banners |
| `WARNING` | degraded paths (embedding sync skipped, watcher not started, deprecated provider names, index disabled) |
| `ERROR` / `CRITICAL` | failures; these also always land in `errors.log` regardless of level |

## 4. Trace ids

`main.trace_id_middleware` sets the `request_trace_id` ContextVar from the inbound `X-Request-Id` header (or a fresh UUID4) and echoes it in the `X-Request-Id` response header. **No formatter or filter injects it into log lines**, and no service reads the ContextVar. To correlate today: take the id from the response header and grep `api.log` for the `uvicorn.access` line by time/path; for chat, use the body-level `request_id` (`ChatInput.request_id`), which *is* logged by the chat workflow and used for progress polling. Wiring the ContextVar into `file_formatter` via a `logging.Filter` that sets `record.trace_id` is the intended extension point (`main.py` docstring).

## 5. Desktop supervisor log files (`desktop/supervisor.js`)

| File | Source | Mechanism |
|---|---|---|
| `DATA_DIR/logs/backend.log` | uvicorn process stdout+stderr: Orb console-format lines (all components, `LEVEL: message`), `llama.cpp` / Metal init prints, Python tracebacks that escape logging, `pip` output from on-demand multimodal installs | `serviceLogPath(dataDir, "backend")`; `_spawn` opens the file in append mode (`openSync(..., "a")`) and passes the fd as both stdout and stderr (`stdio: ["ignore", fd, fd]`) |
| `DATA_DIR/logs/frontend.log` | Next.js server (`run-server.js` or `npm run dev`) | same |
| `DATA_DIR/logs/firefly.log` | `php artisan serve` output | same; Laravel's own logs are under `DATA_DIR/firefly/app/storage/logs/` |
| *(none)* | **Qdrant and Meilisearch** | spawned with default `stdio: "ignore"` — their output is discarded. Qdrant's own storage is `DATA_DIR/qdrant`, Meili's `DATA_DIR/meilisearch`; neither writes a log file by default. To debug them, run the binary from `DATA_DIR/bin` manually with the same args (`Qdrant`: env `QDRANT__STORAGE__STORAGE_PATH`, `QDRANT__SERVICE__HTTP_PORT`; `Meilisearch`: `--db-path … --http-addr 127.0.0.1:17470 --master-key …`). |
| splash / dialog text | `tailLog(logPath, 24)` | `waitHttp(url, timeout, child, logFile)` appends the last 24 lines of the child's log to the "Process exited before … was ready" error shown in the boot-failure dialog |

These files are **never rotated** by the shell; they grow until deleted. The Python component files in the same directory rotate at 10 MB × 5. Boot-failure dialog text hard-codes `~/Library/Application Support/Orb/data/logs/` regardless of platform/`paths.json` ([04](04-desktop-shell.md) §14).

Under Docker there is no `backend.log`; stdout goes to `docker logs orb-backend`, and the component files are under the `/data/logs` volume (`./data/logs` on the host).

## 6. Status surfaces (what the UI polls)

| Endpoint | Consumer | Payload (keys) | Notes |
|---|---|---|---|
| `GET /health` | `desktop/supervisor.js waitHttp` (boot gate, 120 s) | `{"status":"healthy"}` | No dependencies; not proxied through the UI origin |
| `GET /` | manual | `{"message":"Orb is online","status":"active"}` | logs DEBUG |
| `GET /api/v1/setup/status` | `setup/page.tsx` (wizard, polled during downloads), `settings/page.tsx`, `ai-limited-banner.tsx` | `data_dir`, `models_dir`, `paths_json`, `default_vault_path`, `active_vault_path`, `ai_setup_mode`, `ai_configured` (`ai_gate.ai_is_configured()`), `local_models_ready` (`gguf_paths_if_present()`), `multimodal_ready` (all three HF snapshots ready), `needs_model_download` (`mode=="local" and not local_models_ready`), `database_backend`, `llm_provider` | Cheap: filesystem stats only; safe to poll |
| `GET /api/v1/setup/multimodal-status` | `setup/page.tsx` | `mode: "in_process"`, `models: {florence, whisper, marlin: bool}`, `services: services_ready()` | snapshot readiness, not "loaded in RAM" |
| `GET /api/v1/admin/maintenance-status?kb=` | `system-status-indicator.tsx` (sidebar pill, polled), `settings/page.tsx` | `community_detection: {running, pending_nodes, needed, timer_armed, idle_seconds}`, `temporal_digests: {running}`, `ingestion: {active, last_completed_at}`, `healthy: true` | From `IngestionWorkflow.get_maintenance_status()` + `ingestion_tracker.get_status_snapshot()`; `healthy` is hard-coded `True`. Indicator precedence: ingest active → "Ingesting"; community running → "Communities"; digest running → "Digests"; community timer armed → "Queued (idle_seconds, default 120)"; else idle |
| `GET /api/v1/notes/{id}/status` | note editor polling after save/ingest | `id`, `status` (`completed` \| `failed` \| `processing`), `processing_stage`, `processing_model`, `processed`, `failed` | Reads only the five `notes` columns; returns **503 "Database temporarily unavailable, retry shortly"** on SQLite lock errors so the poller retries |
| `GET /api/v1/settings` | settings page | effective provider/models (`06` §13) | first call constructs `LLMService` |
| `GET /api/v1/kb`, `GET /api/v1/kb/{id}/llm` | KB page | rows with `effective_llm`; override + `local_models` list | working tree |

Ingestion progress itself is exposed through `notes.processing_stage` (set at each pipeline stage: `"Saved"`, `"Queued for ingestion"`, multimedia/extraction/graph/indexing stage strings, or an error message on failure) and through the per-note stage/timing dict logged by the pipeline (including `extraction_chunks` and `model_load` seconds from `ModelLoadClock` in the working tree). Chat progress is streamed via the `request_id` polling endpoints. Details: [10](10-ingestion-pipeline.md), [16](16-retrieval-and-chat.md).

## 7. Debugging playbook

All paths relative to `DATA_DIR/logs/` (desktop: `~/Library/Application Support/Orb/data/logs/`).

| Symptom | Look in | Grep / what to look for |
|---|---|---|
| App shows "Orb failed to start … /health was ready" | `backend.log` (tail is in the dialog) | `Traceback`, `ModuleNotFoundError`, `Address already in use`, `Using SQLite database at` (confirms DATA_DIR) |
| Backend up but every AI call returns 503 `ai_not_configured` | `api.log` | `Runtime config overrides applied`, then check `GET /setup/status` (`local_models_ready`, `ai_setup_mode` is derived, `ai_configured`) |
| `GET /settings` or chat 500 with "API_KEY not set" / "Unsupported LLM provider" | `llm.log` | `Primary LLM Provider:`, `Initializing`, `Unsupported`; fix `.env` or `runtime_config.json` |
| Chat slow on first message | `llm.log` | `[ModelLoad] chat loaded in`, `Loading reranker GGUF (exclusive)`, `Raising chat n_ctx` — model swap costs; consider `ORB_MODEL_IDLE_SECONDS=0` |
| Chat answer empty / "Local LLM returned empty content" | `llm.log`, `backend.log` | `empty content (0 output tokens)`, `PromptTooLongError`, `RepetitionLoopError`; Metal OOM lines only appear in `backend.log` (llama.cpp stderr) |
| Extraction truncated / JSON repair loops | `ingestion.log` | `Extraction output truncated`, `chunk i/n`, `extraction_chunks`; tune `ORB_EXTRACTION_CHUNK_TOKENS`, `ORB_LLAMA_N_CTX` |
| Note stuck in "processing" | `ingestion.log`, then `errors.log` | note id, `[IngestionTracker]`, `Multimedia semaphore acquired`; check `GET /notes/{id}/status` `processing_stage` |
| Images/PDF/audio not described | `multimedia.log` | `Local image description failed`, `snapshot`, `ffmpeg`/`ffprobe` not found, `Florence`, `Whisper`, `Marlin`; `GET /setup/multimodal-status` |
| Search finds nothing / vector results empty | `retrieval.log` | `[Qdrant] Raw hits`, `threshold=`, `reranker=on/off`, `MeilisearchService` connection errors; verify Qdrant `17433` / Meili `17470` are up (`curl 127.0.0.1:17433/`, `curl 127.0.0.1:17470/health`) |
| Dimension mismatch errors from Qdrant | `retrieval.log`, `api.log` (startup) | `Embedding infrastructure sync skipped`, `dims`, `recreat`; `models_manifest.json` `embedding_dims` vs `EMBEDDING_DIMENSIONS` |
| Graph queries fail / "No Kuzu path" | `graph.log`, `api.log` | `Repaired kuzu_path`, Kuzu exceptions; check `knowledge_bases.kuzu_path` is a file path |
| External `.md` edits not detected | `ingestion.log` | `VaultWatcher`, `Vault watcher not started` (startup warning) |
| Finance pages error | `finance.log`, `firefly.log`, `DATA_DIR/firefly/app/storage/logs/` | `FireflyService`, token/`runtime.json` problems, `user_group` switch failures |
| `database is locked` / 503 from status polling | `database.log`, `errors.log` | concurrent writers (watcher + API); usually transient |
| Wrong data directory in use | `api.log` startup, `backend.log` | `Logging initialized at level … Logs dir:`, `Using SQLite database at`; compare with `paths.json` and `ORB_DATA_DIR` |
| Anything with a traceback | `errors.log` | aggregated ERROR+ from every component, oldest at top of `errors.log.5` |
| UI blank / 500 from Next | `frontend.log` | Next build/runtime errors; proxy target (`API_PROXY_TARGET`) |
| CORS errors in a browser dev setup | browser console + `api.log` (`OPTIONS` lines) | set `CORS_ORIGINS`/`CORS_ALLOW_ORIGIN_REGEX` |

Useful one-liners:

```bash
LOGS="$HOME/Library/Application Support/Orb/data/logs"
tail -f "$LOGS"/{api,ingestion,llm,retrieval}.log
grep -h " | ERROR | " "$LOGS"/*.log | sort | tail -50
grep -h "\[ModelLoad\]" "$LOGS"/llm.log | tail
```

## 8. Invariants and gotchas

- `setup_logging()` must run before importing `app.api`; do not move the import.
- Component loggers have `propagate=False`; adding a handler to the root logger will **not** capture component logs. Add to `COMPONENT_LOG_FILES` instead.
- A new `get_logger("SomethingNew")` without a table entry silently goes to stdout only (see `EmbeddingService`).
- `extra={}` payloads are invisible with the current formatters.
- The console handler writes to **stdout**; tools that only capture stderr miss everything.
- `errors.log` receives records from *all* loggers; never route it per component.
- `resolve_logs_dir()` creates the directory as a side effect on every call, including at import — importing `app.core.log` in a test creates `DATA_DIR/logs`.
- `.pylintrc` deliberately disables `logging-fstring-interpolation`/`logging-not-lazy`; f-strings in log calls are the house style.
- No log line carries the KB id unless the message includes it; when debugging multi-KB issues grep for the slug/vault path.
- Qdrant/Meili have no logs at all under the desktop shell.

## 9. Extension points

| Goal | Change |
|---|---|
| Dedicated file for a new component | add `"Name": "file.log"` to `COMPONENT_LOG_FILES`; use `get_logger("Name")` |
| Trace id in every line | add a `logging.Filter` that sets `record.trace_id = request_trace_id.get()` on all handlers in `setup_logging`, and include `%(trace_id)s` in `file_formatter` |
| JSON logs | swap `file_formatter` for a JSON formatter in `get_file_handler`; keep `console_formatter` human-readable |
| Per-KB log context | pass `kb.kb_id` in messages (or a filter reading a KB ContextVar set in `get_kb`) |
| Rotate supervisor stdio logs | in `desktop/supervisor.js _spawn`, rotate/truncate `serviceLogPath` files before `openSync` |
| Capture Qdrant/Meili output | pass `logFile: serviceLogPath(dataDir, "qdrant")` in `startSearchEngines` `_spawn` options |

## 10. History / rationale

- Component-file routing under `DATA_DIR/logs` arrived with the desktop refactor (`3f21e08`, 2026-08-02) replacing a single repo-relative `logs/` folder; `resolve_logs_dir`/`reconfigure_logging` were added so the first-run wizard could move logs without restarting.
- `uvicorn.access`/`uvicorn.error` were added to the table so request logs share Orb's format and rotation.
- The trace-id middleware predates the routing table (`68494b7`, 2026-05-07) and was never wired into formatters.
- `ModelLoadClock` (`[ModelLoad]` lines, working tree) exists because "a slow disk and a slow model look identical in a bare stage timer".
