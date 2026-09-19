# Orb Documentation

Comprehensive, code-grounded documentation of the Orb repository: the Tauri desktop shell and Python desktop runtime, the FastAPI backend, the Vite + React frontend, the embedded data stores, the local-model runtime, the ingestion and retrieval pipelines, the finance integration, build/release, testing, configuration, and the project's history and locked decisions.

**Audience.** Engineers working on Orb and AI coding assistants using these files as context. Every document is written from the source code (tracked files only; `desktop/resources/` build output is excluded) and enumerates invariants, gotchas and extension points so that changes can be made safely.

**Snapshot.** Written against the working tree on 2026-09-02, which is commit `02ac9d3` (v0.2.0) plus uncommitted work in progress: per-KB LLM overrides, chunked extraction, model-load instrumentation and five new unit tests. Where a doc describes uncommitted behaviour it says so. Docs cite file paths and symbol names, never line numbers.

**Conventions.** Paths are repo-relative (`backend/app/services/graph.py`). `DATA_DIR` and `MODELS_DIR` refer to the directories chosen on the first-run setup page. `?kb=` means the knowledge-base query parameter. Mermaid diagrams render on GitHub and in most Markdown viewers.

---

## Reading order

1. [01 — Overview](01-overview.md): what Orb is, product decisions, stack.
2. [02 — System architecture](02-system-architecture.md): processes, ports, storage map, write path, read path, cross-cutting contracts.
3. [03 — Repository layout](03-repository-layout.md): every tracked directory and file, and which doc covers it.
4. Then the subsystem doc for the area you are changing (table below).
5. [26 — Decisions and constraints](26-decisions-and-constraints.md) before proposing an architectural change.
6. [27 — Development guide](27-development-guide.md) for setup, commands, conventions and recipes.

---

## Index

| # | Document | Covers |
|---|---|---|
| 01 | [Overview](01-overview.md) | Product scope, delivery model, locked product decisions, technology stack, versioning |
| 02 | [System architecture](02-system-architecture.md) | Process topology, bootstrap layers, storage map, KB isolation, ingestion and retrieval flows, model layer, frontend↔backend contract, deployment modes |
| 03 | [Repository layout](03-repository-layout.md) | Directory-by-directory map of `backend/`, `frontend/`, `desktop/`; git-ignored paths; where to look for a concern |
| 04 | [Desktop shell and runtime](04-desktop-shell.md) | Tauri shell responsibilities and files, boot sequence, `desktop_runtime.py` (ports, sidecar download/boot, Firefly bootstrap, `boot-status.json`), window guard and `window.orbDesktop` bridge, every `ORB_*` env read by the shell |
| 05 | [Packaging, build and release](05-packaging-build-and-release.md) | `desktop/build.py prepare` / `dist` pipeline, packaged layout per platform, build env vars, CI matrix, versioning, signing/notarization and auto-update state |
| 06 | [Backend core and configuration](06-backend-core-and-configuration.md) | FastAPI app, middleware, startup hooks, `Settings`, path resolution, `runtime_config.json`, database layer, ORM models, Pydantic schemas, `get_kb`, health/settings routes, AI gate |
| 07 | [API reference](07-api-reference.md) | Every HTTP route: params, bodies, responses, status codes, side effects, service calls; conventions; frontend cross-check |
| 08 | [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) | KB model and isolation, `KBContext`, registry lifecycle, vault resolution, `vault_sync`, `vault_watcher`, per-KB store fan-out |
| 09 | [Notes, wikilinks and vault files](09-notes-wikilinks-and-vault-files.md) | Note/file contract, title↔filename sync, vault operations, attachments and `/vault-files`, uploads, wikilink parsing/resolution, `note_links`, notes-graph payloads, processing status |
| 10 | [Ingestion pipeline](10-ingestion-pipeline.md) | Entry points, concurrency, the sequential ingestion agent, stage-by-stage walkthrough, chunked extraction, entity resolution, what is written to Kuzu/Qdrant/Meili, re-ingest cleanup, post-triggers, tracker |
| 11 | [Multimedia enrichment](11-multimedia-enrichment.md) | Attachment discovery, PDF/image/audio/video/document handlers, Florence/Whisper/Marlin usage, enrichment block format, temp-file rules |
| 12 | [Local models and inference](12-local-models-and-inference.md) | Model catalogue and manifest, downloads, llama.cpp runtime parameters, exclusive residency, chat/embed/rerank internals, multimodal runtime, `ORB_LLAMA_*` knobs |
| 13 | [LLM providers and prompting](13-llm-providers-and-prompting.md) | `LLMService` abstraction, providers, model resolution, structured output, retries, thinking extraction, AI gate, per-KB overrides, catalogue of every prompt |
| 14 | [Graph storage (Kuzu)](14-graph-storage-kuzu.md) | Connection management, full schema, node identity, relationship model, communities, digests, layouts, every `GraphService` method, graph/admin routes |
| 15 | [Search indexes (Qdrant, Meilisearch)](15-search-indexes-qdrant-meilisearch.md) | Collections, vector params, payload schemas, search functions, dimension sync, Meili index settings and documents, embedding service, contract tests |
| 16 | [Retrieval and chat](16-retrieval-and-chat.md) | Chat request lifecycle and polling, conversation persistence, multi-hop research loop, hybrid channels, fusion and rerank, context assembly, synthesis, tuning |
| 17 | [Finance (Firefly III)](17-finance-firefly.md) | Runtime and token, per-KB administrations, request scoping, `FireflyService` methods and mappings, finance routes, frontend workspace |
| 18 | [Frontend architecture](18-frontend-architecture.md) | Stack, conventions, build modes and rewrites, API layer, providers, desktop bridge, navigation, shared components, types, patterns |
| 19 | [Frontend notes editor](19-frontend-notes-editor.md) | Notes page controller and hooks, vault tree, autosave, ingestion controls, media, CodeMirror extensions (wikilinks, entities, media embeds), read-only rendering |
| 20 | [Frontend chat, graph and pages](20-frontend-chat-graph-and-pages.md) | Home, chat, 3D graph, 2D graph, notes graph, KB manager (incl. per-KB model panel), settings, setup |
| 21 | [Configuration reference](21-configuration-reference.md) | Every env var and alias with default, reader and effect; `paths.json` and `runtime_config.json` schemas; precedence; provider fallback chains |
| 22 | [Data directory layout](22-data-directory-layout.md) | On-disk trees for `DATA_DIR`, `MODELS_DIR`, app-support root, caches; owners; what is safe to delete |
| 23 | [Logging and observability](23-logging-and-observability.md) | Log routing table, formats, rotation, trace ids, runtime and sidecar logs, `boot-status.json`, status surfaces, debugging playbook |
| 24 | [Testing](24-testing.md) | Unit tests and fixtures, lint tooling, gaps |
| 25 | [Development history](25-development-history.md) | Commit timeline, era narratives, decision log with shas, removed/legacy paths, versions, uncommitted work in progress |
| 26 | [Decisions and constraints](26-decisions-and-constraints.md) | Settled decisions with rejected alternatives, rationale and enforcement points; removed features |
| 27 | [Development guide](27-development-guide.md) | Prerequisites, setup, run modes, commands, conventions, recipes, things never to do |
| 28 | [Glossary](28-glossary.md) | Terms and identifiers by area, legacy identifiers |

---

## Quick facts

| | |
|---|---|
| Processes | Tauri shell → `python -m app.desktop_runtime` → FastAPI 17401 (serves the UI), Qdrant 17433, Meilisearch 17470, Firefly III 17412 |
| Stores | vault `.md` + attachments (bodies), SQLite `orb.db` (metadata), Kuzu (graph), Qdrant (vectors, 3 collections/KB), Meilisearch (keyword, 1 index/KB), Firefly SQLite (finance) |
| Models | GGUF chat/embed/rerank via llama-cpp-python; Florence-2, Whisper, Marlin via transformers; all in the API process, one resident at a time |
| Isolation | `?kb=<slug>` on every data route → `KBContext` |
| Bootstrap | `paths.json` (data dir, models dir, vault, AI mode) → `desktop_runtime.py` env → `Settings` (+ `.env`, `runtime_config.json`) |
| Version | 0.3.0 (`desktop/src-tauri/tauri.conf.json`, `Cargo.toml`); `frontend/package.json` still says 0.2.0 |

---

## Maintaining these docs

- When you change behaviour, update the owning doc's section and, if a contract changed, [02](02-system-architecture.md) §10 and [26](26-decisions-and-constraints.md).
- New env vars go in [21](21-configuration-reference.md); new routes in [07](07-api-reference.md); new files in [03](03-repository-layout.md).
- Keep the "History / rationale" sections short and cite commit shas.
- Do not add line numbers; cite symbols.

---

## Discrepancies and probable bugs found while documenting

These were observed in the code while writing the docs (2026-09-02 working tree). They are recorded here as findings, not fixes; each is detailed in the owning doc.

| Area | Finding | Doc |
|---|---|---|
| Retrieval | The GGUF cross-encoder is the only ranking signal: with `RERANKER_ENABLED=false` or the reranker GGUF missing every candidate scores 0.0 < `RERANKER_SCORE_THRESHOLD`, so `hybrid_search` returns nothing and every answer is "couldn't find…". The docstring's keyword fallback does not exist. | 16 |
| Retrieval | `MAX_LOOP_ITERATIONS=3` gives at most two actual retrievals (iteration 1 only plans). | 16 |
| Chat | `GET /api/v1/chat/conversations/{id}/export` calls a non-existent `chat_store.get_messages(db, id)` → 500. `_chat_status` is never evicted. | 16, 07 |
| Ingestion | Re-ingest never deletes prior graph/vector data; `mention_count` grows. `is_similarity` / `created_at` on `SEMANTIC_REL` are never set. | 10, 14 |
| Ingestion | L1 clustering threshold comment (0.50) disagrees with code (0.35). | 10, 21 |
| Multimedia | Frontend recognises `[Video Transcript …]` but the backend writes `[Video Audio Transcript …]` / `[Video Visual Analysis …]`; Whisper runs a single 30 s window with `language="en"`. | 11, 19 |
| Notes | `POST /api/v1/kb` response omits `slug` although the client needs it for `?kb=`. `kb/empty` lacks the `DATA_DIR` containment guard that `delete_kb` has. Deleting a KB orphans its `chat_conversations`. | 08, 09 |
| Notes | Ingestion's LLM title update changes only the SQLite row; the vault file is renamed on the next autosave, not immediately. Attachments with spaces in URLs are ingested but not deleted with the note. | 09, 10 |
| Config | `KUZU_DB_PATH` and `MODELS_PATH` from env are always overwritten by code; `COMMUNITY_DETECTION_ENABLED` / `TEMPORAL_DIGESTS_ENABLED` default `False` in code although `.env.example` says `true`; `EMBEDDING_PROVIDER=openai` raises. `Settings(extra="ignore")` silently drops `STORAGE_BACKEND`, `FILES_URL`, `VIDEO_MAX_PIXELS`, `FPS*`. | 21, 06 |
| Config | Anthropic call sites use `settings.ANTHROPIC_MODEL` directly, ignoring `CHAT_MODEL` / per-KB pins. `ai_is_configured()` returns `True` for `cloud` with no key because `LLM_BASE_URL` has a default. | 13, 06 |
| Logging | `X-Request-Id` is captured but never written to log lines; `EmbeddingService` logger is not routed to a file; Qdrant/Meilisearch run with stdio ignored. | 23 |
| Finance | `_filter_summary_basic` reads `type` but accounts expose `account_type`, so `report().basic["balance-in-vault"]` is always 0. Cash accounts are never listed. No link-out to the Firefly UI exists although `firefly_url` is returned. Recurrences never fire (no cron). | 17 |
| Frontend | `getChatMessages`, `deleteChatConversation`, `getNoteStatus` never send `kb`. Chat footer model names are hard-coded. | 18, 20, 07 |
| Tests | No CI test job. `pytest` is not in `requirements.txt`. `test_relationships.py` imports a module deleted in `da75dfc`; half of `test_graph_queries.py` targets removed APIs; one `test_chat_context.py` assertion is stale; `conftest` pins `LLM_PROVIDER="lm_studio"`. | 24, 27 |
| Versioning | `FastAPI(version="0.1.0")` while packages say `0.2.0`. `.cursor/rules/architecture-decisions.mdc` is deleted in the working tree but is the only in-repo locked-decisions file. | 01, 25 |
