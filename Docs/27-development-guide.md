# Development Guide

**What this covers:** how to set up a working environment, run Orb in each mode, make common kinds of change safely, and the conventions that the codebase follows. It is written for a human contributor and for an AI coding assistant asked to modify the repository. Subsystem internals are in the numbered docs; this file is the practical "how do I…" layer on top of them.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Desktop shell](04-desktop-shell.md) · [Packaging](05-packaging-build-and-release.md) · [Configuration reference](21-configuration-reference.md) · [Logging](23-logging-and-observability.md) · [Testing](24-testing.md) · [Decisions and constraints](26-decisions-and-constraints.md)

---

## 1. Prerequisites

| Tool | Why | Notes |
|---|---|---|
| Rust (`brew install rust` or rustup) + `cargo install tauri-cli` | Tauri shell in `desktop/src-tauri` | Linux also needs webkit2gtk dev packages |
| Node.js 20+ | Building the Vite UI only (nothing Node ships) | `frontend/package.json` |
| Python 3.11+ (packaged builds ship 3.12.9) | FastAPI backend + desktop runtime | Create `backend/.venv`; debug shell builds use it automatically (or set `ORB_PYTHON`) |
| `cmake` + Xcode CLT (macOS) / build-essential (Linux) | building `llama-cpp-python` with Metal / CUDA / CPU backends | Only needed when installing or packaging llama-cpp-python |
| `ffmpeg` | audio transcoding for Qwen3-ASR and media probing | Homebrew paths are prepended to `PATH` by the shell (`runtime.rs tool_path()`) so Finder launches find it |
| ~10–20 GB disk | GGUF + HF model downloads, Qdrant/Meili binaries, portable PHP | Models can live on a NAS via the setup page's models dir |

No Docker, Ollama, LM Studio or database server is required. Qdrant, Meilisearch and PHP/Firefly are downloaded by `desktop_runtime.py` into `DATA_DIR` on first run. Keep `DATA_DIR` on local disk — never iCloud Drive/OneDrive/Dropbox/Google Drive (the runtime warns); only the vault may be synced.

---

## 2. First-time setup

```bash
git clone https://github.com/josetseph/Orb.git
cd Orb

# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Metal build of llama-cpp-python on Apple Silicon:
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
# (multimodal deps in requirements-multimodal.txt are installed on demand by Setup)
cp .env.example .env                  # optional: cloud API keys, overrides
cd ..

# Frontend
cd frontend && npm install && cd ..

# Desktop shell (Rust)
cargo install tauri-cli --version "^2" --locked
```

`backend/.env.example` is the annotated reference for every setting; for a pure-local desktop run you can leave `.env` empty. Cloud API keys are **not** set there any more — enter them in Settings → Cloud API keys (the backend stores them in the OS keychain via `keyring`). Values in `.env` still seed the credential store when you run the backend outside the desktop shell.

---

## 3. Running Orb

### 3.1 Desktop dev mode (recommended)

```bash
# Terminal 1 — UI with live reload
cd frontend && npm run dev                                   # Vite on 3700, proxies /api/v1 to 17401

# Terminal 2 — the shell (debug builds always run the repo backend)
cd desktop/src-tauri && ORB_URL=http://127.0.0.1:3700 cargo tauri dev
```

What happens: the shell shows the setup page if `paths.json` is missing or corrupt, then spawns `python -m app.desktop_runtime` (`backend/.venv` python), which frees the 174xx ports, starts the API (uvicorn on 17401) immediately, and downloads/boots Qdrant, Meilisearch and Firefly behind it; the window opens as soon as `/health` answers and points at `ORB_URL`. Without `ORB_URL` the window loads the API, which serves `frontend/dist` if you have run `npm run build`. Logs go to `DATA_DIR/logs/`; `DATA_DIR` comes from `paths.json` (default `~/Library/Application Support/Orb/data`; keep it on local disk).

Useful env switches (read by `src-tauri/src/runtime.rs` / `desktop_runtime.py`):

| Env | Effect |
|---|---|
| `ORB_SKIP_WIZARD=1` | never show the setup page |
| `ORB_PATHS_FILE=/path/paths.json` | use an alternative bootstrap file (handy for a throwaway dev profile) |
| `ORB_USE_RESOURCES=1` | make a debug build run against `desktop/resources/` like a packaged app |
| `ORB_URL=http://127.0.0.1:3700` | point the window at the Vite dev server |
| `ORB_ROOT`, `ORB_PYTHON` | override repo root / interpreter discovery |
| `ORB_*_PORT` | move any of the four ports |
| `LOG_LEVEL=DEBUG` | verbose backend logs |

### 3.2 Backend only

```bash
cd backend
source .venv/bin/activate
ORB_DATA_DIR=$PWD/../data uvicorn app.main:app --reload --port 8000
```

You must have Qdrant and Meilisearch reachable at the configured host/port (`QDRANT_PORT`, `MEILI_PORT`, `MEILI_MASTER_KEY`), e.g. from a desktop session already running (ports 17433/17470; read the key from `DATA_DIR/meili_master_key`) or binaries started by hand from `DATA_DIR/bin/<triple>/`.

### 3.3 Frontend only

```bash
cd frontend
npm run dev                                              # Vite on 3700
API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev       # against a bare uvicorn on 8000
```

`vite.config.ts` proxies `/api/v1`, `/vault-files`, `/health` to `API_PROXY_TARGET` (default `http://127.0.0.1:17401`). Open `http://127.0.0.1:3700` in a browser; `window.orbDesktop` is absent there, so pickers and notifications degrade gracefully.

### 3.4 Packaged layout and installers

```bash
python3 desktop/build.py prepare     # 10–20 min: python-build-standalone + pip, vite build, firefly seed → desktop/resources/
cd desktop/src-tauri && ORB_USE_RESOURCES=1 cargo tauri dev   # test the bundled runtimes
python3 desktop/build.py dist        # preflight + cargo tauri build → target/release/bundle/ (macOS: .app via Tauri, .dmg via hdiutil)
```

Release builds are produced by CI on `desktop-v*` tags. See [05](05-packaging-build-and-release.md).

---

## 4. Everyday commands

| Task | Command |
|---|---|
| Unit tests | `cd backend && .venv/bin/python -m pip install pytest pytest-asyncio && .venv/bin/python -m pytest tests/unit -q` (pytest is not in `requirements.txt`) |
| Lint backend | `cd backend && .venv/bin/python -m pylint app` |
| Lint frontend | `cd frontend && npm run lint` |
| Type-check frontend | `cd frontend && npx tsc --noEmit` |
| Full Leiden rebuild | `POST /api/v1/admin/rebuild-communities` (or the Rebuild button on the Storage page) |
| Tail logs | `tail -f "$DATA_DIR/logs/backend.log" "$DATA_DIR/logs/ingestion.log"` |
| Reset a dev profile | quit Orb, delete `<repo>/data` (or the chosen DATA_DIR) and `paths.json` |

Unit tests need no live services: `tests/unit/conftest.py` stubs Kuzu, Qdrant, Meilisearch and the LLM, but the test modules still import the real service modules, so the venv must have `requirements.txt` installed (`qdrant_client`, `kuzu`, `openai`, …) plus `pytest` and `pytest-asyncio`. There is no CI test job; run tests locally before committing.

State on 2026-09-20: the full suite is green (485 passed, 0 failed) when run against an interpreter that has `requirements.txt` installed — the bundled runtime under `desktop/resources/backend/python` plus `pytest`/`pytest-asyncio` works, as does a fresh `uv venv`. Set `DATA_DIR`/`ORB_DATA_DIR` to a scratch folder first, otherwise `graph.py` opens the real Kuzu file, which the running app holds locked.

---

## 5. Where data lives while you develop

| Item | Dev default | Packaged default |
|---|---|---|
| `paths.json` | `~/Library/Application Support/Orb/paths.json` (macOS) — shared with a packaged install unless you set `ORB_PATHS_FILE` | same |
| `DATA_DIR` | `paths.json.data_dir` under `cargo tauri dev`; `<repo>/data` for a bare `uvicorn` without `paths.json` | `~/Library/Application Support/Orb/data` (local disk; the runtime warns on a cloud-synced path) |
| `MODELS_DIR` | `paths.json.models_dir`; `<repo>/backend/models` for a bare `uvicorn` without `paths.json` | `~/Library/Application Support/Orb/models` |
| Logs | `DATA_DIR/logs` | same |

Because the bootstrap file is shared, a dev session can silently pick up your real install's data dir. Use `ORB_PATHS_FILE` for isolated experiments. See [22](22-data-directory-layout.md).

---

## 6. Codebase conventions

### Backend (Python)

- **Layering**: `core` → `models`/`schemas` → `services` → `workflows` → `api`. Routers never talk to Kuzu/Qdrant/Meili directly; they call services through the `KBContext`.
- **Every KB-scoped route** declares `kb: KBContext = Depends(get_kb)` and uses `kb.graph`, `kb.qdrant`, `kb.meili`, `kb.retrieval_service`, `kb.ingestion_workflow`, `kb.chat_workflow`. Never import a global graph/qdrant instance for user data.
- **Blocking I/O off the event loop**: wrap Kuzu, filesystem and llama.cpp calls in `asyncio.to_thread` (see `api/notes.py`, `workflows/ingestion.py`).
- **Loggers**: `logger = get_logger("<Component>")` with a name present in `core/log.py::COMPONENT_LOG_FILES`; otherwise lines land in the default file. Use `extra={...}` for structured fields.
- **Settings**: add new knobs to `core/config.py::Settings` with a default, document them in `.env.example`, and list them in [21](21-configuration-reference.md). Read `settings.X`, not `os.environ`, except for the `ORB_LLAMA_*`-style runtime knobs that are deliberately env-only.
- **Runtime-mutable settings** go through `core/runtime_config.py::MUTABLE_KEYS`; never persist secrets there.
- **LLM output** is untrusted: parse through the Pydantic schemas in `schemas/extraction.py` (alias-tolerant validators) and the JSON-cleaning helpers in `services/llm.py`.
- **Style**: pylint config in `backend/.pylintrc`; long modules carry `# pylint: disable=too-many-lines`. Imports are absolute (`from app.services...`). Type hints everywhere; `from __future__ import annotations` in new modules.

### Frontend (TypeScript / React)

- Vite + react-router; pages live in `src/app/<route>/page.tsx` and are registered as lazy routes in `src/App.tsx`. Route-private code lives in `_components/`, `_hooks/`, `_lib/` next to the page.
- All HTTP goes through `src/lib/api.ts`; add a typed method there and a type in `src/lib/types.ts` rather than calling `fetch` from a component. Pass the current KB from `useKB()`.
- Long jobs are polled; reuse the existing patterns (`ChatProvider`, `useNoteIngest`) instead of inventing streams.
- Uploads must go through `api.upload` (multipart, long timeout); every URL is relative to the API origin that serves the UI.
- Dark theme only; Tailwind v4 utility classes; `lucide-react` icons; CSS transitions.
- React Compiler is enabled (`react({ compiler: true })` in `vite.config.ts`), so avoid manual `useMemo`/`useCallback` unless profiling demands it, and keep components pure.

### Desktop (Rust shell + Python runtime)

- The shell (`desktop/src-tauri`) stays minimal: anything the UI needs that can be an HTTP call goes in the backend (`api_desktop.py`), not in `init.js`. New bridge members need a command in `commands.rs`, an entry in `capabilities/remote-ui.json`, the `init.js` wrapper and the `OrbDesktopBridge` type in `frontend/src/lib/desktop.ts`.
- Ports and sidecar orchestration live in `backend/app/desktop_runtime.py` (`PORTS`, `_spawn`, `wait_http`, `status`). Add new services there with a readiness check and a `_log_path` log file; never block uvicorn start on them.
- Every env var read by the runtime uses the `ORB_*` name via `_env`.

### Commits

Recent history uses one-line imperative subjects with a long explanatory body for anything non-trivial (see `git log -5 --format=%B`). Version bumps touch `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`, `frontend/package.json` and `backend/app/main.py` (`FastAPI(version=…)`); release tags are `desktop-v<version>` and CI drafts the GitHub Release from them.

---

## 7. Recipes

### Add an API endpoint

1. Pick the router in `backend/app/api/` (or `api_desktop.py` for setup/finance/notes-graph).
2. Define a Pydantic body in `backend/app/schemas/` if it has a body.
3. Take `kb: KBContext = Depends(get_kb)` and `db: AsyncSession = Depends(get_db)` as needed.
4. Put logic in a service; keep the route thin; offload blocking work with `asyncio.to_thread`.
5. Add the client method to `frontend/src/lib/api.ts` and the response type to `types.ts`.
6. Document it in [07](07-api-reference.md).

### Add a configuration knob

`core/config.py` → `.env.example` → (if the desktop needs a default) the `setdefault` block in `desktop_runtime.main()` → [21](21-configuration-reference.md). If it must be changeable at runtime, add it to `runtime_config.MUTABLE_KEYS`, `apply_to_settings` and the settings API.

### Add a local model to the catalogue

Add a `ModelOption` in `services/model_catalog.py` (id, role, family, HF repo/file, size, dims for embed). For a new embedding model make sure `EMBEDDING_DIMENSIONS` handling and `sync_embedding_infrastructure` cover the new dimension; changing dims requires recreating Qdrant collections deliberately, never implicitly.

### Add a cloud provider

First ask whether you need one: **`openai_compat` already covers every OpenAI-shaped API** (OpenRouter, Groq, Together, vLLM, LM Studio, llama-server, Ollama) — the user supplies a URL, a key and a model name, with no code change. A new provider is only warranted for a genuinely different wire protocol (as with Gemini and Anthropic).

If it is: `services/llm.py` (client construction in `_build_clients`, a `_chat` branch if the SDK is not OpenAI-shaped, model resolution), `services/credentials.py::CLOUD_PROVIDERS` (+ `_ENV_SETTING` for the contributor seed), `core/config.py` (model field), `.env.example`, `kb_registry.LLM_PROVIDERS`, the settings/KB UI option lists. See [13](13-llm-providers-and-prompting.md).

### Change the graph schema

Edit `_SCHEMA_STMTS` in `services/graph.py` using `CREATE … IF NOT EXISTS`; add an idempotent migration for existing databases; update the writes in `workflows/ingestion.py`, the readers in `services/retrieval.py`, the 3D export in `api/graph.py`, and the tests in `tests/unit/test_graph_queries.py`. See [14](14-graph-storage-kuzu.md).

### Add a new attachment type to ingestion

Extend the discovery regexes and the per-type handler in `services/multimedia.py`; produce an enrichment block using the existing markers so re-ingest stripping still works; respect `MULTIMEDIA_CONCURRENCY` and the residency manager. See [11](11-multimedia-enrichment.md).

### Add a page

Create `frontend/src/app/<route>/page.tsx`, add a lazy `<Route>` in `src/App.tsx` and the entry in `components/sidebar.tsx`, use `useKB()` and `api.*`. See [18](18-frontend-architecture.md).

### Debug "the app won't start"

1. Read the error dialog (shown after the runtime dies three times in a minute); it includes the tail of `backend.log`.
2. Check `DATA_DIR/logs/backend.log` (runtime `[desktop] …` lines + uvicorn), then `qdrant.log`, `meilisearch.log`, `firefly.log`; `DATA_DIR/boot-status.json` holds the last sidecar status.
3. Verify nothing else owns ports 17401–17470 (`lsof -iTCP:17401 -sTCP:LISTEN`); the runtime kills stale listeners at start, but a foreign process on those ports will be killed too.
4. A corrupt `paths.json` re-opens the setup page by design.
5. A sidecar failure does not stop the app: the API still serves and the status indicator shows `Local services failed to start: …`; a fresh install needs network once for the binaries.

### Debug "chat/ingest says AI not configured"

Nothing is reachable: no chat+embed GGUFs on disk (`gguf_paths_if_present()`), no cloud key in the credential store, no `LLM_BASE_URL` — or a per-KB provider override points at a cloud provider without a key. Pick a model on the Models page. Check `GET /api/v1/setup/status`.

---

## 8. Things to never do

Short form of [26](26-decisions-and-constraints.md):

- Do not add HTTP model sidecars or reintroduce Ollama / LM Studio / llama-server paths.
- Do not load two heavy models at once outside the residency manager.
- Do not store note bodies in SQLite, or attachments anywhere but `<vault>/attachments/` (grouped by note folder).
- Do not drop or recreate Qdrant collections implicitly on a dimension mismatch.
- Do not query another KB's data from a KB-scoped route; do not let finance lists cross administrations.
- Do not write `TYPESENSE_*` names in new code; the `LIVEOS_*` aliases no longer exist.
- Do not add Docker, Postgres or a second UI server back; the API serves the UI and SQLite is the only database.
- Do not treat `/vault-files/...` paths as temporary files, and never write one into a note — stored links are vault-root-relative (`attachments/…`).
- Do not change `n_ctx` / `swa_full` defaults for Gemma 4 without re-testing the ordinal-loop and Metal OOM cases.
