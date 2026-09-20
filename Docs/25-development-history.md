# Development History

**What this covers.** A chronological reconstruction of how Orb evolved from its first commit (2026-01-17, "Working Version 1", a Docker-composed research prototype called LifeOS) to the current Docker-free Tauri desktop product. It is built entirely from the git history of `/Users/josetseph/Projects/Technical/personal/Orb` (135 commits on `main` at the time of writing; the timeline table in §1 ends at `02ac9d3`, and the Tauri migration, the 1.0.0 cleanup passes and the release prep are covered in §5–§6): commit messages, per-commit file stats, key diffs, and the histories of `README.md`, `docker-compose*.yml`, `backend/requirements.txt`, and the deleted `.cursor/rules/architecture-decisions.mdc`. It records every architectural decision point (what was chosen, what was rejected, and the evidence at the time), the subsystems that were removed, and the legacy aliases/dead paths still visible in the code so that engineers and AI assistants do not resurrect them.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Desktop shell](04-desktop-shell.md) · [Packaging, build and release](05-packaging-build-and-release.md) · [Backend core and configuration](06-backend-core-and-configuration.md) · [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) · [Local models and inference](12-local-models-and-inference.md) · [Graph storage (Kuzu)](14-graph-storage-kuzu.md) · [Search indexes](15-search-indexes-qdrant-meilisearch.md) · [Retrieval and chat](16-retrieval-and-chat.md) · [Finance (Firefly)](17-finance-firefly.md) · [Testing](24-testing.md) · [Decisions and constraints](26-decisions-and-constraints.md) · [Glossary](28-glossary.md)

## How to read this document

- Section 1 is the commit timeline through `02ac9d3` (the first 70 commits) with a category tag and one-line significance.
- Section 2 tells the story era by era (five eras), with the key diffs that mark each transition.
- Section 3 is the decision log: one entry per architectural decision, with date, commit, alternatives rejected, and rationale/evidence.
- Section 4 lists removed subsystems and legacy paths still visible in the code today.
- Section 5 is the version history (`0.1.0` -> `1.0.0`).
- Section 6 summarises the post-0.2.0 work from 2026-09-02 to 2026-09-20: per-KB overrides, the two 2026-09-19 cleanup passes and the 1.0.0 release prep.
- Section 7 lists open questions / discrepancies discovered while reconstructing this history.

Short SHAs are cited throughout; `git show <sha>` in the repo resolves them. Dates are author dates (`--date=short`). Categories used in the timeline: **infra** (stack/deploy), **retrieval**, **ingestion**, **graph**, **llm**, **ui**, **kb** (multi-knowledge-base), **media** (multimodal), **desktop**, **release** (CI/packaging), **security**, **perf**, **docs**, **bench** (benchmarks/experiments), **cleanup**.

## 1. Commit timeline

| Date | SHA | Title | Category | Significance |
|---|---|---|---|---|
| 2026-01-17 | `6fd0224` | Working Version 1 | infra | First commit. FastAPI backend + Next.js frontend + Docker Compose stack (Postgres, Neo4j, Qdrant, MinIO); LangGraph ingestion/chat workflows, Ollama as the LLM provider. Project name: LifeOS. |
| 2026-01-17 | `9f33165` | cleanup | cleanup | Removes scratch files from the first push. |
| 2026-01-18 | `100c61b` | Complete | infra | Fills out the first end-to-end loop (notes -> ingestion -> graph -> chat). |
| 2026-01-18 | `8a08df1` | Update docker-compose.prod.yml | infra | Adds/adjusts a separate production compose file (later deleted in `f28d205`). |
| 2026-01-19 | `8ae5dee` | Complete Version | infra | Second "complete" snapshot of the Docker-era prototype. |
| 2026-01-20 | `2d307c1` | Updates | ingestion | Iteration on ingestion prompts/services. |
| 2026-01-20 | `805cb87` | Cleanup | cleanup | Housekeeping. |
| 2026-01-20 | `4fe47d7` | Minor updates | cleanup | Housekeeping. |
| 2026-01-23 | `6852528` | Implement graph-first hybrid retrieval and 3D graph export | retrieval | First named retrieval architecture: 4-phase graph-first hybrid RAG (temporal anchors -> KG consensus -> grounding -> semantic fallback); unified vector index for distilled nodes (Concept/Entity/Task/Persona/Reference); `/api/v1/graph/export` for 3D visualisation; batch ingestion script with Obsidian content cleaning. |
| 2026-01-23 | `c6de8ac` | 3D Graph | ui | 3D force-graph page in the frontend (react-force-graph-3d). |
| 2026-01-23 | `3a7b204` | Minor updates | cleanup | Housekeeping. |
| 2026-01-26 | `1f0681f` | Working Version 3 | ingestion | Data-quality utilities, batch summarisation, standardised task statuses, unique task names, reference validation, scoring/cutoff optimisation; author note: "Need to optimize the connections in the brain for the next version". |
| 2026-01-29 | `8c133f4` | Multiple Updates - Core System Ready | llm | Multi-provider LLM support with automatic fallback and provider-agnostic structured outputs; `MULTI_PROVIDER.md`; tests moved under `backend/tests/`. Marks end of Era 1. |
| 2026-01-30 | `401e570` | Wrapping up | cleanup | Era-1 closing polish. |
| 2026-01-30 | `a592117` | Update README.md | docs | README refresh for the Jan prototype. |
| 2026-02-01 | `033589d` | Implement bi-temporal relationships and symbolic ranking | graph | Relationships gain `valid_from`/`valid_to`/`ingested_at`/`is_active` with contradiction rules and soft invalidation; neural reranking replaced by pure symbolic ranking; Community nodes; `isolated_context` fields. |
| 2026-02-05 | `b3fd891` | Benchmark testing | bench | Start of the benchmarking campaign (HotPotQA/MuSiQue). |
| 2026-02-05 | `a0fea5e` | fix | bench | Benchmark fixes. |
| 2026-02-05 | `3ab3f22` | fix 2 | bench | Benchmark fixes. |
| 2026-02-06 | `20f043b` | testing | bench | Benchmark runs. |
| 2026-02-06 | `044052a` | more tests | bench | Benchmark runs. |
| 2026-02-19 | `536cbd4` | Add HotPotQA reports and pipeline updates | bench | HotPotQA ingestion + retrieval reports; adds `ingestion_tracker`, `alias_detector`, `rerank`, query-decomposition workflow, `core/log.py` (replacing `logging_config`); resume/checkpointing; isolated_contexts storage for summary A/B testing. |
| 2026-02-20 | `83e2dcb` | testing | bench | Benchmark runs. |
| 2026-02-24 | `38c9b5b` | Optimizing process | ingestion | Ingestion throughput work during benchmarking. |
| 2026-03-04 | `3ab4f9d` | Prune scripts/tests, add results and backend updates | bench | Removes legacy reports/scripts; adds `Results.(Sub Questions Approach)` for HotPotQA and MuSiQue; `analyze_graph.py`; `batch-note-processing/.batch_progress.json`. |
| 2026-03-15 | `24459f8` | Looping approach test | retrieval | "Looping" (iterative multi-hop) retrieval experiment. |
| 2026-04-18 | `559e988` | Add Joint Approach ingestion, services, and UI | retrieval | "Joint Approach": iterative refinement + graph-expansion retrieval; adds feedback feature, Qdrant service, Elasticsearch service, reranker, Tavily; Alembic migrations; init/reset scripts for Elasticsearch/Qdrant/Neo4j/MinIO/Postgres; removes `alias_detector`. |
| 2026-04-19 | `097c822` | Improve LLM/extraction, reranker & retrieval logic | llm | Default `LLM_PROVIDER` -> ollama; robust extraction-schema normalisation; reranker v2/v3 response handling; reranker run early and cut to top-20. |
| 2026-05-07 | `68494b7` | Finalize Final Implementation: Kuzu/Typesense migration | infra | **Neo4j -> embedded Kuzu; Elasticsearch -> Typesense**; `domain` field removed from notes (Alembic); `FINAL_IMPLEMENTATION_INGESTION_REPORT.md`; legacy Elasticsearch modules removed; several 3D graph components removed. |
| 2026-05-10 | `6ed2eb0` | Adding logs for test results | bench | Benchmark logs. |
| 2026-05-13 | `2655bb8` | Add README; reorganize results and update stack | docs | Comprehensive README; "Penultimate Implementation" -> "Final Implementation" results rename; obsolete ingestion workflow removed; frontend eslint/service-badge. |
| 2026-05-13 | `6b6bd54` | Restoring accidental deletion of config file | cleanup | Restores a config file dropped in `2655bb8`. |
| 2026-05-13 | `09e35e3` | Add ingestion-specific LLM clients & 'local' alias | llm | `local` provider alias for OpenAI-compatible servers; `INGESTION_*` config keys and separate ingestion clients (`ingestion_generate`, `ingestion_extract_structured`); `CHAT_MODEL`/`INGESTION_MODEL` fallbacks. |
| 2026-05-15 | `335a253` | Remove feedback feature; add linting & refactors | cleanup | **Feedback feature removed** (model/schema/service/endpoint + migration); `.pylintrc`; storage default MinIO -> RustFS. |
| 2026-05-18 | `e6ce10d` | Final Tests | bench | Final benchmark test outputs. |
| 2026-05-18 | `8eba91d` | Remove benchmark mode and refiner; update messages | cleanup | **`BENCHMARK_MODE` and iterative Refiner node removed**; router renamed `should_route_after_extraction`. |
| 2026-05-18 | `d37abd6` | Add multi-knowledge-base (KB) support | kb | `KBRegistry`/`KBContext`; `kb_id` on notes; per-KB Kuzu path, Qdrant collections, Typesense collection; `get_kb` dependency and `?kb=<slug>`; KB CRUD endpoints; KB context hook in frontend. |
| 2026-05-19 | `2c10d8d` | Improve KB handling, Qdrant, LLM async, ingestion | kb | Orphan-entity cleanup on note delete; `find_nodes_by_exact_names`; `_ensure_collections`; blocking SDK calls wrapped in `asyncio.to_thread`; per-KB workflow instances threaded through agent state; `isHydrated` gating in frontend. |
| 2026-05-19 | `acb19a5` | Audio transcoding, chat context, segmented notes | media | ffmpeg transcode WebM/OGG/Opus -> AAC/M4A; `_reason_step` model-thinking extraction; `ChatProvider`; `SegmentedNoteContent` (text/image/pdf/audio segments); `BENCHMARK_MODE` re-added as a config flag. |
| 2026-05-19 | `948aa33` | final touches | cleanup | Polish. |
| 2026-05-19 | `174be4f` | Update README.md | docs | README tweak. |
| 2026-05-19 | `b71888c` | Add platform screenshots and update README | docs | Screenshots; single `scripts/init_local.py`; Ollama/LM Studio/cloud model setup notes. |
| 2026-05-19 | `f28d205` | Make Docker service defaults and compose updates | infra | Docker-first defaults (hosts `qdrant`, `typesense`, `rustfs`, `postgres`); backend/init/frontend compose services; **`docker-compose.prod.yml` deleted**; tokenizers pinned 0.22.2. |
| 2026-05-20 | `28ea18e` | Refactor LLM, services, and graph layout | llm | `get_chat_model`/`get_ingestion_model`/`init_clients`; `_LazyLLMService` proxy defers torch/provider clients; heavy imports deferred; `OPENAI_MODEL_REASONING` removed. |
| 2026-05-21 | `682755f` | Add note date to extraction and mark processed | ingestion | Note `created_at` injected into extraction content; `processed=True` also clears `failed`. |
| 2026-05-22 | `6365686` | Add entity mention highlighting & editor | ui | `/graph/entities/search` and `/graph/entities/scan-text`; `EntityMentionEditor`, `EntityDetailPanel`, `entity://` pseudo-links. |
| 2026-05-28 | `da75dfc` | Add temporal digest features and admin APIs | graph | Temporal digest nodes; admin endpoints (`/maintenance-status`, `/build-temporal-digests`, `/reset-ingestion-data`, `/reingest-all`); `COMMUNITY_DETECTION_ENABLED`, `TEMPORAL_DIGESTS_ENABLED`, `TEMPORAL_DIGEST_PERIOD`; **bi-temporal relationship-evolution logic and `schemas/relationships.py` removed**; `analyze_query` gains `date_filter`/`period_filter`. |
| 2026-06-10 | `3a3ece2` | Add video processing, proxying, and CORS support | media | Marlin video captioning + Whisper combined; `CORS_ORIGINS`/`CORS_ALLOW_ORIGIN_REGEX`; Next.js rewrites for `/api/v1`, `/health`, `/files`; `FILES_URL` same-origin (`/files/liveos-assets`); transformers/torch bumps; `av`, `qwen-vl-utils`, `torchcodec`. |
| 2026-06-11 | `1986a4b` | Add PDF visual extraction and refactor image descr | media | Florence descriptions for image-heavy/scanned PDF pages via PyMuPDF rendering; `PDF_VISUAL_*` settings. |
| 2026-06-11 | `332ec21` | Update requirements.txt | infra | Dependency pin adjustments. |
| 2026-06-12 | `a8587e6` | Add local model services and ingestion status | media | **Heavy models moved out of process into HTTP sidecars** (`local-models`, `marlin` services; `LOCAL_MODELS_SERVICE_*`, `MARLIN_SERVICE_*`); `processing_stage`/`processing_model` on notes; `/chat/status`; LLM default device -> CPU. (Reverted to in-process in `3f21e08`.) |
| 2026-06-12 | `4408a72` | Add async chat polling and job handling | retrieval | `/api/v1/chat/async` + `/chat/status/{request_id}`; threadpool + lock serialises chat jobs; `query_all` per-collection filters and `day_only`. |
| 2026-06-12 | `80dc440` | Update chat.py | retrieval | Follow-up fix to chat job handling. |
| 2026-07-02 | `ac7f4e4` | Add persistent chat conversations with follow-up context. | retrieval | KB-scoped conversation threads persisted (Postgres at the time); follow-up query rewriting from recent history; conversation create/switch/delete UI. First Cursor co-authored commit. |
| 2026-08-02 | `3f21e08` | Ship LifeOS as a Docker-free desktop app with in-app data cleanup. | desktop | **The big pivot**: Electron supervisor, in-process GGUF models via llama-cpp-python, SQLite, Meilisearch, `.md` vaults, Firefly III; Ollama/sidecar path removed; batch note delete and KB/Firefly wipe controls. |
| 2026-08-02 | `6162be2` | Rebrand LifeOS / LiveOS to Orb across product and docs. | desktop | Rename app, env prefixes, paths, GitHub links; `LIFEOS_*`/`LIVEOS_*` env and path fallbacks retained. |
| 2026-08-02 | `845fd47` | Fix desktop release build: restore port URL helpers and use Node 24. | release | Restores port URL helpers lost in the rebrand; CI on Node 24. |
| 2026-08-02 | `899ddb4` | Harden desktop release: author metadata, Node deps, Intel Mac runner. | release | electron-builder author metadata; `node_deps` bundling approach; separate Intel Mac runner. |
| 2026-08-02 | `2c3518a` | Add Linux AppImage job to desktop release CI. | release | Linux AppImage target. |
| 2026-08-02 | `45fcca5` | Fix Mac Gatekeeper breakage and tighten per-arch desktop CI. | release | Gatekeeper fix for unsigned builds; per-arch CI matrix. |
| 2026-08-03 | `fbcafe7` | Align codebase with Orb desktop product and drop legacy Docker-era paths. | cleanup | Backend API modularised; **Alembic, Typesense, Tavily, Docker, bulky benchmark corpora removed**; packaging slimmed around vault-first local indexes. |
| 2026-08-04 | `2e3c93b` | Fix notes page infinite refresh that blocked title and content saves. | ui | Stabilises circular hook bridges; preserves dirty edits. |
| 2026-08-04 | `38d6038` | Fix Windows Firefly prefetch by extracting PHP zips without system Python. | release | PowerShell `Expand-Archive` with `tar` fallback in prepare-dist. |
| 2026-08-06 | `f8f527f` | Harden security and fix data-loss and perf issues from full-codebase audit. | security | KB delete contained to app-provisioned paths; slug sanitising; LLM fallback isolated from singleton; SSRF/oversize guards on ingest downloads; Firefly scope caching; models resident across batch ingest; Kuzu/vault work off event loop; desktop IPC sender validation, navigation guards, graceful child shutdown, HTTPS-only capped-redirect downloads. |
| 2026-08-06 | `b84ca73` | Speed up chat, notes UI, and graph writes from deferred audit work. | perf | Shared markdown entity-link pipeline; recent-message scanning with id-keyed cache; request cancellation; virtualised notes sidebar; viewport-limited CodeMirror decorations; batched Meili community updates and Kuzu position writes. |
| 2026-08-06 | `b35d612` | Add Obsidian-style [[ wikilink autocomplete with path disambiguation. | ui | `[[` autocomplete with folder hints; click-to-create missing notes; exact vault paths win in resolvers. |
| 2026-08-06 | `72413b9` | Keep note titles and vault filenames in sync for Obsidian-style linking. | ui | Retitle renames the `.md` and rewrites vault wikilinks; autocomplete labels prefer display title. |
| 2026-08-07 | `8de5cda` | Speed up ingestion and retrieval with batching and parallel I/O. | perf | Batched embeds/upserts/Kuzu writes; concurrent entity/BM25/vector search; query-analysis cache (per day). |
| 2026-08-07 | `e14dc67` | Bump Orb to 0.2.0 for ingest/retrieval speedups and wikilink UX. | release | Version 0.1.0 -> 0.2.0. |
| 2026-08-07 | `02ac9d3` | Fix GitHub release download race that wiped Firefly archives mid-extract. | release | `createWriteStream` deferred until HTTP 200 so a 302 close/unlink cannot delete the follow-up download. HEAD at time of writing. |

## 2. Era narratives

### Era 1 — January 2026: "LiveOS Brain", the Docker-composed prototype (`6fd0224` → `a592117`)

**Starting stack (first commit `6fd0224`, 2026-01-17, 81 files, 15.8k lines).** The project began as *LiveOS Brain*, described in its README as "a multimodal, graph-based personal memory system ... a living ontology (knowledge graph) of your life", built on a "Polyglot Persistence" model:

| Concern | Jan 2026 choice | Evidence |
|---|---|---|
| Relational metadata | **Postgres** (`postgres:latest`, container `liveos_db`, host port 5433), SQLAlchemy + Alembic (`aaf000e4972c_initial_schema`, `6d67ab8c8e21_add_processed_field`) | `docker-compose.yml@6fd0224`, `backend/alembic/` |
| Graph + vectors | **Neo4j** community 2025.01 with APOC + GDS plugins (container `liveos_brain`, 7474/7687); vectors stored in Neo4j | `neo4j==6.1.0` in `requirements.txt@6fd0224` |
| Files | **MinIO** (container `liveos_storage`, 9000/9001), via `backend/app/utils/bucket_storage.py` | `minio==7.2.20`, `boto3` |
| LLM | **Ollama** custom model (`backend/Architect.modelfile`, "Knowledge Architect – Gemma3 12B"); OpenAI/Anthropic/Gemini SDKs already pinned | README mermaid diagram |
| Embeddings | Qwen3 Embedding 8B via transformers (`reset_index_mrl.py` implies Matryoshka dims) | `backend/app/scripts/reset_index_mrl.py` |
| Multimedia | Whisper (`openai-whisper`, `faster-whisper`), "DeepSeek OCR" for PDFs/images, `opencv-python`, `timm` | `backend/app/services/multimedia.py` (322 lines) |
| Reranking | `rerankers==0.10.0` | requirements |
| Orchestration | **LangGraph 1.0.6** ingestion agent (`workflows/agents/ingestion_agent.py`) and chat workflow | present from day 1 and still present today |
| Frontend | Next.js app with `chat/`, `graph/`, `notes/` pages, shader background, custom cursor, grain overlay | `frontend/src/app/*` |
| Deploy | `docker-compose.yml` (infra only) + `docker-compose.prod.yml` (142 lines, full stack) | both in first commit |

The first commit also checked in personal test uploads under `backend/data/uploads/` (a transcript PDF, a 13 MB JPG, a voice note) and a `development_process.md` — the author's own running log of "architectural evolution and model choices" (removed in `6ed2eb0`, 2026-05-10).

**Iteration to "Core System Ready".**

- `6852528` (01-23) is the first *named* retrieval design: a 4-phase **graph-first hybrid RAG** (temporal anchors → knowledge-graph consensus → grounding → semantic fallback), a unified vector index over "distilled knowledge nodes" (Concept, Entity, Task, Persona, Reference), the `/api/v1/graph/export` endpoint for 3D visualisation (`c6de8ac` adds the react-force-graph-3d page), and a batch ingestion script that cleans Obsidian content — the earliest sign of the later Obsidian-compatibility direction.
- `1f0681f` (01-26, "Working Version 3") added data-quality utilities (`utils/data_validation.py`, deleted later in `68494b7`), batch summarisation, standardised task statuses, unique task names, reference validation and scoring/cutoff strategies; the message ends "Need to optimize the connections in the brain for the next version".
- `8c133f4` (01-29, "Core System Ready") introduced **multi-provider LLM support with automatic fallback and provider-agnostic structured outputs** (`services/llm.py` grew by ~485 lines; `MULTI_PROVIDER.md` added; `backend/.env.example` created with 117 lines), added `schemas/relationships.py` (306 lines, later deleted in `da75dfc`), moved ad-hoc `backend/test_*.py` files under `backend/tests/`, and deleted the Jan research notes (`DATA_QUALITY_PREVENTION.md`, `DYNAMIC_CUTOFF_RESULTS.md`, `OPTIMIZATION_RESULTS.md`, `RETRIEVAL_ANALYSIS.md`, `findings/test_results.md` — a 2.4k-line Gemini-vs-Gemma comparison). The README at this point pitched a **multi-mode** system (Personal Journal / Academic PKM / Creative) with automatic "domain" detection per note — the `domain` column that was later dropped (`68494b7`).

**What survives from Era 1 today:** FastAPI + Next.js split, LangGraph ingestion agent and chat workflow, the Concept/Entity/Task/Persona/Reference node vocabulary, Qdrant-style vector-first retrieval (via Neo4j then), multi-provider LLM abstraction, `/api/v1/graph/export`. **What does not:** Postgres, Neo4j, MinIO, Ollama modelfile, `domain` classification, the multi-mode pitch, `docker-compose.prod.yml`.

### Era 2 — February to April 2026: benchmarking and retrieval approaches (`033589d` → `097c822`)

This era is research, not product. Twelve of its sixteen commits are titled "testing", "fix", "more tests" or "Benchmark testing". The stack stays Postgres/Neo4j/MinIO, but the retrieval and graph model are reworked several times and evaluated on multi-hop QA datasets. The results directories that were created (and later deleted) give the sequence of approaches tried: `Results (After Optimizations)` → `Results (Sub Questions Approach)` → `Results (Looping approach)` → `Results (Joint Approach)` → `Results (Penultimate Implementation)` → `Results (Final Implementation)`.

**`033589d` (02-01) — bi-temporal relationships and symbolic ranking.** Relationships gained `valid_from`, `valid_to`, `ingested_at`, `is_active` with contradiction rules for soft invalidation and full history (a Graphiti-style model). Neural reranking was *replaced* by pure symbolic ranking; Community nodes appeared; extraction schemas got robust LLM-output normalisation and unified `isolated_context` fields. Most of this bi-temporal machinery was later removed (`da75dfc`, 05-28) — see decision log D-09.

**`b3fd891`…`044052a` (02-05/06) — the benchmarking campaign starts.** `044052a` deleted a checked-in copy of LongBench (`backend/testing_data/longbeach/…`) and per-dataset prep scripts (`prepare_hotpotqa*.py`, `prepare_musique*.py`), consolidating on HotPotQA and MuSiQue. Port `17401` first appears in these commits (`git log -S17401`), as the benchmark harness's backend port, long before it became the desktop API port.

**`536cbd4` (02-19) — HotPotQA reports and pipeline stabilisation.** Two long Markdown reports (`HotPotQA_Ingestion_Report.md`, `HotPotQA_Summary_Retrieval_Report.md`), plus new services: `ingestion_tracker` (still present), `alias_detector` (removed `559e988`), `rerank` (removed `3ab4f9d`), a `query_decomposition` workflow (removed `68494b7`), `core/log.py` replacing `logging_config.py`, resume/checkpointing for batch ingestion, and `isolated_contexts` storage "for summary A/B testing". `sentence-transformers`, `langchain-google-genai` and `compressed-tensors` were added to requirements.

**`3ab4f9d` (03-04) — Sub-Questions approach + pruning.** 99 files deleted: the Feb reports, `IMPROVEMENTS.md`, `OPTIMIZING_PROCESS.md`, `PKM_UPGRADE.md`, `POTENTIAL_TODO.md`, ~25 one-off alias scripts, and nine generations of `evaluate_hop_pipeline*.py` (v1–v6, with `_gemini` variants). Added `Results. (Sub Questions Approach)/{HotPotQA,MuSiQue}` and `backend/scripts/analyze_graph.py`.

**`24459f8` (03-15) — Looping approach.** The iterative multi-hop retrieval loop that survives today (see [Retrieval and chat](16-retrieval-and-chat.md)) was first tested here. `MULTI_PROVIDER.md` and `SYSTEM_ANALYSIS_Q&A.md` were deleted.

**`559e988` (04-18) — Joint Approach.** The largest research commit: iterative refinement + graph-expansion retrieval, and a burst of new infrastructure — **Qdrant** (`qdrant-client`) as a dedicated vector store alongside Neo4j, **Elasticsearch 9** for keyword search, a reranker service, **Tavily** web search, a **feedback** feature (model/schema/service/endpoint + migration), Leiden community detection (`leidenalg`, `python-igraph`), `json-repair`, init/reset scripts for Elasticsearch/Qdrant/Neo4j/MinIO/Postgres, and new 3D graph components (`graph3d/GraphScene.tsx`, `CameraRig.tsx`, `CommunityCluster.tsx`, `NodeDetailPanel.tsx`). This is the moment the "one store per concern" layout (graph / vectors / keyword / files / metadata) was fixed; only the vendors changed afterwards.

**`097c822` (04-19) — robustness.** Default `LLM_PROVIDER` switched to `ollama`; extraction schema accepts bare lists and unwraps `data`/`result`/`extraction` wrappers; reranker handles v2 (dict with `text`) and v3 (plain string) responses; retrieval runs the reranker early and cuts to top-20 candidates to reduce LLM input.

### Era 3 — May 2026: "Final Implementation", migrations and multi-KB (`68494b7` → `da75dfc`)

**`68494b7` (05-07) — Kuzu replaces Neo4j, Typesense replaces Elasticsearch.** In `requirements.txt` this is literally `-neo4j==6.1.0 / +kuzu==0.11.3` and `-elasticsearch>=9.0.0,<10.0.0 / +typesense==2.0.0`. `services/graph.py` was rewritten (2,592 changed lines), `services/llm.py` (3,052) and `workflows/ingestion.py` (1,752) heavily reworked; `elasticsearch_service.py`, `init_neo4j.py`, `reset_neo4j.py`, `init_elasticsearch.py`, `reset_elasticsearch.py`, `utils/data_validation.py`, `utils/text_processing.py`, `workflows/query_decomposition.py` were deleted; `typesense_service.py`, `init_kuzu.py`, `reset_kuzu.py`, `init_typesense.py`, `reset_typesense.py` were added. Alembic `043653ceaf21_remove_domain_from_notes` dropped the `domain` column (README note dated 2026-04-29: "notes no longer use or store a domain classification field"). Unit/integration test scaffolding (`tests/unit/conftest.py`, `test_graph_queries.py`, `test_llm_contracts.py`, `test_typesense_contract.py`) arrived here; the 3D scene components from `559e988` were removed in favour of a single `graph-3d/page.tsx`. `MAX_LOOP_ITERATIONS` first appears in config (`git log -S`).

**`2655bb8` + `6b6bd54` (05-13) — the comprehensive README.** The pitch changed from "living ontology of your life" to "a personal knowledge graph and multi-hop question-answering system", and the README documented the Kuzu/Qdrant/Typesense/Postgres/MinIO stack, Ollama/LM Studio local inference, and benchmark results. `2655bb8` also (accidentally) deleted `backend/app/core/config.py`, restored in `6b6bd54`, and removed `batch-note-processing/`, `ingestion_fidelity.py`, and the Feb-era backfill scripts. Requirements were briefly bumped to unreleased-looking versions (fastapi 0.136.1, torch 2.11.0, anthropic 0.100.0, google-genai 2.0.1) then rolled back in `f28d205` to fastapi 0.128.0 / torch 2.9.1 / anthropic 0.76.0 / google-genai 1.59.0 — the pins still in place today.

**`09e35e3` (05-13) — separate ingestion LLM clients.** Introduced the `local` provider alias (any OpenAI-compatible server), `INGESTION_*` config keys, `_init_ingestion_clients`, `ingestion_generate`/`ingestion_extract_structured`, and `CHAT_MODEL`/`INGESTION_MODEL` fallbacks. The chat-vs-ingestion model split persists in `runtime_config.json` today.

**`335a253` (05-15) — feedback removed, linting added.** The feedback feature from `559e988` was deleted end-to-end (model, schema, service, endpoint, `c7e92f1b3d04_drop_feedback_table`); `.pylintrc` added; storage default switched **MinIO → RustFS** (an S3-compatible drop-in; the bucket name `liveos-assets` dates from here).

**`8eba91d` (05-18) — benchmark mode and Refiner removed.** `BENCHMARK_MODE` config and docs removed; the secondary LLM "Refiner" pass in the ingestion agent (`refinement_node`) deleted and the router renamed `should_route_after_extraction`. (`acb19a5` the next day re-added a `BENCHMARK_MODE` flag for the evaluation harness; it was finally dropped with the Docker-era config in `fbcafe7`.)

**`d37abd6` (05-18) — multi-knowledge-base support.** `services/kb_registry.py` (320 lines) with `KBContext`/`KBRegistry` singletons; `d4f891a2b5c3_add_kb_id_to_notes`; `GraphService`, `QdrantService`, `TypesenseService`, `RetrievalService`, `IngestionWorkflow`, `ChatWorkflow` refactored to per-KB instances; FastAPI `get_kb` dependency and the `?kb=<slug>` convention; KB list/create/rename/delete endpoints; frontend `kb-context.tsx` and `kb/page.tsx`. `2c10d8d` (05-19) followed up with orphan-entity cleanup on note delete, `find_nodes_by_exact_names`, `_ensure_collections`, `asyncio.to_thread` around blocking SDK calls, and per-KB workflow instances threaded through LangGraph state.

**`acb19a5` (05-19) — media and UX.** ffmpeg installed in the image; WebM/OGG/Opus recordings transcoded to AAC/M4A; `_reason_step` extracts model "thinking" (`reasoning_content` / `<think>` blocks) and surfaces it in a collapsible panel; `ChatProvider`/`chat-context.tsx`; `SegmentedNoteContent` splits notes into text/image/pdf/audio segments; `RetrievalService` moved to instance fields `_graph/_qdrant/_typesense`.

**`f28d205` (05-19) — Docker-first.** Default hostnames became compose service names (`qdrant`, `typesense`, `rustfs`, `postgres`), compose gained `backend`, `init` and `frontend` services (host ports 8700 → backend, 3700 → frontend, 15432 → postgres, 8108 → typesense), `backend/.dockerignore` added, and `docker-compose.prod.yml` deleted. This is the high-water mark of the Docker deployment model that `3f21e08` reversed ten weeks later.

**`28ea18e` (05-20) — LLM service refactor.** `get_chat_model`/`get_ingestion_model`/`init_clients`; `_LazyLLMService` proxy so torch and provider SDKs are not imported at startup; `OPENAI_MODEL_REASONING` removed; heavy multimedia/transformers/PIL imports deferred into loaders.

**`682755f`, `6365686`, `da75dfc` (05-21 → 05-28).** Note `created_at` injected into extraction text; `processed=True` also clears `failed`. Entity mention scanning (`/graph/entities/search`, `/graph/entities/scan-text`), `EntityMentionEditor` (replaced by the CodeMirror editor in `3f21e08`), `EntityDetailPanel`, `entity://` pseudo-links. Then temporal digests: digest nodes, admin endpoints (`/maintenance-status`, `/build-temporal-digests`, `/reset-ingestion-data`, `/reingest-all`), `COMMUNITY_DETECTION_ENABLED` / `TEMPORAL_DIGESTS_ENABLED` / `TEMPORAL_DIGEST_PERIOD`, `analyze_query` date/period filters with today's date injected — and the **removal of the bi-temporal relationship-evolution logic** (`schemas/relationships.py` deleted, confidence filtering dropped, hop queries simplified).

### Era 4 — June to July 2026: multimodal and async (`3a3ece2` → `ac7f4e4`)

Four working days in June and one commit in July, but they set up both the multimodal pipeline and the one architectural dead end that the August pivot explicitly forbids.

**`3a3ece2` (06-10) — video, proxying, CORS.** `process_video()` runs **Marlin** (visual events, a Qwen-VL-family captioner needing `qwen-vl-utils`) and **Whisper** (audio) and merges the results; uploads flagged `📎` that are videos are routed through it. `transformers` moved from `==4.57.6` to `>=5.7.0` and `torch` to `>=2.11.0` (the ">= 5.7" constraint is still a locked rule for Marlin). `CORS_ORIGINS` / `CORS_ALLOW_ORIGIN_REGEX` added; Next.js gained rewrites proxying `/api/v1`, `/health` and `/files` to the backend/RustFS so the browser talks same-origin; `FILES_URL` defaulted to `/files/liveos-assets`. The same-origin rewrite pattern is what the desktop app still uses (see D-20).

**`1986a4b` (06-11) — PDF visual extraction.** Pages with little native text, embedded images or drawings are rendered with PyMuPDF at `PDF_VISUAL_RENDER_DPI` and described with Florence; capped by `PDF_VISUAL_EXTRACTION_MAX_PAGES`; `PDF_VISUAL_TEXT_THRESHOLD` decides which pages qualify. `describe_image` was split into download + `_describe_pil_image`.

**`a8587e6` (06-12) — model HTTP sidecars (later reversed).** To keep the containerised API light, Florence/Whisper/Marlin were moved *out* of the API process into two HTTP services, `backend/local_models_service/` and `backend/marlin_service/` (each with its own Dockerfile and requirements; Marlin had a CUDA variant), addressed via `LOCAL_MODELS_SERVICE_*` and `MARLIN_SERVICE_*` settings and driven from the host by `scripts/host-inference.sh`, `setup.sh/.ps1`, `teardown.sh/.ps1`. `requirements.txt` dropped torch/transformers entirely, keeping only `av` for stream probing. The LLM default device became CPU "for stability in containerized environments". The same commit added `processing_stage`/`processing_model` to notes (`e5f6a7b8c9d0`) and the first `/chat/status` progress endpoint. **All of the sidecar machinery was deleted seven weeks later in `3f21e08`** and is now a forbidden pattern in the locked-decisions rule ("do not reintroduce separate model HTTP sidecars").

**`4408a72` + `80dc440` (06-12) — async chat.** `/api/v1/chat/async` returns a `request_id`; jobs run in a threadpool behind a lock (one chat at a time) and report via `/api/v1/chat/status/{request_id}` with `done`/`result`/`error`; the frontend polls. `QdrantService.query_all` gained per-collection filters (`contexts_filter`, `period_key_filter`) and `day_only`. `NEXT_QUERY` parsing was hardened.

**`ac7f4e4` (07-02) — persistent conversations.** `models/chat.py` (`chat_conversations`, `chat_messages`, migration `f1a2b3c4d5e6`), `services/chat_store.py`, follow-up query rewriting from recent history (`services/llm.py` +71), and conversation create/switch/delete UI. First commit co-authored with Cursor; every commit from here on carries `Co-authored-by: Cursor`.

### Era 5 — August 2026: the Orb desktop product (`3f21e08` → `02ac9d3`)

Sixteen commits in six days replaced the deployment model, the name, three storage backends and the model-serving strategy, then hardened and tuned the result.

**`3f21e08` (08-02) — "Ship LifeOS as a Docker-free desktop app" (130 files, +28.2k/−5.6k).** Everything in the current [System architecture](02-system-architecture.md) that is not FastAPI/Next.js/LangGraph/Kuzu/Qdrant dates from this single commit:

- **Electron shell** (`desktop/main.js`, `supervisor.js` 598 lines, `firefly-runtime.js` 587, `paths.js`, `ports.js`, `wizard.html`, `splash.html`, `download-binaries.js`, bundling scripts `bundle-python.js`/`bundle-node.js`/`build-frontend.js`, `PACKAGING.md`, `.github/workflows/desktop-release.yml`). The supervisor starts Qdrant, Meilisearch, Firefly, the API and the UI as child processes; the first-run wizard writes `paths.json`.
- **In-process models**: `services/local_models.py` (1,440 lines; GGUF chat/embed/rerank via `llama-cpp-python>=0.3.0`), `multimodal_runtime.py` (729; Florence/Whisper/Marlin from HF snapshots), `multimodal_models.py`, `multimodal_services.py`, `model_catalog.py` (443; the models manifest), `ai_gate.py`; `requirements-multimodal.txt` split out so the base bundle stays small. `local_models_service/`, `marlin_service/`, `host-inference.sh`, `setup.*`, `teardown.*` deleted.
- **Storage**: `aiosqlite` added (SQLite for metadata), `meilisearch==0.34.1` replacing `typesense` (commented out with "replaced by Meilisearch"), `watchdog` for the vault watcher; new `vault.py`, `vault_ops.py`, `vault_sync.py`, `vault_watcher.py`, `note_files.py`, `local_storage.py`, `wikilinks.py`, `models/wikilink.py` — note bodies become real `.md` files in per-KB vaults.
- **Finance**: `services/firefly_service.py` (2,330 lines), `models/finance.py`, `frontend/src/app/finance/page.tsx` (1,857) — Firefly III run as a PHP child process with a per-KB administration.
- **Frontend**: CodeMirror-based `markdown-editor/` (wikilink, media-embed, entity, live-preview extensions) replacing `entity-mention-editor.tsx`; `setup/page.tsx` wizard; `notes-graph/page.tsx`; `system-status-indicator.tsx`; `lib/desktop.ts` IPC bridge.
- **Governance**: `.cursor/rules/architecture-decisions.mdc` — the "locked decisions" file quoted throughout section 3 — was created here, and `docker-compose.yml` was re-headed "Contributor / optional infra stack only ... not this compose file", with RustFS moved behind a `legacy-s3` profile and Meilisearch v1.49 replacing Typesense.
- `desktop/package.json` starts at **0.1.0**.

**`6162be2` (08-02) — rebrand to Orb.** App name, env prefixes (`LIFEOS_*`/`LIVEOS_*` → `ORB_*`), Application Support paths, GitHub links (`josetseph/LiveOS` → `josetseph/Orb`) renamed, with explicit fallbacks so existing installs keep their data (`paths.js` +61/−, `core/paths.py` +60/−). The rule file's title changed from "LiveOS locked decisions" to "Orb locked decisions" (nothing else in it changed).

**Release CI shake-out (`845fd47`, `899ddb4`, `2c3518a`, `45fcca5`, all 08-02).** The rebrand broke the release build: `845fd47` restored port URL helpers in `ports.js` and moved CI to Node 24; `899ddb4` added electron-builder author metadata, reworked how Node dependencies are bundled (`node_deps`) and added an Intel Mac runner; `2c3518a` added a Linux AppImage job; `45fcca5` fixed a macOS Gatekeeper breakage, tightened the per-arch CI matrix and bumped to **0.1.1**.

**`fbcafe7` (08-03) — align with the desktop product (1,732 files, +12.3k/−35.8k).** The single biggest cleanup: `backend/app/main.py` shrank by ~1,900 lines as the API was split into `backend/app/api/{admin,chat,deps,files,graph,health,kb,notes,settings,vault}.py`; the notes page was decomposed into `notes/_hooks/*` and `notes/_lib/*`; finance and graph-3d pages were componentised. Deleted: all of Alembic (`alembic.ini`, `env.py`, eight migrations), `typesense_service.py`, `tavily_service.py`, `utils/bucket_storage.py`, `models/finance.py`, `models/settings_store.py`, every `backend/scripts/init_*.py`/`reset_*.py`, `desktop/scripts/hotfix-multimodal-ingest.sh`, `model-settings-modal.tsx`, `service-badge.tsx`, `custom-cursor.tsx`, `grain-overlay.tsx`, the Next.js starter SVGs, and 1,516 benchmark corpus notes (`tests/benchmark/hotpotqa_notes/`, `musique_notes/`). `docker-compose.yml` lost 39 more lines but was kept as the contributor stack.

**`2e3c93b`, `38d6038` (08-04).** Notes page infinite-refresh fix (circular hook bridges storming `GET /notes/{id}` and wiping dirty edits). Windows `prepare-dist` no longer needs system Python: Firefly PHP zips are extracted with PowerShell `Expand-Archive`, falling back to `tar`.

**`f8f527f` (08-06) — full-codebase audit.** Security: KB deletion contained to app-provisioned paths, KB slug sanitising, SSRF/oversize guards on ingest downloads, Electron navigation guards and IPC sender validation, HTTPS-only downloads with capped redirects. Data loss: autosave no longer overwrites in-flight keystrokes; Firefly state preserved across upgrades; vault path rewrites no longer double-applied. Perf: Firefly scope switches cached, models kept resident across batch ingest, blocking Kuzu/vault work moved off the event loop, status pollers calmed, wikilink disambiguation by folder proximity, graceful child-process shutdown. Also isolated the LLM fallback from the shared singleton. `ORB_*` env usage was extended here (`git log -S ORB_`).

**`b84ca73`, `b35d612`, `72413b9` (08-06) — perf and Obsidian parity.** Shared markdown entity-link pipeline; chat scans only recent messages with id-keyed cache; superseded heavy requests cancelled; virtualised notes sidebar; CodeMirror decorations limited to the viewport; batched Meili community updates and Kuzu position writes. Then `[[` autocomplete with folder hints and click-to-create, exact vault paths winning in both resolvers; and title↔filename sync (retitle renames the `.md` and rewrites wikilinks across the vault).

**`8de5cda`, `e14dc67`, `02ac9d3` (08-07).** Batched embeds/upserts/Kuzu writes, concurrent entity/BM25/vector search, per-day query-analysis cache; version **0.2.0**; and a fix to `download-binaries.js`/`firefly-runtime.js` where a 302 response's close/unlink could delete the follow-up Azure download before `tar` ran — `createWriteStream` is now deferred until HTTP 200.

## 3. Decision log

One entry per architectural decision, in chronological order of when it was made. "Locked" means the decision is written into `.cursor/rules/architecture-decisions.mdc` (created `3f21e08`, renamed `6162be2`; the file is deleted in the current working tree — see section 6 — but its content is reproduced in [Decisions and constraints](26-decisions-and-constraints.md)). Where the commit message or rule file states a reason it is quoted; where no reason was recorded, the entry says "no rationale recorded" rather than inventing one.

| # | Date / SHA | Decision | Alternatives rejected | Rationale / evidence |
|---|---|---|---|---|
| D-01 | 2026-01-17 `6fd0224` | FastAPI backend + Next.js frontend, LangGraph for ingestion/chat workflows | (none recorded) | Present from the first commit. `langgraph==1.0.6` stayed pinned until 2026-09-19, when the ingestion agent became a plain sequential function and LangGraph was dropped (§6). |
| D-02 | 2026-01-23 `6852528` | Graph-first hybrid retrieval with a unified vector index over distilled nodes (Concept/Entity/Task/Persona/Reference) | Plain chunk RAG | Commit message: "prioritizing temporal anchors, knowledge graph consensus, grounding, and semantic fallback". The node vocabulary persists in Kuzu today. |
| D-03 | 2026-01-29 `8c133f4` | Provider-agnostic LLM layer with automatic fallback and structured outputs | Single-provider (Ollama-only) | `MULTI_PROVIDER.md`, `.env.example`; enables the OpenAI/Gemini/Anthropic/HF options still offered. |
| D-04 | 2026-02-01 `033589d` | Symbolic ranking instead of neural reranking | Neural reranker (`rerankers` lib) | Message: "Replaces neural reranking with pure symbolic ranking". **Reversed** by `559e988`/`097c822` (reranker service, run early, top-20) and again by `3f21e08` (GGUF cross-encoder in-process). Net: a reranker *is* used today. |
| D-05 | 2026-02-19 `536cbd4` | Resume/checkpointing in batch ingestion; `ingestion_tracker` service | Fire-and-forget | Needed for multi-thousand-note HotPotQA runs; `ingestion_tracker` remains the source of ingestion status. |
| D-06 | 2026-03-15 `24459f8` | Iterative multi-hop "looping" retrieval | Sub-questions (query decomposition) approach (`3ab4f9d`) | Results dirs show the progression Sub Questions → Looping → Joint; `query_decomposition.py` deleted in `68494b7`. |
| D-07 | 2026-04-18 `559e988` | One store per concern: graph DB + dedicated vector DB (Qdrant) + keyword engine + object store + relational metadata | Vectors inside the graph DB (Neo4j vector index, Era 1) | Qdrant and Elasticsearch added together; layout unchanged since, only vendors swapped. |
| D-08 | 2026-04-19 `097c822` | Reranker runs early and truncates to top-20 before LLM | Rerank after LLM/late | Message: "to reduce LLM input size". |
| D-09 | 2026-04-29 (README) / `68494b7` | Drop per-note `domain` classification and the multi-mode (journal/PKM/creative) pitch | Domain-aware retrieval and synthesis (Era 1 README) | README note dated 2026-04-29; Alembic `043653ceaf21_remove_domain_from_notes`. No further rationale recorded. |
| D-10 | 2026-05-07 `68494b7` | **Kuzu (embedded) replaces Neo4j** | Neo4j community + APOC/GDS in Docker | Message: "finalize architecture shift to embedded Kuzu". Embedded = no server process, per-KB directory (`d37abd6`), and later no Docker at all (`3f21e08`). No performance comparison recorded in git. Locked: "Indexes under DATA_DIR: Qdrant, Meilisearch, Kuzu". |
| D-11 | 2026-05-07 `68494b7` | **Typesense replaces Elasticsearch** for keyword search | Elasticsearch 9 | Same commit; ES modules deleted. Superseded by D-19. |
| D-12 | 2026-05-13 `09e35e3` | Separate chat and ingestion LLM clients/models | One model for everything | `INGESTION_*` keys, `ingestion_generate`/`ingestion_extract_structured`; still visible as `model` vs `ingestion_model` in `runtime_config.json` and in the per-KB override work in progress (section 6). |
| D-13 | 2026-05-15 `335a253` | Remove the feedback feature; storage default MinIO → RustFS | Keep thumbs-up/down feedback | No rationale recorded. RustFS itself was later relegated to a `legacy-s3` compose profile (`3f21e08`) — attachments live in the vault (locked: "no RustFS on desktop"). |
| D-14 | 2026-05-18 `8eba91d` | Remove the second-pass LLM "Refiner" node and benchmark mode | Two-pass extraction | Message: "simplifies the ingestion path by removing the secondary LLM refinement pass". |
| D-15 | 2026-05-18 `d37abd6` | Multiple isolated knowledge bases; `?kb=<slug>` selects KB on almost every endpoint; per-KB Kuzu dir, Qdrant collections, keyword index | Single global graph | `KBRegistry`/`KBContext`; extended to Firefly administrations in `3f21e08` (locked: "Per-KB Firefly III administrations (`user_group_id`). Never leak lists across vaults."). |
| D-16 | 2026-05-19 `2c10d8d` | Blocking SDK/DB calls wrapped in `asyncio.to_thread`; per-KB workflow instances passed through LangGraph state | Module-level singletons | Message: "make blocking LLM SDK calls asynchronous-friendly"; extended in `f8f527f` ("move blocking Kuzu/vault work off the event loop"). |
| D-17 | 2026-05-19 `f28d205` | Docker-first defaults, single `docker-compose.yml` | Separate prod compose | **Reversed** for end users by `3f21e08`; compose kept only as "Contributor / optional infra stack". |
| D-18 | 2026-05-20 `28ea18e` | Lazy LLM/torch initialisation (`_LazyLLMService`), deferred heavy imports | Eager import at startup | Message: "reduce startup memory and circular imports". |
| D-19 | 2026-05-28 `da75dfc` | Drop bi-temporal relationship evolution (valid_from/valid_to contradiction rules) in favour of temporal digest nodes + date/period query filters | Graphiti-style edge invalidation (D-04's sibling from `033589d`) | `schemas/relationships.py` deleted; message: "cleans up legacy relationship/evolution code". Feature flags `TEMPORAL_DIGESTS_ENABLED`, `COMMUNITY_DETECTION_ENABLED`. |
| D-20 | 2026-06-10 `3a3ece2` | Same-origin proxying: Next.js rewrites `/api/v1`, `/health`, `/files` (later `/vault-files`) to the backend | Browser calling backend/port directly with CORS | Message: "streamline same-origin proxying"; `CORS_*` settings added for the non-proxied case. Desktop keeps the rewrites (`frontend/next.config.ts`); uploads go direct to the API port to avoid the Next.js body proxy — see [Desktop shell](04-desktop-shell.md). |
| D-21 | 2026-06-10 `3a3ece2` | `transformers>=5.7`, `torch>=2.11`, `qwen-vl-utils` for Marlin/Qwen-VL | Pinned transformers 4.57 | Locked: "one shared in-process stack — do not reintroduce ... a second transformers major for Marlin alone". |
| D-22 | 2026-06-12 `a8587e6` | Heavy vision/audio models as HTTP sidecars (`local_models_service`, `marlin_service`) | In-process | Message: "run heavy vision/audio/video models as separate host/Docker services". **Reversed** by D-25 seven weeks later; now explicitly forbidden (`LOCAL_MODELS_SERVICE_URL`, `MARLIN_SERVICE_URL`, `POST …/image/describe|/audio/transcribe|/caption|/rerank`). |
| D-23 | 2026-06-12 `4408a72` | Async chat: `POST /chat/async` + polling `/chat/status/{request_id}`; one chat job at a time (threadpool + lock) | Long-lived synchronous request | Message: "run chat jobs in a threadpool with a lock to serialize work". Serialisation is consistent with exclusive model residency (D-26). |
| D-24 | 2026-07-02 `ac7f4e4` | Persisted KB-scoped conversations; follow-up rewriting from recent history | Session-only chat | `chat_conversations`/`chat_messages`; first Cursor co-authored commit. |
| D-25 | 2026-08-02 `3f21e08` | **Electron desktop app, no Docker for end users**; supervisor launches Qdrant, Meilisearch, Firefly, API, UI as child processes; graphical first-run wizard writes `paths.json` (data dir, models dir) | Docker Compose (D-17), browser-only | Locked: "End users do not run Docker or browser-only setup ... Bootstrap only: `paths.json`". README pitch: "No Docker. No cloud required." |
| D-26 | 2026-08-02 `3f21e08` | **All local models in-process** in the API (`local_models.py` for GGUF chat/embed/rerank via `llama-cpp-python`; `multimodal_runtime.py` for Florence/Whisper/Marlin); **exclusive residency** — one heavy model resident at a time | Ollama, LM Studio, `llama-server`, HTTP sidecars (D-22) | Locked rule; `requirements.txt` comment: "In-process GGUF inference (no llama-server)". Exclusive residency is the memory budget that makes a single consumer machine viable. |
| D-27 | 2026-08-02 `3f21e08` | Chat GGUF defaults `n_ctx=16384`, `swa_full=True`, `repeat_penalty=1.12`, flash-attn opt-in | 32k context; compact SWA | Locked: "compact SWA causes Gemma 4 ordinal loops; 32k + full SWA OOMs". Code comment in `local_models.py`: "16k + swa_full fits ~24GB Metal; 32k + swa_full OOMs". Supervisor exports `ORB_LLAMA_N_CTX` default `16384`. |
| D-28 | 2026-08-02 `3f21e08` | GGUF selection lives in the models manifest (`model_catalog.py`); embed dims kept in sync with Qdrant via `sync_embedding_infrastructure`; reranker is a cross-encoder GGUF (not dim-coupled) | Free-form model paths | Locked rule. Ingestion integrity: "Qdrant fail-closed for contexts/cores. Dim mismatch mid-ingest raises (no silent collection wipe)". |
| D-29 | 2026-08-02 `3f21e08` | **Note bodies are real `.md` files in a per-KB vault; SQLite holds metadata only** | Postgres row storage (Era 1–4) | Locked: "Never store note body in SQLite ... Attachments live in the vault (no RustFS on desktop)". `aiosqlite` added; `DATABASE_BACKEND="sqlite"` default with `postgres` retained for the contributor compose. |
| D-30 | 2026-08-02 `3f21e08` | **Meilisearch replaces Typesense** | Typesense (D-11) | `requirements.txt`: `# typesense==2.0.0  # replaced by Meilisearch`. No further rationale recorded in git; Meilisearch ships as a single static binary the supervisor can run without Docker, which fits D-25. |
| D-31 | 2026-08-02 `3f21e08` | Multimedia enrichment is appended into the vault `.md` before graph ingest; prior enrichment blocks stripped on re-ingest; vault files never deleted as temps | Enrichment stored separately | Locked rule ("Multimedia ingest" section). Order: PDF text (+Florence) → image Florence → audio/video Whisper → video Marlin. |
| D-32 | 2026-08-02 `3f21e08` | Firefly III bundled as a PHP child process, one administration (`user_group_id`) per KB | External finance tool / none | Locked rule; `firefly-runtime.js`, `firefly_service.py`. Windows extraction fixed in `38d6038`; state preserved across upgrades in `f8f527f`. |
| D-33 | 2026-08-02 `3f21e08` | Fixed high port block 174xx: UI 17400, API 17401, Firefly 17412, Qdrant 17433, Meilisearch 17470; overridable via `ORB_*_PORT` (`LIVEOS_*_PORT` accepted) | Default vendor ports (3000/8000/6333/7700/8080) | `desktop/ports.js`. 17401 had been the benchmark harness port since `b3fd891`. Avoids clashes with developer instances of the same tools. |
| D-34 | 2026-08-02 `6162be2` | Rename LifeOS/LiveOS → Orb with backwards-compatible env (`LIVEOS_*`) and path (`…/LifeOS`, `…/LiveOS`) fallbacks | Clean break | Message: "keeping LifeOS/LiveOS path and env fallbacks for existing installs". |
| D-35 | 2026-08-02 `899ddb4`, `45fcca5` | Node runtime dependencies bundled as `node_deps` (via `bundle-node.js`) rather than installed at runtime; per-arch CI (arm64 + Intel Mac runners, Linux AppImage) | Single universal build | Needed for the Gatekeeper fix and Intel builds; see [Packaging](05-packaging-build-and-release.md). |
| D-36 | 2026-08-03 `fbcafe7` | No Alembic: SQLite schema created/patched in code (`_ensure_optional_columns` pattern) | Alembic migrations (Era 1–4) | All migrations deleted; `alembic` removed from requirements. Tavily web search removed. |
| D-37 | 2026-08-06 `f8f527f` | KB delete contained to app-provisioned paths; KB slug sanitised; ingest downloads HTTPS-only, SSRF-guarded, size-capped; Electron IPC sender validation and navigation guards; graceful child shutdown; atomic `paths.json` writes (write `.tmp`, then `renameSync`) | Trusting user-supplied paths/URLs; direct `writeFileSync` of `paths.json` | Audit commit message; `desktop/main.js` comment added in `f8f527f`: "Atomic write — a truncated paths.json used to boot the app with default" paths (i.e. a crash mid-write silently reset the user's data/models dirs). |
| D-38 | 2026-08-06 `f8f527f` | Models stay resident across a batch ingest (no unload between notes) | Unload after every note | Message: "keep models resident across batch ingest"; complements exclusive residency (D-26) by avoiding thrash within one model kind. |
| D-39 | 2026-08-06 `b35d612`, `72413b9` | Obsidian-compatible wikilinks: `[[` autocomplete with folder disambiguation, exact vault path wins, title ↔ filename kept in sync (retitle renames file and rewrites links) | Opaque note IDs in links | Messages; [Notes and wikilinks](09-notes-wikilinks-and-vault-files.md). |
| D-40 | 2026-08-07 `8de5cda` | Batched embeds/upserts/Kuzu writes; concurrent entity/BM25/vector search; query-analysis cache keyed per day | Sequential per-item I/O | Message: "Cut redundant store round trips and model swaps". |
| D-41 | `68494b7` → today | `MAX_LOOP_ITERATIONS = 3` for the retrieval loop | Unbounded / higher | Present since the Kuzu migration; still `3` in `backend/app/core/config.py`. No recorded tuning rationale. |

## 4. Removed subsystems and legacy paths still visible in the code

Do **not** resurrect any of these. They are listed so an assistant reading an alias, a comment or a compose file does not mistake it for a live path.

### 4.1 Removed subsystems (fully gone from the tree)

| Subsystem | Lived | Removed in | Notes |
|---|---|---|---|
| Neo4j graph (+APOC/GDS) | `6fd0224` → `68494b7` | `68494b7` | Replaced by embedded Kuzu (D-10). |
| Elasticsearch keyword index | `559e988` → `68494b7` | `68494b7` | Replaced by Typesense (D-11), then Meilisearch (D-30). |
| Typesense keyword index | `68494b7` → `3f21e08` | service deleted `fbcafe7` | `TYPESENSE_*` settings survive as aliases (4.2). |
| MinIO / RustFS object storage (`utils/bucket_storage.py`) | `6fd0224` → `fbcafe7` | `fbcafe7` | RustFS still in compose under `profiles: ["legacy-s3"]`; attachments now live in the vault. |
| Postgres as the primary DB | `6fd0224` → `3f21e08` | default flipped `3f21e08` | Still selectable via `DATABASE_BACKEND=postgres` + `DATABASE_TRANSACTION_POOLER_URL` for the contributor compose only. |
| Alembic migrations | `6fd0224` → `fbcafe7` | `fbcafe7` | Schema now created in code. |
| Feedback feature | `559e988` → `335a253` | `335a253` | Model/schema/service/endpoint/migration. |
| Iterative Refiner node, `BENCHMARK_MODE` | Era 2/3 | `8eba91d` (flag briefly back `acb19a5`, gone `fbcafe7`) | |
| Query-decomposition ("sub questions") workflow | `536cbd4` → `68494b7` | `68494b7` | |
| `alias_detector`, `rerank.py`, `data_validation.py`, `text_processing.py`, `ingestion_fidelity.py` | Era 1–3 | `559e988`, `3ab4f9d`, `68494b7`, `2655bb8` | Research-era helpers. |
| Bi-temporal relationship evolution (`schemas/relationships.py`) | `8c133f4` → `da75dfc` | `da75dfc` | |
| Tavily web search | `559e988` → `fbcafe7` | `fbcafe7` | |
| Model HTTP sidecars (`local_models_service/`, `marlin_service/`, `host-inference.sh`, `setup.*`, `teardown.*`) | `a8587e6` → `3f21e08` | `3f21e08` | Forbidden by locked rule (D-22/D-26). |
| Ollama / LM Studio / `llama-server` as local providers | `6fd0224` → `3f21e08` | `3f21e08` | `ollama`/`lm_studio` remain as deprecated aliases (4.2). |
| `docker-compose.prod.yml` | `6fd0224` → `f28d205` | `f28d205` | |
| `batch-note-processing/` scripts, `backend/scripts/init_*.py` / `reset_*.py`, `init_local.py`, `init.sh` | Era 1–4 | `2655bb8`, `fbcafe7` | Replaced by in-app admin endpoints and wizard. |
| HotPotQA/MuSiQue note corpora and per-run result JSONs | Era 2–3 | `fbcafe7` (1,516 files) | Harness and `Results/` now live on the `orb-testing` branch. |
| `entity-mention-editor.tsx`, `model-settings-modal.tsx`, `service-badge.tsx`, `custom-cursor.tsx`, `grain-overlay.tsx`, `graph3d/{GraphScene,CameraRig,CommunityCluster,NodeDetailPanel}.tsx` | Era 1–4 | `68494b7`, `3f21e08`, `fbcafe7` | Replaced by CodeMirror editor, settings page, componentised graph-3d. |
| `development_process.md`, `MULTI_PROVIDER.md`, `RETRIEVAL_FAQ.md`, research `*.md` reports | Era 1–3 | `8c133f4`, `24459f8`, `3ab4f9d`, `6ed2eb0` | The current `Docs/` set supersedes them. |

### 4.2 Legacy names, aliases and dead branches still in the tree

| Where | What you will see | Status |
|---|---|---|
| `backend/app/core/paths.py`, `desktop/src-tauri/src/runtime.rs` | (removed 2026-09-19) the `LIVEOS_*` env aliases and the `…/LifeOS`, `…/LiveOS` Application Support fallbacks from D-34 | Dropped once no install depended on them; only `ORB_*` names and the `Orb` folder remain. |
| `backend/app/core/config.py` | `TYPESENSE_HOST/PORT/API_KEY/COLLECTION_NAME` next to `MEILI_HOST/PORT/MASTER_KEY/INDEX_NAME`, with a validator that copies legacy `TYPESENSE_*` onto `MEILI_*` "for older installs" | Aliases only; there is no Typesense client. |
| `backend/app/core/config.py`, `backend/app/core/database.py` | `DATABASE_BACKEND: "sqlite" (desktop default) \| "postgres" (contributor docker)`; asyncpg URL rewriting | Postgres branch is contributor-only; desktop is SQLite (D-29). `asyncpg` is still pinned for this. |
| `backend/app/models/note.py` | `content = Column(Text, nullable=True, default="")` | Vestigial: note bodies live in the vault `.md` (D-29). Do not start writing bodies here. |
| `backend/app/services/llm.py` | `"ollama": _local, "lm_studio": _local  # deprecated alias` and warnings when `provider in ("ollama","lm_studio")` | Aliases map to the generic OpenAI-compatible `local` provider; no Ollama-specific code path remains. |
| `frontend/next.config.ts` | `filesProxyTarget` defaulting to `http://rustfs:9000` and the `/files/:path*` rewrite | Docker-era file proxy; desktop serves attachments via `/vault-files/:path*` → API. |
| `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `backend/.dockerignore` | Postgres, RustFS (`legacy-s3` profile), Qdrant, Meilisearch, backend, frontend services | Header states "Contributor / optional infra stack only. End users run the Orb desktop app". Not used by packaging or CI. |
| `backend/requirements.txt` | (cleaned 2026-09-19) `langchain-openai`, `langgraph`, `instructor`, `tenacity`, `python-dateutil`, `dateparser`, `requests`, `regex`, `scikit-learn` were unimported or single-use and are gone | — |
| `Results/…` directories, `Platform Images/` | Benchmark reports from Eras 2–3, screenshots | Historical artefacts; reports still reference "LiveOS Brain". |

## 5. Version history

The version lives in `desktop/src-tauri/tauri.conf.json` and `Cargo.toml` (the release tag is `desktop-v<version>`); `frontend/package.json` and the FastAPI `version` are kept in step by hand.

| Version | Date / SHA | Scope |
|---|---|---|
| (unversioned) | 2026-01-17 → 2026-07-02 | Research prototype "LiveOS Brain"; no release artefacts. |
| **0.1.0** | 2026-08-02 `3f21e08` | First desktop build: Electron shell, in-process models, SQLite + vaults, Meilisearch, Firefly, GitHub Actions release workflow. Rebranded to Orb in `6162be2` the same day. |
| **0.1.1** | 2026-08-02 `45fcca5` | macOS Gatekeeper fix, per-arch CI (arm64/Intel Mac, Linux AppImage), Node 24, author metadata, `node_deps` bundling. |
| *(unreleased)* | 2026-08-03 → 08-06 `fbcafe7`…`72413b9` | API modularisation and legacy removal, notes-page fixes, Windows Firefly prefetch, security/data-loss/perf audit, wikilink autocomplete, title↔filename sync. |
| **0.2.0** | 2026-08-07 `e14dc67` | "ingest/retrieval speedups and wikilink UX" — includes `8de5cda` batching/parallel I/O. `02ac9d3` (Firefly download race fix) landed after the bump and is in HEAD without a version change. |
| *0.3.0 (untagged)* | 2026-09-18 `1b5e452` | Electron shell replaced by a Tauri 2 shell over the Python desktop runtime; version bumped in the tree but never tagged. |
| **1.0.0** | 2026-09-19 `1d5c7d7` (bump `15e4c92`) | Major: the over-engineering sweep (§6 entry of the same date) — one LLM call path, GGUF-only local chat, no stored AI mode, finance API trimmed to what the UI uses, 16 dependencies dropped, ~7k lines removed, suite green. First release from the Tauri shell. Release prep followed through 2026-09-20 (`5b7a90f` … `0b50c22`, §6): bundle id `com.josetseph.orb`, a `release` job drafting the GitHub Release, the hdiutil DMG, the band-aid removal pass, vault-relative attachment links and the attachments-only vault tree. |

## 6. Uncommitted work in progress (as of 2026-09-02)

The working tree diverges from `02ac9d3`. This section summarises `git status` / `git diff --stat` only; it does not describe behaviour beyond what the diffs and new file names show. 22 tracked files modified (+1,144 / −340), 9 untracked files (excluding `Docs/`).

**Deleted:** `.cursor/rules/architecture-decisions.mdc` (the locked-decisions rule quoted in section 3). The deletion is now in `HEAD` as well; its content is preserved in [Decisions and constraints](26-decisions-and-constraints.md), which is the sole source.

**Per-KB LLM overrides** (`backend/app/api/kb.py` +119, `backend/app/services/kb_registry.py` +205, `ai_gate.py`, `llm.py`, `retrieval.py`, `workflows/chat.py`, `frontend/src/app/kb/page.tsx`, `frontend/src/lib/{api,types}.ts`, new `frontend/src/app/kb/_components/KBModelPanel.tsx` 274 lines, new test `backend/tests/unit/test_kb_llm_config.py`):

- New nullable columns on `knowledge_bases`: `llm_provider`, `llm_model`, `llm_ingestion_model` — comment: "Per-KB LLM override (NULL = inherit the system Settings). Chat + ingestion only — embed/rerank/multimodal stay system-wide (embed dims are shared)". Added via `_ensure_optional_columns(conn: sqlite3.Connection)` (the no-Alembic pattern, D-36).
- New endpoints `GET/PATCH /api/v1/kb/{kb_id}/llm` (`get_kb_llm`, `update_kb_llm`, `KBLLMInput`), returning `{kb_id, override{provider,model,ingestion_model}, effective{provider,model,ingestion_model,inherited}, providers[], local_models[{id,label,size_gb}]}`; `local_models` is limited to "Chat GGUFs already on disk — the only local models a KB can pin" (`model_catalog.chat_model_downloaded`, `downloaded_chat_models`).
- `kb_registry`: `build_kb_llm_service`, `effective_llm_config`, `KBContext.has_llm_override` / `.llm` / `.apply_llm_override`, `KBRegistry.set_llm_config` / `.effective_llm`. `ai_gate` gains `provider_is_configured`, `ai_is_configured(kb=None)`, `require_ai(kb=None)`.

**Extraction chunking** (new `backend/app/workflows/extraction_chunking.py` 159 lines; `workflows/agents/ingestion_agent.py` +508/−; `workflows/ingestion.py` +79/−; `llm.py` `ingestion_generate_with_meta`, `ingestion_count_tokens`, `ingestion_context_tokens`; new tests `test_extraction_chunking.py`, `test_ingestion_chunked_extraction.py`):

- Constants `_OUTPUT_TO_INPUT_RATIO = 2.5`, `_DEFAULT_CHUNK_TOKENS = 4000`, `MIN_SPLIT_TOKENS = 400`; functions `chunk_token_budget`, `split_for_extraction` (sentence-regex packing with `_split_oversized`), `merge_extractions`.
- Ingestion agent gains `_build_extraction_prompt`, `_extract_chunk(llm, text, count_tokens, depth)`, `_extract_with_chunking(llm, content, logs) -> (Extraction, int)`, `_batch_image_titles`.

**Model load clock / runtime budget** (`local_models.py` +195; `multimodal_runtime.py` `_record_load`; `workflows/ingestion.py` `_log_timing` (a short-lived `services/timing.py` wrapper module was folded back into direct `ModelLoadClock` calls on 2026-09-19); new tests `test_model_load_clock.py`, `test_local_runtime_budget.py`):

- `class ModelLoadClock` (`record(kind, seconds)`, `snapshot()`, `diff(before, after)`, `describe(delta)`), `class PromptTooLongError(RuntimeError)`, `_default_chat_max_tokens()`, `ensure_chat_loaded`, `resolve_chat_gguf`, `count_tokens`, `_prompt_token_estimate`, `_remaining_output_budget`.
- `desktop/supervisor.js`: the fixed `ORB_LLAMA_MAX_TOKENS` default of `"10240"` is removed; new comment: "No default output cap — the API sizes max_tokens per call from the context left after the prompt; a fixed cap truncated long extractions." The variable is only exported when set in the environment. `ORB_LLAMA_N_CTX` default `16384` is unchanged (D-27).

**Minor:** `firefly_service.py` (+20/−), `api/admin.py`, `api/chat.py`, `api/notes.py`, `api_desktop.py` (1–4 line changes each, consistent with the `require_ai(kb)` / per-KB LLM plumbing).

### 2026-09-19 — over-engineering sweep (`1d5c7d7`)

A repo-wide audit removed about 6,500 lines and 16 dependencies without changing product behaviour:

- **Dependencies dropped.** Python: `instructor`, `langgraph`, `langchain-openai`, `tenacity`, `python-dateutil`, `dateparser`, `requests`, `regex`, `scikit-learn`. npm: `axios`, `tailwind-merge`, `framer-motion`, `class-variance-authority`, `postcss`, `@tanstack/react-virtual`. Rust: `url`.
- **LLM service** (`llm.py` 1903 → ~1070 lines): one `_chat()` fan-out; `extract_structured` and the per-provider extraction helpers, `GeminiChatWrapper`, the fallback provider and the async client twins are gone; query analysis is plain JSON behind `lru_cache`.
- **Ingestion**: the LangGraph `StateGraph` became a sequential `run_ingestion_agent`; relationship score fields (`strength/confidence/relevance`, `sentiment`, `edge_weight`) were removed because no prompt ever filled them; community clustering is a numpy greedy merge.
- **Local chat is GGUF-only**: `chat_runtimes.py` (MLX / transformers) deleted; `model_formats.py` 304 → 57 lines.
- **Setup**: `AI_SETUP_MODE` removed end to end (setting, `paths.json`, Tauri env, first-run picker, frontend); readiness comes from `ai_gate` alone. `LIVEOS_*`/`LifeOS` aliases and the `kb_registry.json` migration removed.
- **Finance**: bills, piggy banks, tags, webhooks, object groups, exchange rates, attachments and `/finance/open` routes removed (no UI ever called them).
- **Frontend**: native `fetch` client, native `<dialog>` modals, no hydration flag, no barrels, lint at 0 warnings.
- Tests: 455 passing, 0 failing (stale tests fixed or deleted).

**2026-09-19 (later the same day) — band-aid removal pass (`69330c7`):** repairs that ran on every read became one-time migrations, and prompt-and-parse became JSON mode.

- **Structured output**: `json_mode` on `_chat`/`generate`/`ingestion_generate[_with_meta]`/`_reason_step` (OpenAI `response_format`, Gemini `response_mime_type`, llama.cpp JSON grammar; Anthropic prompt-driven); the research step and community naming are JSON parsed by pydantic (`_ResearchStep`, `_CommunityName`), deleting `_section_re`, `_clean_next_query`, the first-turn/`FULL_ANSWER` rescues and `_parse_name_summary`; `_clean_json` is fence + curly quotes + `json_repair` (hard import).
- **Closed relationship vocabulary**: `RELATIONSHIP_TYPES` (42 predicates, catch-all `related_to`) listed in the prompts and enforced by the schema; `clean_rel_type`, the alias validators and the `_` → space rewrites are gone; default `relates_to` → `related_to`.
- **Enrichment blocks**: `wrap_legacy_enrichment_blocks` gives pre-marker output markers; `_strip_prior_multimedia_enrichment(keep=…)` removes delimited blocks only (no truncation from the first header). Task-split prompts no longer render doubled braces; `describe_image_section` gets its `llm` on the main path. Community names must pass `_name_fits_members` instead of a generic-word blocklist. Meili `isolated_contexts` is an array; `MeilisearchService.index_name`.
- **One-time migrations** (doc 22 §9.1): vault sweep `migrate_vault_files` (`<vault>/.orb/migrated-v1`), `rel_path` backslash repair in `init_db`, legacy `notes.content` bodies moved to disk, `normalize_kuzu_path` and `llm_provider` coercion only in `kb_registry._load`, `main._migrate_stores` (`DATA_DIR/.stores-migrated-v1-<kb_id>`: `FACTS:` scrub, note-name backfill, `relates_to` → `related_to`).
- **Deleted read-time repairs**: `normalize_vault_file_refs`, the collapse in `rewrite_refs_in_text`, `graph._strip_facts_prefix`, `_migrate_legacy_db_path`, `retrieval._extract_predicate`, `api/graph._meili_doc_as_dict` and its per-request title resolve/Kuzu backfill, `reranker._normalize_results` (rows carry `relevance_score` only), `core/log.LOGS_DIR`, `LocalLlamaRuntime.ensure_loaded`, `.partial` sibling checks; `_heal_selection_paths` runs once at boot and the env-default GGUF guess is persisted.
- **API / frontend**: chat responses carry `sources: [{id, title}]` instead of a `### References` block; `created_at` is a `datetime` (422 on garbage); upload response is `{url}`; frontend URL helpers stopped repairing/decoding; Tauri `trusted()` compares URL origins.
- Tests: 473 passing, 0 failing; new `test_vault_migration.py`, `test_note_created_at.py`, `test_ingestion_community_names.py`.

**2026-09-19 → 09-20 — 1.0.0 release prep (`5b7a90f` … `0b50c22`):** what shipped between the band-aid pass and the release.

- **Release**: version 1.0.0 in `tauri.conf.json`, `Cargo.toml`, `frontend/package.json` and the FastAPI app (`15e4c92`); bundle identifier `com.josetseph.orb` (`621b17e`); `.github/workflows/desktop-release.yml` gained a `release` job that drafts the GitHub Release from the four platform artifacts on a `desktop-v*` tag (`5b7a90f`).
- **macOS DMG**: `build.py dist` asks Tauri for the `.app` only (`--bundles app`) and writes `Orb_<ver>_<arch>.dmg` itself with one `hdiutil create` from a staging folder (`Orb.app` + `Applications` symlink), HFS+ and UDZO zlib-9 (`1c01efb`, `dc76156`) — Tauri's `bundle_dmg.sh` mount/AppleScript/unmount failed intermittently with "Resource busy", and an APFS image compressed to twice the size.
- **Data dir guard**: `desktop_runtime.main()` warns loudly when the data dir sits under `CloudStorage`, `Mobile Documents`, `Dropbox` or `Google Drive` (`329cbc3`); the owner's own data dir moved off OneDrive. Guidance: `DATA_DIR` on local disk, only the vault in a synced folder.
- **Firefly `.env` quoting**: every value is quoted by `_env_quote` (`f54809b`) — the default macOS data dir path contains a space ("Application Support") and phpdotenv rejected it.
- **Vault-relative attachment links** (`76a9f4f`): notes store `attachments/<sub>/<file>` (percent-encoded segments) instead of `/vault-files/<kb>/…`; readers accept both forms (`vault_rel_from_url`, `attachment_key`, frontend `vaultRelPath`), the frontend's `resolveFileUrl` mints the serving URL per current KB, and `local_storage.vault_file_url` does the same for previews/extractor input. The vault sweep became v2 (relativise links) and then v3.
- **Vision projector pairing** (`76a9f4f`): `find_mmproj` matches only `mmproj-<model stem>-*.gguf` beside the chat GGUF (no "only projector in the folder" fallback); `LocalLlamaRuntime` initialises the projector's mtmd context eagerly at load and drops it with a warning on failure; the `orb-mmproj` daemon thread started by `sync_embedding_infrastructure` downloads the projector for the selected catalog model.
- **Attachments only under `attachments/`, uploads grouped by note folder** (`5a60d40`, `0b50c22`): `list_vault_media_files` and the `media_files` key of `GET /vault/folders` are gone, the tree has no media rows, `vault_ops.move_vault_file` refuses moves across the `attachments/` boundary, `POST /upload?folder=` stores at `attachments/<folder>/<stem>-<8hex><ext>`, and the v3 sweep (`<vault>/.orb/migrated-v3`) moves stray files in and rewrites their links. Attachment-link stripping reuses the move rewriter's matcher.
- **Small fixes**: `getNoteStatus` sends `?kb=` on the status poll.
- Tests: 485 passing, 0 failing; new `test_upload_folder.py` plus cases in `test_vault_folders.py`, `test_vault_migration.py`, `test_local_runtime_budget.py`, `test_desktop_runtime.py`.

## 7. Open questions and discrepancies

- `.cursor/rules/architecture-decisions.mdc` was the only in-repo statement of the locked decisions; it is now deleted in `HEAD`, so `Docs/26-decisions-and-constraints.md` is the sole source.
- No commit records *why* Kuzu was chosen over Neo4j or Meilisearch over Typesense beyond "embedded"/"replaced by"; the decision log states this explicitly rather than inferring benchmarks.
- `docker-compose.yml`, both `Dockerfile`s and `backend/.dockerignore` survive despite `fbcafe7`'s message "remove ... Docker". They are contributor-only; nothing in CI uses them.
- `backend/app/models/note.py` still has a `content` column although note bodies are vault files (D-29). Whether it is written anywhere is a question for [Notes and vault files](09-notes-wikilinks-and-vault-files.md).
- The README pins the retrieval reranker as "symbolic" nowhere today, but D-04 → D-26 shows reranking flipped symbolic → neural → GGUF cross-encoder; docs on retrieval should describe only the current GGUF cross-encoder.
