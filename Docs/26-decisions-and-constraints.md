# Decisions and Constraints

**What this covers:** the architectural and product decisions that are considered settled in Orb, the invariants they imply, and the rationale behind each. Many were written down originally in `.cursor/rules/architecture-decisions.mdc` (a rules file for AI editors, still present in git `HEAD` though deleted in the working tree); others are visible only in code comments and commit messages. Treat every entry here as a constraint: do not reintroduce a rejected approach without an explicit new decision.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Development history](25-development-history.md) · [Development guide](27-development-guide.md) · every subsystem doc's "Invariants" section

Format per decision: **Decision** · Rejected alternatives · Rationale / evidence · Where it is enforced.

---

## A. Product and delivery

### A1. Orb is an Electron desktop app; end users never run Docker
- Rejected: browser-only setup, `docker compose` as the install path, a hosted service.
- Rationale: the target user has personal notes, voice memos and finances on one machine; Docker was the biggest onboarding obstacle in the LifeOS era (README history, commit `3f21e08` "Ship LifeOS as a Docker-free desktop app").
- Enforced: `desktop/supervisor.js` spawns local binaries; `docker-compose.yml` header says "Contributor / optional infra stack only"; README section "Contributors — optional Docker infra".

### A2. First-run wizard collects only data dir, models dir, optional vault, AI mode
- Rejected: a full settings UI at first launch; auto-choosing directories.
- Rationale: users keep models on NAS/OneDrive; the bootstrap file must be tiny and robust. A truncated `paths.json` once looked like total data loss, hence atomic writes and "corrupt file → wizard".
- Enforced: `main.js::save-wizard` (atomic tmp+rename), `supervisor.js::needsWizard`, `core/paths.py::save_paths_file`.

### A3. Dedicated port block 17400–17470
- Rejected: 3000/8000/6333/7700 defaults.
- Rationale: coexist with the developer stacks most users of this app already run.
- Enforced: `desktop/ports.js` is the single source; everything else derives from it.

### A4. Unsigned builds do not auto-update
- Rationale: an unsigned app must not silently pull binaries from GitHub Releases.
- Enforced: `main.js::setupAutoUpdater` requires packaged + `ORB_ENABLE_UPDATER=1`.

### A5. Rename LifeOS/LiveOS → Orb, keep read-compatibility aliases
- Rationale: existing installs must keep working after the rename (`6162be2`).
- Enforced: `envFirst("ORB_X", "LIVEOS_X")` in the shell, `_env_first` in `core/paths.py`, App Support fallback dirs, `lifeos_current_kb` migration in `kb-context.tsx`. **New code writes only `ORB_*`.**

---

## B. Models and inference

### B1. Every local model runs in-process in the API
- Rejected: Ollama, LM Studio, `llama-server`, a "local-models" HTTP service, a separate Marlin service (`LOCAL_MODELS_SERVICE_URL`, `MARLIN_SERVICE_URL`, `POST …/image/describe|/audio/transcribe|/caption|/rerank`).
- Rationale: one process to supervise, no port juggling, no network hop for embeddings, easier packaging (`fbcafe7`, rules file).
- Enforced: `services/local_models.py`, `multimodal_runtime.py`; `EmbeddingService` maps `ollama`/`lm_studio` to `local` with a warning; supervisor has no sidecar ports.

### B2. Exclusive residency: one heavy model at a time
- Rejected: keeping chat + embed + rerank resident; separate processes per model.
- Rationale: consumer machines (24 GB Metal was the reference) cannot hold Gemma 4 E4B, Qwen3 embed/rerank and Florence/Whisper/Marlin simultaneously.
- Enforced: residency manager in `local_models.py` (`_unload_peers_for_gguf`, `ensure_chat_loaded`, `ensure_embed_loaded`), `_unload_multimodal_families`, `multimodal_runtime` single-family policy; `ModelLoadClock` reports load time separately so swaps are visible in stage timings.

### B3. Chat GGUF defaults: `n_ctx=16384`, `swa_full=true`, `repeat_penalty=1.12`, flash-attention opt-in, no fixed output cap
- Rejected: 32k context (OOMs with full SWA on ~24 GB Metal); compact SWA (causes Gemma 4 "ordinal loops" / "or the" repetition cascades); a fixed `ORB_LLAMA_MAX_TOKENS=10240` (silently truncated long extractions — removed as a default in the current working tree, the runtime now sizes `max_tokens` from the context left after the prompt).
- Enforced: `local_models.py` defaults + repetition-cascade detector with abort/retry; `desktop/supervisor.js` env block; `desktop/binaries/README.md`.

### B4. GGUF selection lives in the models manifest; embed dims must match Qdrant
- Rationale: switching embedding model changes vector size; collections must be resized deliberately.
- Enforced: `save_selection(..., embedding_dims=)` and `sync_embedding_infrastructure()` at startup; `POST /setup/select-chat-model` resizes.

### B5. Reranker is a cross-encoder GGUF, not coupled to embedding dims
- Rationale: rerank quality drove the "Final Implementation" results; a cross-encoder can be swapped independently of the vector space.
- Enforced: `services/reranker.py`, `LocalGgufReranker`.

### B6. Three provider axes (chat / ingestion / embeddings), plus per-KB chat+ingestion override
- Rationale: bulk extraction benefits from a cheaper or faster model than chat; embeddings must stay system-wide because Qdrant dims are shared across KBs.
- Enforced: `Settings.LLM_PROVIDER/CHAT_MODEL`, `INGESTION_PROVIDER/INGESTION_MODEL`, `EMBEDDING_*`; per-KB columns `llm_provider/llm_model/llm_ingestion_model` (working tree) with validation in `api/kb.py::update_kb_llm` (local ids must be downloaded; cloud needs a key). Embed/rerank/multimodal are **not** per-KB by design.

### B7. Cloud providers remain available but are opt-in
- Rationale: privacy-first default; some users want frontier quality.
- Enforced: `AI_SETUP_MODE` gating (`ai_gate.py`), keys only in `.env`, never in `runtime_config.json`.

### B8. Multimodal stack is one shared torch + transformers ≥ 5.7 install
- Rejected: a second transformers major just for Marlin; separate venvs per model.
- Rationale: Marlin (Qwen3.5 backbone) requires ≥ 5.7; Florence/Whisper were patched to run on 5.x rather than pinning two stacks.
- Enforced: `requirements-multimodal.txt`, `multimodal_services._MULTIMODAL_PIP`, compatibility patches in `multimodal_runtime.py`.

---

## C. Storage

### C1. Note bodies are real `.md` files in a per-KB vault; SQLite is metadata only
- Rejected: bodies in Postgres/SQLite (the original design; `notes.content` remains as a deprecated fallback), S3/RustFS object storage for attachments.
- Rationale: Obsidian compatibility, user ownership, cloud-sync friendliness, "your knowledge on your machine".
- Enforced: `note_files.persist_note_body/note_body`, `vault.py`, `vault_sync.py`; `notes.content` kept empty by `persist_note_body`.

### C2. SQLite (`DATA_DIR/orb.db`) is the desktop metadata store; Postgres is contributor-only
- Rationale: zero-install; a single file to back up.
- Enforced: `core/database.py` picks Postgres only when `DATABASE_BACKEND=postgres` and a URL is set.

### C3. Embedded Kuzu replaced Neo4j
- Rationale: no server process, embeddable in the desktop bundle, Cypher-compatible enough (`68494b7`). Labels became a `kind` property (`note | indexable | community | temporal_digest`) because Kuzu tables are static.
- Enforced: `services/graph.py` docstring "Cypher translation notes"; `kuzu==0.11.3` pin.

### C4. Meilisearch replaced Typesense
- Rationale: native Windows binary and simpler packaging (`f28d205`, `d37abd6` era).
- Enforced: `services/meilisearch_service.py`; `TYPESENSE_*` accepted as aliases; DB column `typesense_collection` retained with `meili_index` synonym.

### C5. Qdrant is the source of truth for vectors and long descriptive text; fail-closed on dimension mismatch
- Rejected: silently recreating a collection when the embedding model changes.
- Rationale: an implicit wipe destroyed user vectors mid-ingest during the audit (`f8f527f`).
- Enforced: `qdrant_service` raises on mismatch during ingest; only `sync_embedding_infrastructure` / explicit selection may recreate.

### C6. Per-KB isolation of every store, including Firefly
- Rationale: users keep work and personal vaults apart; finance data must never leak between them.
- Enforced: `KBContext`, slug-prefixed collections/indexes, per-KB Kuzu dirs, `knowledge_bases.firefly_group_id`, `_run_scoped` in `firefly_service.py`.

### C7. Vault files are never temp files
- Rationale: an early multimedia path deleted "temporary" inputs that were actually the user's attachments.
- Enforced: `multimedia.py` resolves `/vault-files/...` to disk and only deletes files it created itself.

### C8. Cleanup and deletion are contained to `DATA_DIR`
- Rationale: audit finding — a mis-set path could delete arbitrary folders.
- Enforced: KB delete / reset paths check containment before `rmtree`; `safe_vault_join` for every vault-relative path; `reveal-in-folder` allow-list in `main.js`.

---

## D. Ingestion and retrieval

### D1. Joint extraction with entity resolution against the existing graph
- Rejected: the earlier "Sub Questions" and "Looping" ingestion variants (see `Results/`).
- Rationale: benchmark reports showed better multi-hop recall with one joint pass plus dedup/merge (`559e988`, Final Implementation reports).
- Enforced: `ingestion_agent` extraction → storage nodes; entity resolution by exact normalised name against Qdrant `node_cores` (Kuzu fallback). Embedding-similarity merging and `is_similarity` edges were removed with the bi-temporal design and are not written today.

### D2. Relationship weight `edge_weight = strength×0.5 + confidence×0.3 + relevance×0.2` (1–10 scale)
- Rationale: fixed, explainable blend computed at ingest time and stored on every `SEMANTIC_REL` (it was the input to the now-removed symbolic reranker).
- Current use: written by `GraphService` (`edge_weight` merge logic keeps the first non-null value) and exposed on graph payloads; `retrieval.py` does not currently rank by it. Because the extraction prompt never asks for scores, nearly every edge carries the defaults (5/7/5 → 5.6). Keep the formula stable so existing edges stay comparable.
- Enforced: `schemas/extraction.py` comment, `services/graph.py`, `tests/unit/test_extraction_schemas.py`.

### D3. Temporal digests instead of bi-temporal relationship evolution
- Rejected (reversed): the Feb 2026 bi-temporal design (`033589d`: `valid_from/valid_to/is_active`, "evolved" relationships, `schemas/relationships.py`) and the symbolic reranker introduced with it.
- Rationale: `da75dfc` (2026-05-28) removed the evolution logic as complexity without measured benefit and added period-keyed temporal digests (`TEMPORAL_DIGESTS_ENABLED`, `TEMPORAL_DIGEST_PERIOD`) stored as summary nodes/vectors instead.
- Enforced: `SEMANTIC_REL.ingested_at/last_updated/mention_count` are live; `created_at` and `is_similarity` are never-set leftovers; `tests/unit/test_relationships.py` still imports the deleted module and fails at collection. Do not rebuild bi-temporal edges on top of these columns without a new decision.

### D4. Ingestion is FIFO by default; heavy media jobs serialised
- Rationale: local GPUs thrash when two notes contend for the same model; exclusive residency makes true parallelism counter-productive.
- Enforced: `INGESTION_PIPELINE_CONCURRENCY=1`, `MULTIMEDIA_CONCURRENCY=1`, semaphores in `IngestionWorkflow`.

### D5. Long notes are chunked for extraction rather than truncated
- Rationale: extraction JSON is ~2–3× the input; a single call overflows the context and truncates mid-JSON (working tree module `extraction_chunking.py`, tests `test_extraction_chunking.py`).
- Enforced: `chunk_token_budget`, `ORB_EXTRACTION_CHUNK_TOKENS`, merge of per-chunk `Extraction`s.

### D6. Enrichment blocks are stripped before re-ingest
- Rationale: transcripts were duplicated on every re-ingest.
- Enforced: block markers + strip step in `multimedia.py` / `ingestion.py`.

### D7. Community detection is idle-triggered, pre-emptible, and off by default
- Rejected: synchronous recompute after every note.
- Rationale: throughput; a new ingestion signals a running recompute to stop early. The algorithm is named "Leiden" throughout code, logs and UI but is implemented with scikit-learn agglomerative clustering over node embeddings (thresholds 0.25 / 0.35 / 0.75 across levels).
- Enforced: `ingestion_tracker.COMMUNITY_IDLE_SECONDS=120`, `_community_run_state_lock`; `COMMUNITY_DETECTION_ENABLED` and `TEMPORAL_DIGESTS_ENABLED` default `False` in `Settings` (`.env.example` says `true`).

### D8. Retrieval is a bounded multi-hop loop, `MAX_LOOP_ITERATIONS=3`
- Rejected: unbounded loops; single-shot vector RAG; the removed "refiner" and "benchmark mode" (`8eba91d`).
- Rationale: HotPotQA-style questions rarely improve past three iterations before KB-miss exhaustion (`core/config.py` comment; `Results/`).
- Enforced: `Settings.MAX_LOOP_ITERATIONS`, `retrieve_with_iterative_loop`.

### D9. Hybrid retrieval with cross-encoder rerank
- Rationale: graph-first hybrid retrieval (`6852528`) plus reranking (`097c822`) produced the best final-implementation numbers.
- Enforced: `hybrid_search` channels, `RERANKER_*` settings.

### D10. Async chat with polling and a single in-flight job
- Rejected: server-sent streaming; unlimited concurrent chats.
- Rationale: one resident chat model; the UI needs stage feedback over minutes-long local generations; the job must run on the loop that owns the DB session (`4408a72`, `fbcafe7`).
- Enforced: `_chat_job_lock`, `/chat/async` + `/chat/status/{id}`, `ChatProvider` polling.

### D11. External vault edits mark notes stale, never auto-ingest
- Rationale: cloud-sync bursts (hundreds of events) would otherwise trigger runaway LLM work.
- Enforced: `vault_watcher.py`, self-write suppression TTL (12 s), shared sync engine.

---

## E. Desktop shell and security

### E1. Renderer isolation and navigation lockdown
- Rationale: the renderer displays user note content and, potentially, remote images/links (`f8f527f` audit).
- Enforced: `contextIsolation`, `sandbox`, `nodeIntegration:false`; `setWindowOpenHandler` deny + `shell.openExternal`; `will-navigate` restricted to the app origin and packaged `file://` pages.

### E2. Only the wizard window may save paths
- Rationale: a compromised note renderer could otherwise repoint `DATA_DIR` and make the supervisor execute binaries from an arbitrary folder.
- Enforced: `isWizardSender` on `save-wizard` and `wizard-done`.

### E3. Children are spawned detached, with log-file fds, and reaped on quit and on next launch
- Rationale: Electron hard-exits left orphaned uvicorn/node/qdrant holding the ports; `before-quit` now awaits SIGTERM→SIGKILL and startup sweeps the port block.
- Enforced: `Supervisor._spawn`, `stopAllAsync`, `freeDesktopPorts`.

### E4. Downloads open the write stream only after HTTP 200 and verify content-length
- Rationale: GitHub → Azure redirects raced an async unlink and truncated Firefly archives mid-extract (`02ac9d3`); https→http redirects are refused for executables.
- Enforced: `download-binaries.js::downloadFile`, `firefly-runtime.js`.

### E5. Random Meilisearch master key per fresh install, file mode 0600
- Rationale: `orb-dev-key` was a shared default; kept only for installs that already have Meili data.
- Enforced: `supervisor.js::resolveMeiliMasterKey`.

### E6. Same-origin `/api/v1` rewrites for the UI, but uploads go straight to the API port
- Rationale: Next's proxy truncated large uploads at 10 MB; same-origin avoids CORS for everything else.
- Enforced: `next.config.ts` rewrites + `proxyClientMaxBodySize`, `desktop.ts::resolveApiBaseUrl`, `api.upload`.

### E7. Frontend standalone deps ship as `node_deps/`
- Rationale: electron-builder strips any folder named `node_modules` from `extraResources`.
- Enforced: `scripts/build-frontend.js`, `run-server.js`, `check-resources.js`.

### E8. Native wheels are built on the target OS in CI (no cross-compiling)
- Rationale: `llama-cpp-python` Metal/CPU builds are platform-specific; v0.1.0 Mac builds shipped broken Node helper symlinks from a mismatched runner.
- Enforced: `.github/workflows/desktop-release.yml` matrix (macos-14 arm64, macos-15-intel x64, windows-latest, ubuntu-latest).

---

## F. Finance

### F1. Firefly III embedded via portable PHP, one administration per KB
- Rejected: building a bespoke ledger; a shared Firefly instance across KBs.
- Rationale: mature double-entry accounting for free; per-KB scoping keeps vaults independent.
- Enforced: `firefly-runtime.js` (seed → `DATA_DIR/firefly`, `.env`, migrations, token), `firefly_service._run_scoped` (switches the single Orb user's active `user_group_id`, stamps `user_group_id` on every request, allow-lists ids for user-wide endpoints), `knowledge_bases.firefly_group_id`. The administration is created lazily on the first scoped finance call for a KB, not at KB creation; all finance calls are serialised by one global `asyncio.Lock` and each list spawns at least one `php -r` subprocess (~1 s Laravel boot).

### F2. Firefly's writable state lives under `DATA_DIR/firefly`, never inside the app bundle
- Rationale: bundles are read-only and replaced on upgrade; upgrades stash-and-swap the app dir while preserving the SQLite DB.
- Enforced: `firefly-runtime.js` upgrade path.

---

## G. Removed features (do not resurrect without a new decision)

| Removed | Commit | Reason |
|---|---|---|
| Feedback feature | `335a253` | unused in product |
| Refiner + benchmark mode | `8eba91d` | no measurable gain; complexity |
| Neo4j | `68494b7` | server dependency |
| Typesense | `f28d205` era | packaging; Windows |
| RustFS / S3 attachments, `/files` proxy | `3f21e08` / `fbcafe7` | vault files instead (compose keeps a `legacy-s3` profile) |
| Model HTTP sidecars (local-models, Marlin services) | `fbcafe7` | in-process runtime |
| `docker-compose.prod.yml` | Jan 2026 era | not the product path |
| Postgres as default | `3f21e08` | SQLite for desktop |

See [25-development-history.md](25-development-history.md) for the full chronology.
