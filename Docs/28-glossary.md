# Glossary

**What this covers:** definitions of the terms, identifiers and abbreviations used throughout the Orb codebase and this documentation, with pointers to where each concept is implemented. Terms are grouped by area and alphabetised within each group.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · every other doc links back here for terminology.

---

## Product and shell

| Term | Meaning | Where |
|---|---|---|
| **Orb** | The product name (since 2026-08-02, `6162be2`). Lineage: **LiveOS Brain** (Jan–Jul 2026) → **LifeOS** (`3f21e08`, desktop pivot) → **Orb** (same day). | everywhere |
| **Desktop shell** | The Tauri (Rust) application in `desktop/src-tauri` that spawns the desktop runtime, owns the window and provides native pickers/notifications. | [04](04-desktop-shell.md) |
| **Desktop runtime** | `backend/app/desktop_runtime.py` — the one child the shell spawns; sweeps ports, runs uvicorn, downloads and boots Qdrant, Meilisearch and Firefly. | [04](04-desktop-shell.md) |
| **Setup page** | First-run page (`desktop/shell/index.html`) that collects data dir, models dir, optional default vault and AI mode, and writes `paths.json`. | [04](04-desktop-shell.md) |
| **boot-status.json** | `DATA_DIR/boot-status.json`, the runtime's sidecar boot progress, surfaced by `/admin/maintenance-status` and the UI status indicator (there is no splash screen). | [04](04-desktop-shell.md) |
| **paths.json** | Bootstrap file in the OS app-support dir: `data_dir`, `models_dir`, `default_vault_path?`, `ai_setup_mode?`. | [21](21-configuration-reference.md) |
| **DATA_DIR** | Root for all mutable app data (SQLite, Kuzu, Qdrant, Meili, vaults, logs, binaries, Firefly). | [22](22-data-directory-layout.md) |
| **MODELS_DIR** | Root for GGUF files, HF snapshots and the models manifest. | [22](22-data-directory-layout.md) |
| **App Support root** | `~/Library/Application Support/Orb` (macOS), `%APPDATA%\Orb` (Windows), `~/.config/Orb` (Linux); LifeOS/LiveOS dirs are read as fallbacks. | [04](04-desktop-shell.md) |
| **Port block** | 17401 API (serves the UI) · 17412 Firefly · 17433 Qdrant · 17470 Meilisearch; 3700 is the Vite dev server only. | [04](04-desktop-shell.md) |
| **Packaged layout** | Tauri resource dir (`backend/`, `frontend/`, `firefly/`) produced by `desktop/build.py prepare`. | [05](05-packaging-build-and-release.md) |
| **build.py** | `python3 desktop/build.py prepare` (bundle Python, Vite build, Firefly seed, source stamps) and `dist` (preflight + `cargo tauri build`). | [05](05-packaging-build-and-release.md) |
| **orbDesktop bridge** | `window.orbDesktop`, injected by `src-tauri/src/init.js`: `isDesktop`, `pickDirectory`, `pickFile`, `restartBackend`, `notify`. | [18](18-frontend-architecture.md) |
| **AI setup mode** | `AI_SETUP_MODE` ∈ `none | local | cloud`. Historical: the backend derives readiness from actual configuration and no longer gates on this key; `ai_gate.derived_setup_mode()` reports it for display. | [13](13-llm-providers-and-prompting.md) |
| **runtime_config.json** | `DATA_DIR/runtime_config.json`; mutable overrides `provider`, `model`, `ingestion_model`, `base_url`, `ai_setup_mode`. | [21](21-configuration-reference.md) |

## Knowledge bases, vaults, notes

| Term | Meaning | Where |
|---|---|---|
| **Knowledge base (KB)** | An isolated partition: vault + Kuzu db + three Qdrant collections + Meili index + Firefly administration. Selected by `?kb=<slug>`. | [08](08-knowledge-bases-and-vaults.md) |
| **Slug** | Immutable `[a-z0-9_-]` KB identifier derived from the name at creation; used for vault/Kuzu dirs, Qdrant/Meili names and the `?kb=` value. Distinct from the KB `id` (UUID or literal `default`, used in SQLite and `/vault-files/<id>/…`) and the renamable display `name`. | [08](08-knowledge-bases-and-vaults.md) |
| **KBContext** | Dataclass bundling a KB's service instances (qdrant, meili, lazy graph/retrieval/ingestion/chat). | [08](08-knowledge-bases-and-vaults.md) |
| **kb_registry** | SQLite-backed registry + in-memory cache of KBs. | [08](08-knowledge-bases-and-vaults.md) |
| **Vault** | The folder of `.md` notes and attachments for one KB (`DATA_DIR/vaults/<slug>` or a user-chosen path). | [09](09-notes-wikilinks-and-vault-files.md) |
| **rel_path** | Vault-relative path of a note's `.md` file, stored on `notes.rel_path`. | [09](09-notes-wikilinks-and-vault-files.md) |
| **Note body** | The Markdown content of a note; always read from the vault file (`note_body`), never authoritative in SQLite. | [09](09-notes-wikilinks-and-vault-files.md) |
| **Title ↔ filename sync** | Renaming a note renames its file and vice versa, so `[[Title]]` links keep working (Obsidian-style). | [09](09-notes-wikilinks-and-vault-files.md) |
| **Wikilink** | `[[Title]]`, `[[folder/Title]]`, `[[Title|alias]]` links between notes; parsed by `WIKILINK_RE`; stored as `note_links` rows. | [09](09-notes-wikilinks-and-vault-files.md) |
| **Notes graph** | The graph of notes connected by wikilinks (distinct from the entity graph). | [09](09-notes-wikilinks-and-vault-files.md), [20](20-frontend-chat-graph-and-pages.md) |
| **Attachment markers** | `[📎](url)` file, `[🎤](url)` voice recording, `![alt](url)` image — discovered by ingestion. | [11](11-multimedia-enrichment.md) |
| **/vault-files/** | URL scheme `/vault-files/<kb>/<rel_path>` that serves vault files from disk. | [09](09-notes-wikilinks-and-vault-files.md) |
| **safe_vault_join** | Path-traversal guard: resolves a rel path under the vault root or raises. | [09](09-notes-wikilinks-and-vault-files.md) |
| **vault_sync** | Reconciles SQLite note rows with files on disk. | [08](08-knowledge-bases-and-vaults.md) |
| **vault_watcher** | watchdog observer for external edits; marks notes stale, never auto-ingests. | [08](08-knowledge-bases-and-vaults.md) |
| **Self-write** | A file write performed by Orb itself; recorded for ~12 s so the watcher ignores it. | [08](08-knowledge-bases-and-vaults.md) |
| **Processing stage** | Human-readable ingestion status string on `notes.processing_stage` (e.g. "Queued for ingestion"). | [10](10-ingestion-pipeline.md) |

## Ingestion and enrichment

| Term | Meaning | Where |
|---|---|---|
| **Ingestion** | The pipeline that turns a note into graph nodes, edges, vectors and keyword documents. | [10](10-ingestion-pipeline.md) |
| **Ingestion agent** | LangGraph state machine (`multimodal → extraction → storage → summarization`) in `workflows/agents/ingestion_agent.py`. | [10](10-ingestion-pipeline.md) |
| **IngestionWorkflow** | Per-KB class holding the storage logic (dedup, merge, write to Kuzu/Qdrant/Meili, post-triggers). | [10](10-ingestion-pipeline.md) |
| **Extraction** | Pydantic root schema of LLM output: `nodes`, `relationships`, `sentiment`, `title`. Tolerant of messy LLM shapes. | [06](06-backend-core-and-configuration.md) |
| **Node (schema)** | Extracted entity: `name`, free-form `type`, `type_reasoning`, `isolated_context`. | [06](06-backend-core-and-configuration.md) |
| **Isolated context** | The note-local snippet that justifies an entity; embedded into the `node_isolated_contexts` collection. | [15](15-search-indexes-qdrant-meilisearch.md) |
| **Core** | An entity's consolidated description/summary across notes; embedded into `node_cores`. | [15](15-search-indexes-qdrant-meilisearch.md) |
| **edge_weight** | `strength × 0.5 + confidence × 0.3 + relevance × 0.2` on a 1–10 scale. | [14](14-graph-storage-kuzu.md) |
| **Joint Approach** | The ingestion design (Apr 2026) that extracts nodes and relationships in one LLM pass and merges with existing entities; adopted for the final implementation. | [25](25-development-history.md) |
| **Entity resolution** | Matching a newly extracted entity to an existing node by exact normalised name (Qdrant `node_cores` payload `name` is the lookup source, Kuzu the fallback). There is no embedding-similarity matching today; `SEMANTIC_REL.is_similarity` is a never-set leftover of the removed bi-temporal design. | [10](10-ingestion-pipeline.md) |
| **Enrichment block** | Markdown appended to a note by multimedia processing (transcripts, captions, PDF text); stripped and regenerated on re-ingest. | [11](11-multimedia-enrichment.md) |
| **Florence** | Microsoft Florence-2-large vision model: captions/OCR for images and PDF pages. | [11](11-multimedia-enrichment.md), [12](12-local-models-and-inference.md) |
| **Whisper** | OpenAI whisper-large-v3-turbo: audio/video transcription. | [11](11-multimedia-enrichment.md) |
| **Marlin** | Marlin-2B (Qwen3.5 backbone) video-understanding model. | [11](11-multimedia-enrichment.md) |
| **ingestion_tracker** | Process-global tracker of in-flight ingestions; triggers a community recompute after `COMMUNITY_IDLE_SECONDS` (120 s, a constant) of idleness, and lets a new ingestion pre-empt a running recompute. | [10](10-ingestion-pipeline.md) |
| **Community** | Cluster of entities stored as `Node(kind='community')` with `MEMBER_OF`/`CONTAINS` edges and levels. Called "Leiden" in code, logs and UI, but implemented with scikit-learn agglomerative clustering over embeddings; off by default (`COMMUNITY_DETECTION_ENABLED=False` in code). | [14](14-graph-storage-kuzu.md) |
| **Temporal digest** | Periodic (month/week/year) summary node (`Node(kind='temporal_digest')`, `period_key` in Qdrant payload) built from a period's contexts; feature-flagged by `TEMPORAL_DIGESTS_ENABLED` (off by default) and used by month-scoped retrieval. | [14](14-graph-storage-kuzu.md) |

## Graph and indexes

| Term | Meaning | Where |
|---|---|---|
| **Kuzu** | Embedded property-graph database (Cypher dialect). One database directory per KB. | [14](14-graph-storage-kuzu.md) |
| **Node.kind** | `note | indexable | community | temporal_digest` — replaces Neo4j labels after the Kuzu migration (the module docstring lists only the first three). | [14](14-graph-storage-kuzu.md) |
| **Indexable** | A graph node that represents an extracted entity (as opposed to a note or community). | [14](14-graph-storage-kuzu.md) |
| **REFERENCES** | Rel table: note → entity, carries `note_id`. | [14](14-graph-storage-kuzu.md) |
| **SEMANTIC_REL** | Rel table for LLM-extracted relationships (`rel_type`, scores, `edge_weight`, temporal fields, `mention_count`, `is_similarity`). | [14](14-graph-storage-kuzu.md) |
| **Bi-temporal (historical)** | Feb 2026 design (`033589d`) where relationships carried `valid_from/valid_to/is_active` and "evolved" over time. Removed in `da75dfc` (2026-05-28) in favour of temporal digests; `SEMANTIC_REL.created_at` and `is_similarity` remain as never-set leftover columns. | [14](14-graph-storage-kuzu.md), [25](25-development-history.md) |
| **Symbolic ranking (historical)** | Graph-structure-based reranking introduced in `033589d` to replace a neural reranker; itself replaced by the current GGUF cross-encoder reranker. Not present in today's code. | [25](25-development-history.md) |
| **Solar layout / spring layout** | Deterministic 3D layouts (`compute_solar_positions`, `compute_spring_layout_3d`) stored as `pos_x/y/z` so the UI does no physics. | [14](14-graph-storage-kuzu.md) |
| **Qdrant** | Vector database; three collections per KB: `node_cores`, `node_relationships`, `node_isolated_contexts` (prefixed by slug for non-default KBs). | [15](15-search-indexes-qdrant-meilisearch.md) |
| **sync_embedding_infrastructure** | Startup check that Qdrant collection dimensions match the configured embedding model. | [12](12-local-models-and-inference.md) |
| **Meilisearch** | Keyword/BM25 search engine; one index per KB (`orb_nodes` / `<slug>_nodes`). Replaced Typesense. | [15](15-search-indexes-qdrant-meilisearch.md) |
| **Master key** | Meilisearch API key; random per install (`DATA_DIR/meili_master_key`) or `orb-dev-key` for pre-existing data. | [04](04-desktop-shell.md) |

## Retrieval and chat

| Term | Meaning | Where |
|---|---|---|
| **Hybrid search** | One retrieval iteration combining entity lookup, keyword, vector and graph expansion channels. | [16](16-retrieval-and-chat.md) |
| **Research loop / iterative loop** | `retrieve_with_iterative_loop`: up to `MAX_LOOP_ITERATIONS` (3) hybrid searches driven by generated follow-up questions. | [16](16-retrieval-and-chat.md) |
| **Potential questions** | LLM-generated sub-questions (≤ `MAX_POTENTIAL_QUESTIONS`) that steer later iterations. | [16](16-retrieval-and-chat.md) |
| **Reranker** | Cross-encoder GGUF (Qwen3-Reranker) scoring query/passage pairs by yes/no logits; keeps `RERANKER_TOP_K`. | [12](12-local-models-and-inference.md) |
| **Thinking** | Model reasoning text separated from the answer and stored on `chat_messages.thinking`. | [16](16-retrieval-and-chat.md) |
| **request_id** | Client- or server-generated id for an async chat job, polled at `/chat/status/{request_id}`. | [16](16-retrieval-and-chat.md) |
| **Conversation** | Persisted chat thread scoped to a KB; history trimmed to `CHAT_HISTORY_MAX_MESSAGES` (24). | [16](16-retrieval-and-chat.md) |
| **Citation / source** | Reference from an answer back to notes/entities used, rendered inline by the chat page. | [16](16-retrieval-and-chat.md), [20](20-frontend-chat-graph-and-pages.md) |

## Models and providers

| Term | Meaning | Where |
|---|---|---|
| **GGUF** | Quantised model file format loaded by llama.cpp. | [12](12-local-models-and-inference.md) |
| **llama-cpp-python** | Python bindings for llama.cpp used for in-process chat, embeddings and reranking. | [12](12-local-models-and-inference.md) |
| **Exclusive residency** | Only one heavy model family is loaded at a time; loading one unloads the others. | [12](12-local-models-and-inference.md) |
| **Model catalog / manifest** | `model_catalog.py` lists downloadable GGUFs; the manifest in `MODELS_DIR` records what is installed/selected. | [12](12-local-models-and-inference.md) |
| **Staging dir** | Local SSD cache (`~/Library/Caches/Orb/model-downloads`) used before moving downloads onto NAS/cloud-synced `MODELS_DIR`. | [12](12-local-models-and-inference.md) |
| **SWA / swa_full** | Sliding-window attention; `ORB_LLAMA_SWA_FULL=true` avoids Gemma 4 repetition loops. | [12](12-local-models-and-inference.md) |
| **Ordinal loop** | Failure mode where Gemma 4 repeats ordinals / "or the"; detected and retried. | [12](12-local-models-and-inference.md) |
| **Provider axes** | Independent chat, ingestion and embedding provider/model settings. | [13](13-llm-providers-and-prompting.md) |
| **local (alias)** | `LLM_PROVIDER=local` means in-process GGUF; historical alias for the former Ollama/LM Studio paths. | [13](13-llm-providers-and-prompting.md) |
| **Multimodal runtime** | `multimodal_runtime.py`: torch/transformers loader for Florence, Whisper, Marlin. | [12](12-local-models-and-inference.md) |
| **ensure_multimodal_services** | Verifies HF snapshots and can pip-install torch/transformers into the running interpreter. | [12](12-local-models-and-inference.md) |

## Finance

| Term | Meaning | Where |
|---|---|---|
| **Firefly III** | Open-source personal finance manager (Laravel) embedded via portable PHP. | [17](17-finance-firefly.md) |
| **Administration / user group** | Firefly's multi-tenant unit; Orb creates one per KB (`knowledge_bases.firefly_group_id`). | [17](17-finance-firefly.md) |
| **runtime.json** | `DATA_DIR/firefly/runtime.json` written by the shell; contains the API token and URLs the backend uses. | [17](17-finance-firefly.md) |
| **FinanceWorkspace** | Frontend/API readiness object (`exists`, `ready`, `status`, `detail`, group info). | [17](17-finance-firefly.md) |
| **php-bin** | NativePHP's portable PHP builds downloaded by `desktop_runtime.py` (or seeded from the bundle). | [04](04-desktop-shell.md) |

## Observability and testing

| Term | Meaning | Where |
|---|---|---|
| **Component log** | `DATA_DIR/logs/{api,ingestion,multimedia,chat,database,graph,llm,retrieval,finance,errors}.log` routed by logger name. | [23](23-logging-and-observability.md) |
| **X-Request-Id / trace_id** | Per-request correlation id stored in a ContextVar. | [06](06-backend-core-and-configuration.md) |

## Legacy identifiers (read-only compatibility)

| Identifier | Status |
|---|---|
| `LIVEOS_*` env vars, `LifeOS`/`LiveOS` app-support dirs | Accepted as fallbacks; never write them |
| `typesense_collection` column | Column name kept for existing DBs; holds the Meilisearch index name (`meili_index` synonym). The `TYPESENSE_*` env aliases are gone. |
| `notes.content` column | Deprecated fallback; body lives in vault |
| `/files/*` rewrite to RustFS, `STORAGE_BACKEND` | Container-era S3 storage; desktop uses vault files |
| Electron shell (`main.js`, `preload.js`, `supervisor.js`), Next.js UI server, `prepare-dist`, `node_deps`, `credentials.enc` | Replaced by the Tauri shell + `desktop_runtime.py`, the API-served Vite build, `build.py`, and the OS keychain via `keyring` (2026-09) |
| `LOCAL_MODELS_SERVICE_URL`, `MARLIN_SERVICE_URL`, `ollama`, `lm_studio` | Removed sidecar/provider paths; code warns and maps to `local` |
| `lifeos_current_kb`, `liveos_current_kb`, `window.liveosDesktop` | Migrated on read by the frontend |
| `POST /api/v1/ingest` | Legacy note-create + ingest route |
