# Repository Layout

**What this covers:** every tracked directory and file in the Orb repository, what it is for, and which documentation file describes it in depth. Use this as the map when you need to find where a behaviour lives. Build outputs and user data that are git-ignored are listed separately so they are not mistaken for source.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Desktop shell](04-desktop-shell.md) · [Packaging](05-packaging-build-and-release.md) · [Backend core](06-backend-core-and-configuration.md) · [Frontend architecture](18-frontend-architecture.md) · [Data directory layout](22-data-directory-layout.md) · [Development guide](27-development-guide.md)

---

## 1. Top level

```
Orb/
├── backend/            FastAPI + Python services (the API process)
├── frontend/           Next.js 16 UI (served by the desktop shell or next dev)
├── desktop/            Electron shell, process supervisor, packaging scripts
├── Docs/               This documentation set
├── Results/            Archived benchmark reports and logs (HotPotQA / MuSiQue experiments)
├── Platform Images/    Screenshots used by the README
├── .github/workflows/  desktop-release.yml — installer build matrix
├── .cursor/rules/      architecture-decisions.mdc (locked product decisions; deleted in the
│                       working tree at the time of writing but still in HEAD — see 26)
├── docker-compose.yml  Contributor-only infra stack (NOT the product path)
├── README.md           Product README (install, build, privacy)
├── LICENSE             MIT
└── .gitignore
```

Three runtime processes are built from three top-level source trees: the Python API from `backend/`, the UI from `frontend/`, and the Electron supervisor from `desktop/`. The desktop shell additionally downloads or bundles binaries it does not own (Qdrant, Meilisearch, PHP + Firefly III, portable Python and Node).

| Path | Purpose | Detailed doc |
|---|---|---|
| `backend/` | API, ingestion, retrieval, graph, models, finance proxy | 06 – 17, 21 – 24 |
| `frontend/` | Next.js app router UI | 18 – 20 |
| `desktop/` | Electron main process, supervisor, wizard, packaging | 04, 05 |
| `Results/` | Benchmark artefacts | 24 |
| `docker-compose.yml` | Postgres + Qdrant + Meilisearch + API + UI for contributors | 05 (§ Docker), 21 |
| `.github/workflows/desktop-release.yml` | CI: macOS arm64 / macOS x64 / Windows / Linux installers | 05 |
| `.cursor/rules/architecture-decisions.mdc` | "Locked decisions" rules file for AI editors | 26 |

---

## 2. `backend/`

```
backend/
├── app/
│   ├── main.py                 FastAPI app, middleware, startup/shutdown hooks
│   ├── api_desktop.py          Setup/paths, notes-graph (wikilinks), finance, /vault-files routes
│   ├── api/                    Domain routers (one file per domain) + shared deps
│   │   ├── __init__.py         register_all_routers()
│   │   ├── deps.py             get_kb() — resolves ?kb= to a KBContext
│   │   ├── admin.py            maintenance status, rebuild communities, digests, reset, reingest-all
│   │   ├── chat.py             sync/async chat, status polling, conversations, messages
│   │   ├── files.py            /upload and file delete
│   │   ├── graph.py            3D graph export, node detail, entity search / scan-text / note-subgraph
│   │   ├── health.py           GET / and GET /health
│   │   ├── kb.py               knowledge base CRUD, empty, delete-non-default
│   │   ├── notes.py            notes CRUD, move, ingest, status, batch delete, legacy /ingest
│   │   ├── settings.py         GET/PATCH runtime LLM settings
│   │   └── vault.py            vault move/delete/mkdir/folders/local-path
│   ├── core/
│   │   ├── config.py           pydantic Settings (all env-driven configuration)
│   │   ├── paths.py            DATA_DIR / MODELS_DIR / paths.json resolution
│   │   ├── runtime_config.py   DATA_DIR/runtime_config.json mutable overrides
│   │   ├── database.py         async SQLAlchemy engine (SQLite default, Postgres optional)
│   │   ├── log.py              component log routing under DATA_DIR/logs
│   │   └── inference_device.py Metal / CUDA / Vulkan / CPU detection for llama.cpp
│   ├── models/                 SQLAlchemy ORM (metadata only — bodies live in vault .md)
│   │   ├── note.py             notes
│   │   ├── kb.py               knowledge_bases (incl. firefly_group_id)
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
│   │   ├── local_storage.py    /vault-files URL ↔ vault path mapping
│   │   ├── graph.py            Kuzu GraphService (schema, writes, queries, communities, digests)
│   │   ├── qdrant_service.py   Qdrant collections (cores / relationships / isolated contexts)
│   │   ├── meilisearch_service.py  per-KB keyword index
│   │   ├── embedding.py        in-process GGUF embeddings
│   │   ├── reranker.py         in-process GGUF cross-encoder reranker
│   │   ├── retrieval.py        hybrid multi-hop retrieval loop
│   │   ├── chat_store.py       conversation / message persistence
│   │   ├── llm.py              multi-provider LLMService (local / OpenAI / Gemini / Anthropic / HF)
│   │   ├── local_models.py     GGUF download + llama-cpp-python runtime + exclusive residency
│   │   ├── model_catalog.py    resource-aware catalogue of chat / embed / rerank GGUFs
│   │   ├── multimodal_runtime.py   in-process Florence-2 / Whisper / Marlin
│   │   ├── multimodal_models.py    HF snapshot downloads into MODELS_DIR
│   │   ├── multimodal_services.py  readiness + on-demand pip install of torch/transformers
│   │   ├── multimedia.py       attachment discovery + PDF / image / audio / video / doc enrichment
│   │   ├── ingestion_tracker.py    ingestion bookkeeping + idle-triggered Leiden recompute
│   │   ├── firefly_service.py  Firefly III HTTP client, per-KB administration scoping
│   │   └── ai_gate.py          AI_SETUP_MODE gating
│   ├── workflows/
│   │   ├── ingestion.py        IngestionWorkflow — extraction, graph persistence, embedding, indexing
│   │   ├── chat.py             ChatWorkflow — research loop + attribution
│   │   └── agents/ingestion_agent.py   LangGraph state machine driving IngestionWorkflow
│   └── utils/graph_layout.py   deterministic 3D layouts (solar + Fruchterman–Reingold)
├── scripts/run_community_detection.py   CLI: full Leiden rebuild
├── tests/
│   ├── unit/                   pytest contract tests (conftest stubs Kuzu / Qdrant / Meili / LLM)
│   └── benchmark/              HotPotQA / MuSiQue harness: fetch_notes, prepare_dataset, evaluate
├── requirements.txt            base deps (FastAPI, SQLAlchemy, kuzu, qdrant, meilisearch, llama-cpp-python, …)
├── requirements-multimodal.txt torch / transformers ≥ 5.7 / qwen-vl-utils (installed on demand)
├── .env.example                fully commented configuration reference
├── Dockerfile / .dockerignore  contributor container image
└── .pylintrc
```

Layering rule (enforced by convention, not tooling): `core` → `models`/`schemas` → `services` → `workflows` → `api`. `api_desktop.py` predates the split into `api/` and still holds setup, finance, notes-graph and `/vault-files` routes.

---

## 3. `frontend/`

```
frontend/
├── next.config.ts          standalone output, rewrites (/api/v1, /vault-files, /health, /files), 512 MB proxy body
├── package.json            Next 16.2, React 19.2, Tailwind 4, CodeMirror 6, three + react-force-graph
├── tsconfig.json / eslint.config.mjs / postcss.config.mjs
├── Dockerfile              contributor container image
├── public/                 logo.png, logo-icon.png, favicon.ico
└── src/
    ├── app/
    │   ├── layout.tsx      root layout: KBProvider > ChatProvider > Sidebar + main + AiLimitedBanner
    │   ├── globals.css
    │   ├── page.tsx        Home
    │   ├── chat/page.tsx   Chat (async polling, conversations, citations)
    │   ├── notes/          Notes workspace
    │   │   ├── page.tsx
    │   │   ├── _components/  VaultFolderTree, NotesSidebar, NoteEditorHeader, FilePreviewModal, …
    │   │   ├── _hooks/       useNotesPageController (hub), useVaultTree, useNoteAutosave, useNoteIngest,
    │   │   │                 useNoteMedia, useNotesList, useNoteSelection, useNoteBatchSelection, useWikilinkPreview
    │   │   └── _lib/         wikilinks, folder-tree, processing-status, rewrite-vault-urls, media-recorder, …
    │   ├── notes-graph/page.tsx  wikilink graph
    │   ├── graph-3d/page.tsx     3D entity graph
    │   ├── graph/page.tsx        2D graph
    │   ├── finance/page.tsx      Firefly-backed finance workspace
    │   ├── kb/page.tsx           Knowledge base manager
    │   ├── setup/page.tsx        First-run / model download / paths
    │   └── settings/page.tsx     Provider + model settings
    ├── components/
    │   ├── sidebar.tsx, ai-limited-banner.tsx, system-status-indicator.tsx
    │   ├── connected-notes-panel.tsx, entity-detail-panel.tsx, segmented-note-content.tsx
    │   ├── blob-media-player.tsx, shader-background.tsx, suppress-three-warnings.tsx
    │   ├── markdown-editor/   MarkdownNoteEditor + CodeMirror extensions (wikilink, entity, media embed, …)
    │   ├── graph3d/           Graph3DCanvas, HUD, NodeDetailModal, GraphSearchOverlay, hooks, nodeColors
    │   └── finance/           tabs/, hooks/ (useFinanceWorkspace, useFinanceMutations), lists, panels
    └── lib/
        ├── api.ts             axios client — every backend call
        ├── types.ts           shared TS types (Note, ChatStatus, KnowledgeBase, Finance*, SetupStatus, …)
        ├── kb-context.tsx     current KB (localStorage `orb_current_kb`)
        ├── chat-context.tsx   chat state + polling across routes
        ├── desktop.ts         window.orbDesktop bridge helpers
        ├── markdown-entities.tsx  entity-aware markdown rendering
        └── utils.ts
```

---

## 4. `desktop/`

```
desktop/
├── main.js               Electron main: windows, IPC, navigation lockdown, boot, quit
├── supervisor.js         Spawns Qdrant, Meilisearch, Firefly, uvicorn, Next; env injection; port sweep
├── paths.js              dev vs packaged path contract; python/node discovery; App Support root
├── ports.js              17400 UI / 17401 API / 17412 Firefly / 17433 Qdrant / 17470 Meili
├── preload.js            contextBridge → window.orbDesktop
├── download-binaries.js  Qdrant + Meilisearch release download into DATA_DIR/bin/<platform>
├── firefly-runtime.js    PHP runtime + Firefly seed → DATA_DIR/firefly, .env, migrations, token
├── splash.html           boot status window
├── wizard.html           first-run wizard (data dir, models dir, vault, AI mode)
├── package.json          electron-builder config, scripts
├── build/entitlements.mac.plist
├── assets/logo.png, build/icon.png
├── binaries/README.md    (binaries themselves are downloaded, not committed)
├── scripts/
│   ├── prepare-dist.js   bundle-python → build-frontend → bundle-node → prefetch-firefly
│   ├── bundle-python.js  python-build-standalone + pip install into resources/backend/python
│   ├── build-frontend.js next build (standalone) → resources/frontend (node_deps, run-server.js)
│   ├── bundle-node.js    portable Node → resources/node
│   ├── prefetch-firefly.js / prefetch-binaries.js
│   ├── check-resources.js  predist gate
│   └── notarize.js       afterSign hook (APPLE_* env)
├── README.md, PACKAGING.md
└── resources/            GIT-IGNORED build output (bundled python, frontend, node, firefly)
```

---

## 5. Git-ignored paths you will see locally

These exist on a developer machine but are not source. Do not document, lint, or edit them.

| Path | What it is | Owner |
|---|---|---|
| `desktop/resources/` | prepare-dist output: portable Python + site-packages, Next standalone, Node, Firefly seed | `desktop/scripts/*` |
| `desktop/dist/` | electron-builder installers | electron-builder |
| `desktop/node_modules/`, `frontend/node_modules/` | npm deps | npm |
| `frontend/.next/`, `frontend/out/`, `frontend/build/` | Next build cache/output | Next |
| `backend/.venv/`, `backend/venv/` | Python venv used by `npm start` in dev | developer |
| `backend/models/` | dev fallback MODELS_DIR | local_models / multimodal_models |
| `backend/logs/` | legacy log dir (current logs live under DATA_DIR/logs) | — |
| `data/` (repo root) | dev fallback DATA_DIR (SQLite, Kuzu, Qdrant, Meili, vaults, logs) | backend + compose volumes |
| `backend/tests/benchmark/.cache/`, `hotpotqa_notes/`, `musique_notes/`, `results/`, `.prepare_progress.json` | benchmark artefacts | benchmark harness |
| `.env`, `.env.local` | secrets (API keys) | developer |
| `.context/`, `.agent/`, `.gemini/`, `.github/agents|instructions|hooks|prompts`, `.github/copilot-instructions.md` | AI-editor scratch | — |

Note: `.gitignore` force-tracks `Results*/` even though it contains `*.log` files.

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
| Finance | `services/firefly_service.py`, `desktop/firefly-runtime.js` → [17](17-finance-firefly.md) |
| UI page | `frontend/src/app/<route>/page.tsx` → [18](18-frontend-architecture.md), [19](19-frontend-notes-editor.md), [20](20-frontend-chat-graph-and-pages.md) |
| An env var | [21](21-configuration-reference.md) |
| A file on disk under Application Support | [22](22-data-directory-layout.md) |
| A log line | `core/log.py` → [23](23-logging-and-observability.md) |
| Startup failure of the desktop app | `desktop/supervisor.js` → [04](04-desktop-shell.md) |
| Building an installer | `desktop/scripts/`, CI workflow → [05](05-packaging-build-and-release.md) |
| Why something is the way it is | [25](25-development-history.md), [26](26-decisions-and-constraints.md) |
