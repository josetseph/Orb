# Repository Layout

**What this covers:** every tracked directory and file in the Orb repository, what it is for, and which documentation file describes it in depth. Use this as the map when you need to find where a behaviour lives. Build outputs and user data that are git-ignored are listed separately so they are not mistaken for source.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Desktop shell](04-desktop-shell.md) · [Packaging](05-packaging-build-and-release.md) · [Backend core](06-backend-core-and-configuration.md) · [Frontend architecture](18-frontend-architecture.md) · [Data directory layout](22-data-directory-layout.md) · [Development guide](27-development-guide.md)

---

## 1. Top level

```
Orb/
├── backend/            FastAPI + Python services (the API process)
├── frontend/           Vite + React UI (static build served by the API; Vite dev server in dev)
├── desktop/            Tauri (Rust) shell, first-run setup page, build.py packaging script
├── Docs/               This documentation set
├── Platform Images/    Screenshots used by the README
├── .github/workflows/  desktop-release.yml — installer build matrix + a job that drafts the GitHub Release; ci.yml — pytest, frontend lint/build, cargo check on push/PR
├── README.md           Product README (install, build, privacy)
├── LICENSE             MIT
└── .gitignore
```

Two processes are built from three top-level source trees: the Tauri shell from `desktop/`, and the Python desktop runtime + API from `backend/`, which serves the static UI built from `frontend/`. The runtime additionally downloads or the bundle ships binaries the repo does not own (Qdrant, Meilisearch, PHP + Firefly III, portable Python).

| Path | Purpose | Detailed doc |
|---|---|---|
| `backend/` | API, ingestion, retrieval, graph, models, finance proxy | 06 – 17, 21 – 24 |
| `frontend/` | Vite + React (react-router) UI | 18 – 20 |
| `desktop/` | Tauri shell, first-run setup page, `build.py` packaging | 04, 05 |
| `.github/workflows/desktop-release.yml` | CI: macOS arm64 / macOS x64 / Windows / Linux installers, then a `release` job that drafts the GitHub Release from the four artifacts on a `desktop-v*` tag | 05 |
| `.github/workflows/ci.yml` | CI on every push to `main` and every PR: backend `pytest tests/unit`, frontend `npm run lint` + `npm run build`, `cargo check` of the Tauri shell | 24, 05 |

---

## 2. `backend/`

```
backend/
├── app/
│   ├── main.py                 FastAPI app, middleware, startup/shutdown hooks
│   ├── desktop_runtime.py      The process the Tauri shell spawns: port sweep, uvicorn, sidecar download/boot, Firefly bootstrap
│   ├── api_desktop.py          Setup/paths, notes-graph (wikilinks), finance, /desktop/reveal, /vault-files routes
│   ├── api/                    Domain routers (one file per domain) + shared deps
│   │   ├── __init__.py         register_all_routers()
│   │   ├── deps.py             get_kb() — resolves ?kb= to a KBContext
│   │   ├── admin.py            maintenance status, rebuild communities, digests, reset, reingest-all
│   │   ├── chat.py             sync/async chat, status polling, conversations, messages
│   │   ├── credentials.py      cloud API keys / endpoint credentials (OS keychain via keyring)
│   │   ├── files.py            /upload and file delete
│   │   ├── graph.py            3D graph export, node detail, entity search / scan-text / note-subgraph
│   │   ├── health.py           GET / and GET /health
│   │   ├── kb.py               knowledge base CRUD, empty, delete-non-default
│   │   ├── models.py           model catalogue / download routes
│   │   ├── notes.py            notes CRUD, move, ingest, status, batch delete, legacy /ingest
│   │   ├── settings.py         GET/PATCH runtime LLM settings
│   │   └── vault.py            vault move/delete/mkdir/folders/local-path
│   ├── core/
│   │   ├── config.py           pydantic Settings (all env-driven configuration)
│   │   ├── paths.py            DATA_DIR / MODELS_DIR / paths.json resolution
│   │   ├── runtime_config.py   DATA_DIR/runtime_config.json mutable overrides
│   │   ├── database.py         async SQLAlchemy engine (SQLite via aiosqlite)
│   │   ├── log.py              component log routing under DATA_DIR/logs
│   │   └── inference_device.py Metal / CUDA / Vulkan / CPU detection for llama.cpp
│   ├── models/                 SQLAlchemy ORM (metadata only — bodies live in vault .md; knowledge_bases is raw DDL in kb_registry.py)
│   │   ├── note.py             notes
│   │   ├── chat.py             chat_conversations, chat_messages
│   │   └── wikilink.py         note_links
│   ├── schemas/                Pydantic request / extraction schemas
│   │   ├── note.py             CreateNoteInput, MoveNoteInput, MoveVaultFileInput, …
│   │   ├── chat.py             ChatInput, ChatTurn, CreateConversationInput
│   │   └── extraction.py       Node, ExtractedRelationship, Extraction, NoteInput (LLM-noise tolerant)
│   ├── services/               Stateful services (one instance per KB where relevant)
│   │   ├── kb_registry.py      KBContext + registry (per-KB service bundles)
│   │   ├── vault.py            vault filesystem helpers, WIKILINK_RE, self-write suppression
│   │   ├── vault_ops.py        move/rename/delete with markdown reference rewriting; safe_vault_join
│   │   ├── vault_sync.py       reconcile SQLite note rows with .md files on disk
│   │   ├── vault_watcher.py    watchdog observers for external vault edits
│   │   ├── note_files.py       note_body / persist_note_body
│   │   ├── wikilinks.py        note_links maintenance, notes-graph payloads
│   │   ├── local_storage.py    uploads into attachments/<note folder>/; vault-relative link ↔ /vault-files URL mapping
│   │   ├── graph.py            Kuzu GraphService (schema, writes, queries, communities, digests)
│   │   ├── qdrant_service.py   Qdrant collections (cores / relationships / isolated contexts)
│   │   ├── meilisearch_service.py  per-KB keyword index
│   │   ├── embedding.py        in-process GGUF embeddings
│   │   ├── reranker.py         in-process GGUF cross-encoder reranker
│   │   ├── retrieval.py        hybrid multi-hop retrieval loop
│   │   ├── chat_store.py       conversation / message persistence
│   │   ├── credentials.py      CredentialStore — cloud keys in the OS keychain (keyring)
│   │   ├── llm.py              multi-provider LLMService (local / OpenAI / Gemini / Anthropic / HF)
│   │   ├── local_models.py     GGUF download + llama-cpp-python runtime + exclusive residency
│   │   ├── model_catalog.py    resource-aware catalogue of chat / embed / rerank GGUFs
│   │   ├── model_discovery.py  discover user-supplied chat GGUFs on disk (shards, depth cap)
│   │   ├── model_formats.py    GGUF layout helpers: shards, magic bytes, name-based advisories
│   │   ├── gguf_metadata.py    GGUF header parser (pooling_type / chat_template role hints)
│   │   ├── asr_engine.py       Qwen3-ASR backend selection (MLX on Apple Silicon, transformers elsewhere)
│   │   ├── multimodal_runtime.py   in-process Qwen3-ASR / Marlin loader
│   │   ├── multimodal_models.py    HF snapshot downloads into MODELS_DIR
│   │   ├── multimodal_services.py  readiness + on-demand pip install of torch/transformers
│   │   ├── multimedia.py       attachment discovery + PDF / image / audio / video / doc enrichment
│   │   ├── ingestion_tracker.py    ingestion bookkeeping + idle-triggered Leiden recompute
│   │   ├── ingestion_checkpoint.py resume a failed ingestion from the model call that broke
│   │   ├── extraction_budget.py    learn how large an extraction chunk each model handles
│   │   ├── firefly_service.py  Firefly III HTTP client, per-KB administration scoping
│   │   └── ai_gate.py          AI readiness, derived from real configuration
│   ├── workflows/
│   │   ├── ingestion.py        IngestionWorkflow — extraction, graph persistence, embedding, indexing
│   │   ├── chat.py             ChatWorkflow — research loop + attribution
│   │   ├── extraction_chunking.py   paragraph-bounded chunking + merge of per-chunk extractions
│   │   └── agents/ingestion_agent.py   sequential ingestion agent driving IngestionWorkflow
│   └── utils/graph_layout.py   deterministic 3D layouts (solar + Fruchterman–Reingold)
├── tests/
│   ├── unit/                   46 pytest modules (485 tests; conftest only pins `LLM_PROVIDER`), e.g.
│   │   ├── test_vault_migration.py          one-time vault sweep (v3: stray files moved under attachments/, links relativised, legacy blocks wrapped)
│   │   ├── test_upload_folder.py            uploads land in attachments/<note folder>/; traversal rejected
│   │   ├── test_vault_folders.py            folder moves, the attachments/ boundary, link stripping
│   │   ├── test_desktop_runtime.py          Meili key, atomic boot status, Firefly .env quoting
│   │   ├── test_note_created_at.py          created_at validation on note create/update/ingest
│   │   └── test_ingestion_community_names.py community naming JSON + member-fit check
├── requirements.txt            base deps (FastAPI, SQLAlchemy, kuzu, qdrant, meilisearch, llama-cpp-python, …)
├── requirements-dev.txt        pytest + pytest-asyncio (pinned)
├── requirements-multimodal.txt torch / transformers ≥ 5.7 / qwen-vl-utils (installed on demand)
├── .env.example                fully commented configuration reference
└── .pylintrc
```

Layering rule (enforced by convention, not tooling): `core` → `models`/`schemas` → `services` → `workflows` → `api`. `api_desktop.py` predates the split into `api/` and still holds setup, finance, notes-graph and `/vault-files` routes.

---

## 3. `frontend/`

```
frontend/
├── index.html              Vite entry
├── vite.config.ts          React Compiler plugin, `@` alias, dev proxy (/api/v1, /vault-files, /health → 17401)
├── package.json            Vite, React 19.2, react-router, Tailwind 4, CodeMirror 6, three + react-force-graph
├── tsconfig.json / eslint.config.mjs / postcss.config.mjs
├── public/                 logo.png, logo-icon.png, favicon.ico
├── dist/                   GIT-IGNORED `npm run build` output, served by the API
└── src/
    ├── main.tsx            React root
    ├── App.tsx             KBProvider > ChatProvider > Sidebar + CommandPalette + main; lazy react-router routes (/ → /notes)
    ├── app/
    │   ├── globals.css
    │   ├── chat/page.tsx   Chat (async polling, conversations, citations)
    │   ├── notes/          Notes workspace
    │   │   ├── page.tsx
    │   │   ├── _components/  VaultFolderTree, NotesSidebar, NoteEditorHeader, FilePreviewModal, …
    │   │   ├── _hooks/       useNotesPageController (hub), useVaultTree, useNoteAutosave, useNoteIngest,
    │   │   │                 useNoteMedia, useAttachmentJobs, useNotesList, useNoteSelection, useNoteBatchSelection, useWikilinkPreview
    │   │   └── _lib/         wikilinks, folder-tree, processing-status, rewrite-vault-urls, parse-note-attachments, media-recorder, …
    │   ├── notes-graph/page.tsx  wikilink graph
    │   ├── graph-3d/page.tsx     3D entity graph (`/graph` redirects here)
    │   ├── finance/page.tsx      Firefly-backed finance workspace
    │   ├── kb/page.tsx           Knowledge base manager
    │   ├── models/               Model catalogue page (+ `_components/ModelPicker.tsx`)
    │   ├── setup/page.tsx        First-run / model download / paths
    │   └── settings/page.tsx     Provider + model settings
    ├── components/
    │   ├── sidebar.tsx, command-palette.tsx, settings-shell.tsx, ai-limited-banner.tsx, system-status-indicator.tsx
    │   ├── connected-notes-panel.tsx, entity-detail-panel.tsx, segmented-note-content.tsx
    │   ├── blob-media-player.tsx, shader-background.tsx
    │   ├── markdown-editor/   MarkdownNoteEditor + CodeMirror extensions (wikilink, entity, media embed, …)
    │   ├── graph3d/           Graph3DCanvas, HUD, NodeDetailModal, GraphSearchOverlay, hooks, nodeColors
    │   └── finance/           tabs/, hooks/ (useFinanceWorkspace, useFinanceMutations), lists, panels
    └── lib/
        ├── api.ts             fetch client — every backend call
        ├── types.ts           shared TS types (Note, ChatStatus, KnowledgeBase, Finance*, SetupStatus, …)
        ├── kb-context.tsx     current KB (localStorage `orb_current_kb`)
        ├── chat-context.tsx   chat state + polling across routes
        ├── desktop.ts         window.orbDesktop bridge helpers (pickers, restartBackend, notifyIfUnfocused)
        ├── endpoint-names.ts, models-types.ts
        ├── markdown-entities.tsx  entity-aware markdown rendering
        └── utils.ts
```

---

## 4. `desktop/`

```
desktop/
├── build.py              packaging pipeline: bundle Python + backend, build UI, seed Firefly, preflight, cargo tauri build (macOS: .app via Tauri, DMG via hdiutil)
├── shell/index.html      first-run setup page (data dir, models dir, vault → paths.json)
├── src-tauri/
│   ├── tauri.conf.json   identifier com.josetseph.orb, version, no static windows, withGlobalTauri
│   ├── Cargo.toml / build.rs
│   ├── capabilities/     shell.json (bundled page), remote-ui.json (http://127.0.0.1:17401)
│   ├── icons/
│   └── src/
│       ├── main.rs       builder, plugins, run-event handling (Exit → stop; macOS Reopen)
│       ├── runtime.rs    paths, packaged-vs-repo layout, spawn/watch/kill the runtime, window, health poll
│       ├── commands.rs   app_state, save_setup, restart_backend
│       └── init.js       injected into every page: window.orbDesktop bridge + single-window guard
├── build/entitlements.mac.plist, build/icon.png
├── assets/logo.png
├── README.md, PACKAGING.md
└── resources/            GIT-IGNORED build.py output (backend/, frontend/, firefly/)
```

---

## 5. Git-ignored paths you will see locally

These exist on a developer machine but are not source. Do not document, lint, or edit them.

| Path | What it is | Owner |
|---|---|---|
| `desktop/resources/` | `build.py prepare` output: portable Python + site-packages + `app/`, Vite build, Firefly seed | `desktop/build.py` |
| `desktop/src-tauri/target/`, `desktop/src-tauri/gen/` | cargo build output; installers under `target/release/bundle/` | cargo / tauri |
| `frontend/node_modules/` | npm deps | npm |
| `frontend/dist/` | Vite build output served by the API | Vite |
| `backend/.venv/`, `backend/venv/` | Python venv used by `cargo tauri dev` in dev | developer |
| `backend/models/` | dev fallback MODELS_DIR | local_models / multimodal_models |
| `backend/logs/` | legacy log dir (current logs live under DATA_DIR/logs) | — |
| `data/` (repo root) | dev fallback DATA_DIR (SQLite, Kuzu, Qdrant, Meili, vaults, logs) | backend |
| `.env`, `.env.local` | secrets (API keys) | developer |
| `.context/`, `.agent/`, `.gemini/`, `.github/agents|instructions|hooks|prompts`, `.github/copilot-instructions.md` | AI-editor scratch | — |

Note: `.gitignore` force-tracks `Results*/` (with its `*.log` files), but the benchmark results now live on the `orb-testing` branch, not on `main`.

---

## 6. Where to look for a given concern

| Concern | Start here |
|---|---|
| A route's behaviour | `backend/app/api/<domain>.py` or `api_desktop.py` → [07](07-api-reference.md) |
| How a KB is created / isolated | `services/kb_registry.py` → [08](08-knowledge-bases-and-vaults.md) |
| Note save, rename, move, wikilinks | `api/notes.py`, `services/vault*.py`, `services/wikilinks.py` → [09](09-notes-wikilinks-and-vault-files.md) |
| What happens after "Ingest" | `workflows/agents/ingestion_agent.py` → `workflows/ingestion.py` → [10](10-ingestion-pipeline.md) |
| PDF / image / audio / video handling | `services/multimedia.py`, `multimodal_runtime.py` → [11](11-multimedia-enrichment.md) |
| Model download / which GGUF is loaded | `services/local_models.py`, `model_catalog.py` → [12](12-local-models-and-inference.md) |
| Prompt text, provider switching | `services/llm.py` → [13](13-llm-providers-and-prompting.md) |
| Kuzu schema / Cypher | `services/graph.py` → [14](14-graph-storage-kuzu.md) |
| Qdrant / Meilisearch payloads | `services/qdrant_service.py`, `meilisearch_service.py` → [15](15-search-indexes-qdrant-meilisearch.md) |
| Why chat answered the way it did | `services/retrieval.py`, `workflows/chat.py` → [16](16-retrieval-and-chat.md) |
| Finance | `services/firefly_service.py`, `desktop_runtime.py` (Firefly bootstrap) → [17](17-finance-firefly.md) |
| UI page | `frontend/src/app/<route>/page.tsx` → [18](18-frontend-architecture.md), [19](19-frontend-notes-editor.md), [20](20-frontend-chat-graph-and-pages.md) |
| An env var | [21](21-configuration-reference.md) |
| A file on disk under Application Support | [22](22-data-directory-layout.md) |
| A log line | `core/log.py` → [23](23-logging-and-observability.md) |
| Startup failure of the desktop app | `desktop/src-tauri/src/runtime.rs`, `backend/app/desktop_runtime.py`, `DATA_DIR/logs/backend.log` → [04](04-desktop-shell.md) |
| Building an installer | `desktop/build.py`, CI workflow → [05](05-packaging-build-and-release.md) |
| Why something is the way it is | [25](25-development-history.md), [26](26-decisions-and-constraints.md) |
