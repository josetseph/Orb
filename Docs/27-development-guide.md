# Development Guide

**What this covers:** how to set up a working environment, run Orb in each mode, make common kinds of change safely, and the conventions that the codebase follows. It is written for a human contributor and for an AI coding assistant asked to modify the repository. Subsystem internals are in the numbered docs; this file is the practical "how do I…" layer on top of them.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Desktop shell](04-desktop-shell.md) · [Packaging](05-packaging-build-and-release.md) · [Configuration reference](21-configuration-reference.md) · [Logging](23-logging-and-observability.md) · [Testing](24-testing-and-benchmarks.md) · [Decisions and constraints](26-decisions-and-constraints.md)

---

## 1. Prerequisites

| Tool | Why | Notes |
|---|---|---|
| Node.js 20+ (CI uses 24) | Electron shell, Next.js | `desktop/` and `frontend/` each have their own `package.json` |
| Python 3.11+ (packaged builds ship 3.12.9) | FastAPI backend | Create `backend/.venv`; the supervisor prefers it automatically |
| `cmake` + Xcode CLT (macOS) / build-essential (Linux) | building `llama-cpp-python` with Metal / CUDA / CPU backends | Only needed when installing or packaging llama-cpp-python |
| `ffmpeg` | audio transcoding for Whisper | Homebrew paths are prepended to `PATH` by the supervisor so Finder launches find it |
| ~10–20 GB disk | GGUF + HF model downloads, Qdrant/Meili binaries, portable PHP | Models can live on a NAS via the wizard's models dir |

No Docker, Ollama, LM Studio or database server is required. Qdrant, Meilisearch and PHP/Firefly are downloaded by the supervisor into `DATA_DIR` on first run.

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

# Desktop shell
cd desktop && npm install
```

`backend/.env.example` is the annotated reference for every setting; for a pure-local desktop run you can leave `.env` empty. Cloud API keys are **not** set there any more — enter them in Settings → Cloud API keys (the shell encrypts them into the OS keychain). Values in `.env` still seed the credential store when you run the backend outside the desktop shell.

---

## 3. Running Orb

### 3.1 Desktop dev mode (recommended)

```bash
cd desktop
npm start
```

What happens: `main.js` shows the wizard if `paths.json` is missing or corrupt, then the supervisor frees the 174xx ports, downloads Qdrant/Meilisearch if needed, starts Firefly, then starts the API (`backend/.venv` python, uvicorn on 17401) and the UI (`next dev` on 17400) in parallel. Logs go to `DATA_DIR/logs/`. `DATA_DIR` defaults to `<repo>/data` in dev (unless `paths.json` says otherwise).

Useful env switches (all read by `desktop/paths.js` / `main.js` / `supervisor.js`):

| Env | Effect |
|---|---|
| `ORB_SKIP_WIZARD=1` | never show the wizard |
| `ORB_PATHS_FILE=/path/paths.json` | use an alternative bootstrap file (handy for a throwaway dev profile) |
| `ORB_FRONTEND_DEV=1` | force `next dev` even if a standalone build exists |
| `ORB_RESOURCES=./resources` | run against the packaged layout produced by `prepare-dist` |
| `ORB_URL=http://127.0.0.1:3700` | point the main window at an externally running UI |
| `ORB_PYTHON`, `ORB_NODE` | override interpreter/binary discovery |
| `ORB_*_PORT` | move any of the five ports |
| `LOG_LEVEL=DEBUG` | verbose backend logs |

### 3.2 Backend only

```bash
cd backend
source .venv/bin/activate
ORB_DATA_DIR=$PWD/../data uvicorn app.main:app --reload --port 8000
```

You must have Qdrant and Meilisearch reachable at the configured host/port (`QDRANT_PORT`, `MEILI_PORT`, `MEILI_MASTER_KEY`), e.g. from a desktop session already running (ports 17433/17470) or from `docker compose up qdrant meilisearch`. The benchmark harness assumes the API at `http://localhost:8000`.

### 3.3 Frontend only

```bash
cd frontend
API_PROXY_TARGET=http://127.0.0.1:17401 npm run dev     # serves on 3700
```

`next.config.ts` rewrites `/api/v1`, `/vault-files`, `/health` to `API_PROXY_TARGET`. Set `NEXT_PUBLIC_API_URL` to an absolute URL if you prefer to bypass the rewrites.

### 3.4 Docker (contributors only)

```bash
docker compose up -d       # postgres, qdrant, meilisearch, backend (8700), frontend (3700)
```

The compose stack is not the product path: no model sidecars, Postgres instead of SQLite, and the desktop shell must never be wired to it.

### 3.5 Packaged layout and installers

```bash
cd desktop
npm run prepare-dist          # 10–20 min: python-build-standalone + pip, next build, node, firefly seed
ORB_RESOURCES=./resources npm start   # test the bundled runtimes
npm run dist:mac | dist:win | dist:linux
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
| Full Leiden rebuild | `cd backend && .venv/bin/python scripts/run_community_detection.py` or `POST /api/v1/admin/rebuild-communities` |
| Benchmarks | `python tests/benchmark/fetch_notes.py`, `prepare_dataset.py --dataset hotpotqa`, `evaluate.py --dataset hotpotqa --verbose` |
| Tail logs | `tail -f "$DATA_DIR/logs/backend.log" "$DATA_DIR/logs/ingestion.log"` |
| Reset a dev profile | quit Orb, delete `<repo>/data` (or the chosen DATA_DIR) and `paths.json` |

Unit tests need no live services: `tests/unit/conftest.py` stubs Kuzu, Qdrant, Meilisearch and the LLM, but the test modules still import the real service modules, so the venv must have `requirements.txt` installed (`instructor`, `qdrant_client`, `kuzu`, …). There is no CI test job; run tests locally before committing.

State observed on 2026-09-02: the repo's `backend/.venv` contained only a partial install (FastAPI, Pydantic, SQLAlchemy), so collection failed with `ModuleNotFoundError: instructor` / `qdrant_client`. Independently of the venv, `tests/unit/test_relationships.py` imports `app.schemas.relationships`, a module that no longer exists in the tree, so that file will error at collection until it is updated or removed. See [24](24-testing-and-benchmarks.md).

---

## 5. Where data lives while you develop

| Item | Dev default | Packaged default |
|---|---|---|
| `paths.json` | `~/Library/Application Support/Orb/paths.json` (macOS) — shared with a packaged install unless you set `ORB_PATHS_FILE` | same |
| `DATA_DIR` | `<repo>/data` | `~/Library/Application Support/Orb/data` |
| `MODELS_DIR` | `<repo>/backend/models` | `~/Library/Application Support/Orb/models` |
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

- App router; every page is `"use client"`. Route-private code lives in `_components/`, `_hooks/`, `_lib/` next to the page.
- All HTTP goes through `src/lib/api.ts`; add a typed method there and a type in `src/lib/types.ts` rather than calling axios from a component. Pass the current KB from `useKB()`.
- Wait for `isHydrated` from `useKB()` before the first fetch, or you will fetch the default KB and then refetch.
- Long jobs are polled; reuse the existing patterns (`ChatProvider`, `useNoteIngest`) instead of inventing streams.
- Uploads must go through `api.upload`, which resolves the direct desktop API URL to avoid the Next proxy body limit.
- Dark theme only; Tailwind v4 utility classes; `lucide-react` icons; `framer-motion` for transitions.
- React Compiler is enabled (`reactCompiler: true`), so avoid manual `useMemo`/`useCallback` unless profiling demands it, and keep components pure.

### Desktop (Node / Electron)

- Renderer isolation is strict: `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`. New renderer capabilities go through `preload.js` + an `ipcMain.handle` in `main.js` with input validation (`assertAbsolutePath`, allow-listed roots).
- All ports come from `ports.js`; all path discovery from `paths.js`. Do not hard-code either.
- Child processes are spawned detached with log-file fds; add new services via `Supervisor._spawn` and a `waitHttp` readiness check.
- Every env var read by the shell accepts an `ORB_*` name first and a `LIVEOS_*` legacy alias second via `envFirst`.

### Commits

Recent history uses one-line imperative subjects with a long explanatory body for anything non-trivial (see `git log -5 --format=%B`). Version bumps touch `desktop/package.json` and `frontend/package.json`; release tags are `desktop-v<version>`.

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

`core/config.py` → `.env.example` → (if desktop-injected) `desktop/supervisor.js` env block → [21](21-configuration-reference.md). If it must be changeable at runtime, add it to `runtime_config.MUTABLE_KEYS`, `apply_to_settings` and the settings API.

### Add a local model to the catalogue

Add a `ModelOption` in `services/model_catalog.py` (id, role, family, HF repo/file, size, dims for embed). For a new embedding model make sure `EMBEDDING_DIMENSIONS` handling and `sync_embedding_infrastructure` cover the new dimension; changing dims requires recreating Qdrant collections deliberately, never implicitly.

### Add a cloud provider

First ask whether you need one: **`openai_compat` already covers every OpenAI-shaped API** (OpenRouter, Groq, Together, vLLM, LM Studio, llama-server, Ollama) — the user supplies a URL, a key and a model name, with no code change. A new provider is only warranted for a genuinely different wire protocol (as with Gemini and Anthropic).

If it is: `services/llm.py` (client construction in `init_clients` **and** `_init_ingestion_clients`, model resolution), `services/credentials.py::CLOUD_PROVIDERS` (+ `_ENV_SETTING` for the contributor seed), `desktop/credentials.js::KNOWN_PROVIDERS`, `core/config.py` (model field), `.env.example`, `kb_registry.LLM_PROVIDERS`, the settings/KB UI option lists. See [13](13-llm-providers-and-prompting.md).

### Change the graph schema

Edit `_SCHEMA_STMTS` in `services/graph.py` using `CREATE … IF NOT EXISTS`; add an idempotent migration for existing databases; update the writes in `workflows/ingestion.py`, the readers in `services/retrieval.py`, the 3D export in `api/graph.py`, and the tests in `tests/unit/test_graph_queries.py`. See [14](14-graph-storage-kuzu.md).

### Add a new attachment type to ingestion

Extend the discovery regexes and the per-type handler in `services/multimedia.py`; produce an enrichment block using the existing markers so re-ingest stripping still works; respect `MULTIMEDIA_CONCURRENCY` and the residency manager. See [11](11-multimedia-enrichment.md).

### Add a page

Create `frontend/src/app/<route>/page.tsx` (`"use client"`), add the entry to the `navigation` array in `components/sidebar.tsx`, use `useKB()` and `api.*`. See [18](18-frontend-architecture.md).

### Debug "the app won't start"

1. Read the error dialog; it includes the last 24 lines of the failing service's log.
2. Check `DATA_DIR/logs/backend.log` (uvicorn), `frontend.log`, `firefly.log`.
3. Verify nothing else owns ports 17400–17470 (`lsof -iTCP:17401 -sTCP:LISTEN`); the supervisor kills stale listeners at start, but a foreign process on those ports will be killed too.
4. A corrupt `paths.json` re-opens the wizard by design.
5. Binary download failures fall back to cached binaries; a fresh install needs network once.

### Debug "chat/ingest says AI not configured"

Nothing is reachable: no chat+embed GGUFs on disk (`gguf_paths_if_present()`), no cloud key in the credential store, no `LLM_BASE_URL` — or a per-KB provider override points at a cloud provider without a key. Pick a model on the Models page. Check `GET /api/v1/setup/status`.

---

## 8. Things to never do

Short form of [26](26-decisions-and-constraints.md):

- Do not add HTTP model sidecars or reintroduce Ollama / LM Studio / llama-server paths.
- Do not load two heavy models at once outside the residency manager.
- Do not store note bodies in SQLite or attachments outside the vault.
- Do not drop or recreate Qdrant collections implicitly on a dimension mismatch.
- Do not query another KB's data from a KB-scoped route; do not let finance lists cross administrations.
- Do not write `LIVEOS_*` / `TYPESENSE_*` names in new code.
- Do not wire Docker into the desktop shell.
- Do not treat `/vault-files/...` paths as temporary files.
- Do not change `n_ctx` / `swa_full` defaults for Gemma 4 without re-testing the ordinal-loop and Metal OOM cases.
