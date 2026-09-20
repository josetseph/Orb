# Testing

**What this covers.** Everything that verifies Orb's behaviour: the pytest unit suite under `backend/tests/unit/` (what each module pins, what `conftest.py` stubs and why), the lint/type tooling (`backend/.pylintrc`, `frontend/eslint.config.mjs`, `frontend/tsconfig.json`), the CI job (`.github/workflows/ci.yml`), the current testing gaps (no frontend tests, no integration tests), and how to add tests for new services following the existing patterns.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Packaging, build and release](05-packaging-build-and-release.md) · [Backend core and configuration](06-backend-core-and-configuration.md) · [API reference](07-api-reference.md) · [Notes, wikilinks and vault files](09-notes-wikilinks-and-vault-files.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Local models and inference](12-local-models-and-inference.md) · [LLM providers and prompting](13-llm-providers-and-prompting.md) · [Graph storage (Kuzu)](14-graph-storage-kuzu.md) · [Search indexes (Qdrant / Meilisearch)](15-search-indexes-qdrant-meilisearch.md) · [Retrieval and chat](16-retrieval-and-chat.md) · [Configuration reference](21-configuration-reference.md) · [Logging and observability](23-logging-and-observability.md) · [Development history](25-development-history.md) · [Decisions and constraints](26-decisions-and-constraints.md) · [Development guide](27-development-guide.md) · [Glossary](28-glossary.md)

---

## 1. Responsibilities and boundaries

| Owns | Does NOT own |
|---|---|
| `backend/tests/unit/` — the only automated test suite in the repository (pytest, pure-Python, no live infrastructure). | Runtime behaviour of the services under test — see the per-subsystem docs linked above. |
| Lint/type configuration: `backend/.pylintrc`, `frontend/eslint.config.mjs`, `frontend/tsconfig.json`. | CI enforcement — there is none. `.github/workflows/desktop-release.yml` is the only workflow; it builds installers and drafts a GitHub Release, and runs no tests (see [Packaging, build and release](05-packaging-build-and-release.md)). |

Boundary facts that matter when modifying code:

- Unit tests never start Qdrant, Meilisearch, Kuzu, SQLite, or an LLM. Every service is instantiated with `Class.__new__(Class)` to skip `__init__` (which would open connections), then the attributes the method under test reads are set by hand. Any change to a service's `__init__`-assigned attribute names (`_client`, `_enabled`, `index_name`, `_index`, `_db`, `_conn`) silently breaks the corresponding test helper.
- The harness is single-KB: it never sends `?kb=`, so it operates on whatever the backend resolves as the default KB. Run it against a dedicated KB by making that KB the default before starting (see [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md)).


## 2. Files

| Path | Purpose | Key exports / contents |
|---|---|---|
| `backend/tests/__init__.py`, `backend/tests/unit/__init__.py` | Make `tests` a package so pytest's default (`prepend`) import mode inserts `backend/` on `sys.path`; `app.*` imports then work from any cwd. | — |
| `backend/tests/unit/conftest.py` | The one shared fixture: the autouse settings patch. | `patch_settings` (autouse, pins `LLM_PROVIDER="local"`) |
| `backend/tests/unit/test_chat_context.py` | Follow-up query rewrite (`LLMService.rewrite_follow_up_query`). | 6 tests in `TestRewriteFollowUpQuery` |
| `backend/tests/unit/test_chat_summary.py` | Rolling chat summary: `ChatStore.refresh_summary` window arithmetic over a real SQLite file and `render_history` ordering/truncation. | 2 tests |
| `backend/tests/unit/test_extraction_schemas.py` | Pydantic pre-validators in `app/schemas/extraction.py` (`None` handling, wrapper unwrapping, the closed `RELATIONSHIP_TYPES` vocabulary — off-list predicates become `related_to`). | 16 tests |
| `backend/tests/unit/test_graph_layout.py` | Pure geometry in `app/utils/graph_layout.py`. | `TestFibonacciSphere`, `TestDeterministicJitter`, `TestComputeSolarPositions`, `TestComputeSpringLayout3d` |
| `backend/tests/unit/test_graph_queries.py` | Cypher-shape regression guards for `GraphService.get_related_nodes`. | `_get_graph_service_class()` (imports `app.services.graph` with `kuzu.Database`/`kuzu.Connection` patched), `_make_graph_service()`, `_row()` |
| `backend/tests/unit/test_llm_json_cleaning.py` | `LLMService._clean_json` (fence stripping, smart quotes, `json_repair`). | 16 tests |
| `backend/tests/unit/test_meili_contract.py` | Meilisearch document contract: `node_id` (primary key) always present in `index_node` payloads; `isolated_contexts` stored as a list. | `_make_meili_service()`, 2 tests |
| `backend/tests/unit/test_qdrant_contract.py` | Qdrant payload/filter contract for `upsert_node_core` and `search_node_cores`. | `_make_service()` |
| `backend/tests/unit/test_extraction_chunking.py` | Paragraph-bounded chunking, token budget, and extraction merge in `app/workflows/extraction_chunking.py`. | `TestSplitForExtraction`, `TestChunkTokenBudget`, `TestMergeExtractions` |
| `backend/tests/unit/test_ingestion_chunked_extraction.py` | Chunk/truncate/retry loop and batched image titling in `ingestion_agent`; documents the duck-typed LLM protocol via `_StubLLM`. | 4 async tests |
| `backend/tests/unit/test_kb_llm_config.py` | Per-KB provider/model override resolution (`kb_registry.effective_llm_config`) and `LLMService` model getters. | `TestEffectiveLLMConfig`, `TestLLMServiceOverrides` |
| `backend/tests/unit/test_local_runtime_budget.py` | `LocalLlamaRuntime` output budgeting, token counting, `LLAMA_MAX_TOKENS`, GGUF resolution, `response_format` pass-through in JSON mode, eager projector init dropping a broken projector so text chat still works. | `TestOutputBudget`, `TestMaxTokensEnv`, `TestResolveChatGguf`, `TestJsonMode`, `TestEagerProjectorInit` (17 tests) |
| `backend/tests/unit/test_model_load_clock.py` | `ModelLoadClock` snapshot/diff/describe. | `TestModelLoadClock` |
| `backend/tests/unit/test_vault_migration.py` | The one-time vault sweep (`vault_sync.migrate_vault_files`): legacy link shapes fixed in place and the second run is a no-op; v2 relativises `/vault-files/<any kb>/…` links and their markers; v3 moves stray non-`.md` files under `attachments/` and rewrites their links; `.orb/migrated-v3` marker written. | 3 tests |
| `backend/tests/unit/test_upload_folder.py` | `store_upload` puts a file in `attachments/<note folder>/`, root notes upload flat, and `..`/absolute folders raise `Invalid folder`. | 3 tests |
| `backend/tests/unit/test_vault_folders.py` | Folder moves carry everything and rewrite the extraction marker with the link, a folder cannot move into itself, `move_vault_file` refuses moves across the `attachments/` boundary, folder delete removes the tree; `TestStripRefs` pins that deleting an attachment strips every link form and leaves unrelated notes untouched. | 5 tests + `TestStripRefs` |
| `backend/tests/unit/test_desktop_runtime.py` | Meili master key random for fresh installs and sticky, legacy `orb-dev-key` kept when data exists, `boot-status.json` written atomically, Firefly `.env`/`runtime.json` keys; `TestFireflyEnvQuoting` pins `_env_quote` (single quotes; double-quoted escaped fallback on an apostrophe). | 6 tests |
| `backend/tests/unit/test_vision_routing.py` | `find_mmproj` pairs only `mmproj-<chat stem>-*.gguf` beside the chat GGUF (another model's projector, or none, → `None`); `mmproj_hf_path` naming. | 8 tests |
| `backend/tests/unit/test_note_created_at.py` | `created_at` on note create/update is a `datetime`: garbage → 422, naive → UTC. | 4 tests |
| `backend/tests/unit/test_ingestion_community_names.py` | Community names come back as `{name, summary}` JSON and are accepted only when anchored in a member entity (`_name_fits_members`), else the derived fallback. | 2 tests |
| `backend/tests/unit/test_gguf_metadata.py` | GGUF header parsing against **synthesised** fixtures (`build_gguf` writes spec-conformant bytes), including the guards for corrupt/hostile headers and the `pooling_type` vs `chat_template` role distinction. | 24 tests |
| `backend/tests/unit/test_model_discovery.py` | Disk scanning: shard grouping, AppleDouble/partial/dotfile filtering, venv + depth pruning, `MODELS_DIR`-relative refs, the metadata cache, `inspect_chat_model`. | 31 tests |
| `backend/tests/unit/test_byo_model_selection.py` | End-to-end bring-your-own model: `resolve_chat_gguf` for catalog ids / relative refs / absolute paths, loud failure for missing or unusable files, `n_ctx` clamping, KB-API validation, payload listing. | 18 tests |
| `backend/tests/unit/test_credentials.py` | `CredentialStore` semantics, version bumps, endpoint URL identity, and the invariant that **no status output contains key material**. | 24 tests |
| `backend/tests/unit/test_finance_chat_llm.py` | Finance synthesis routes through the KB's own LLM and runs off the event loop (a concurrent poller must keep ticking). | 6 async tests |
| `backend/tests/unit/test_credentials.py`, `test_credentials_keyring.py` | `CredentialStore`: endpoint URL normalisation, keychain persistence through `keyring` (mocked), session-only fallback when no backend is usable. | — |
| `backend/.pylintrc` | Backend lint policy. | see §6 |
| `frontend/eslint.config.mjs`, `frontend/tsconfig.json` | Frontend lint + TS strictness. | see §6 |
| `Results/…` (branch `orb-testing`) | The Feb–May 2026 benchmark reports and logs (Sub Questions, Looping, Joint, Final Implementation, After Optimizations) — moved off `main` with the harness (`49bfedd`); the `.gitignore` force-track rule for `Results*/` remains. | — |
| `.github/workflows/desktop-release.yml` | The only CI workflow: four `build.py prepare` + `build.py dist` jobs on tag `desktop-v*`, then a `release` job that drafts the GitHub Release from their artifacts. **No job runs pytest, pylint or eslint** (`npm run build` inside `prepare` does run `tsc --noEmit`). | — |


## 3. Running the unit tests

### 3.1 Invocation

```bash
cd backend
source .venv/bin/activate            # or use .venv/bin/python -m pytest
pip install -r requirements.txt -r requirements-dev.txt   # pytest + pytest-asyncio are in requirements-dev.txt
python -m pytest tests/unit -q
python -m pytest tests/unit/test_extraction_schemas.py -q      # one module
python -m pytest tests/unit -q -k "chunk"                       # by keyword
```

- Run from `backend/` (or pass `backend/tests/unit` as the path). `backend/tests/__init__.py` and `backend/tests/unit/__init__.py` make `tests` a package, so pytest's default `prepend` import mode inserts `backend/` (the first ancestor without an `__init__.py`) on `sys.path`; `import app...` then resolves without `PYTHONPATH`.
- `backend/pytest.ini` sets `testpaths = tests/unit` (so a bare `pytest` never picks up `tests/integration/`), `asyncio_mode = strict` (`async def` tests must carry `@pytest.mark.asyncio` — the four async tests in `test_ingestion_chunked_extraction.py` do) and registers the `integration` marker. There is no `pyproject.toml`, `setup.cfg` or `tox.ini`.
- No markers, no `-p` plugins, no coverage configuration. `.gitignore` ignores `.pytest_cache/`.

### 3.2 Dependencies the suite needs

| Package | Why | In `requirements.txt`? |
|---|---|---|
| `pytest` | runner | `requirements-dev.txt` |
| `pytest-asyncio` | `@pytest.mark.asyncio` tests | `requirements-dev.txt` |
| `json-repair`, `openai`, `google-genai`, `anthropic` | imported by `app/services/llm.py` (several test modules import `LLMService`) | yes |
| `qdrant-client` | `app/services/qdrant_service.py` (`test_qdrant_contract.py` also imports `qdrant_client.models` directly) | yes |
| `kuzu` | `test_graph_queries.py` does `patch("kuzu.Database")`, which imports the real `kuzu` module before patching | yes |
| `meilisearch` | `app/services/meilisearch_service.py` | yes |
| the ingestion stack (`numpy`, `kuzu`, …) | `app/workflows/agents/ingestion_agent.py` (`test_ingestion_chunked_extraction.py`) | yes |
| `llama-cpp-python` | `app/services/local_models.py` (`test_local_runtime_budget.py`, `test_model_load_clock.py`) — only if imported at module top; the tests never load a GGUF | yes (multimodal extras separate) |

In short: the unit tests need the **full** backend environment installed, even though they never open a connection. A venv built from `requirements.txt` + `requirements-dev.txt` is the minimum.

### 3.3 Environment and settings

- `conftest.py`'s autouse `patch_settings` fixture sets `settings.LLM_PROVIDER = "local"` for every test, regardless of the shell environment. Importing `app.core.config` still evaluates `Settings()` once from the process environment (`extra="ignore"`) and resolves `DATA_DIR`/`KUZU_DB_PATH` defaults — see [Backend core and configuration](06-backend-core-and-configuration.md). Exported `Settings` variables in your shell leak into the tests except for the keys individual tests monkeypatch.
- `test_kb_llm_config.py` has its own autouse fixture that overrides `LLM_PROVIDER` to `"local"` and pins `LLM_MODEL`, `CHAT_MODEL`, `INGESTION_MODEL`, `INGESTION_LLM_MODEL`, `GEMINI_MODEL`, `OPENAI_MODEL` — it runs after `patch_settings` and wins.
- `normalize_base_url` lives only in `backend/app/services/credentials.py` (the UI sends the raw URL); `test_credentials.py` covers the URL corpus and `test_credentials_keyring.py` the keychain persistence with `keyring` mocked.
- Settings read by code under test and controlled via `monkeypatch.setattr(config.settings, …)`: `EXTRACTION_CHUNK_TOKENS`, `LLAMA_MAX_TOKENS`; `test_local_runtime_settings.py` covers the `PUT /settings/local-runtime` round-trip. Set `DATA_DIR`/`ORB_DATA_DIR` to a scratch folder before running the suite: `app/services/graph.py` opens the configured Kuzu file at import and the running desktop app holds a lock on the real one.
- Nothing writes to disk except `tmp_path` fixtures in `test_local_runtime_budget.py`.

### 3.4 Observed state of the suite (2026-09-20)

`pytest tests/unit -q` → **485 passed, 0 failed** (2026-09-20, after the attachments / projector / packaging changes; 473 after the 09-19 band-aid removal pass, 455 before it) against an interpreter with `requirements.txt` + `pytest` + `pytest-asyncio` installed (the bundled `desktop/resources/backend/python` works; so does `uv venv --system-site-packages` over it). The repo's `backend/.venv` is still a partial install and cannot run the suite.

Stale tests were cleaned up on 2026-09-19: `test_relationships.py`, `test_timing_helpers.py` and `test_chat_runtimes.py` were deleted with the code they covered; `test_graph_queries.py` lost the three tests for `min_confidence` / `find_paths_between_nodes` (APIs removed in `fbcafe7`); `test_qdrant_*.py` and `test_meili_contract.py` now set the backing `_client` attribute instead of the read-only lazy `client` property; `test_model_formats.py` shrank to the GGUF cases; `test_credentials.py` fakes `keyring` (it used to write into the developer's real keychain); the image-title stub speaks `ingestion_generate_with_meta`; the whitespace-query assertion in `test_chat_context.py` matches the stripping behaviour.

## 4. `conftest.py` — fixtures and what they stub

`backend/tests/unit/conftest.py` is the only conftest (the `tests/integration/` package and its conftest were deleted in `335a253`). It holds a single fixture:

| Fixture | Scope | What it does |
|---|---|---|
| `patch_settings` | function, **autouse** | `monkeypatch.setattr(config.settings, "LLM_PROVIDER", "local", raising=False)` — pins the provider; `test_kb_llm_config.py` overrides it with its own autouse fixture. |

The Joint-Approach-era `mock_llm_service` / `mock_graph_service` / `mock_meili_service` / `mock_typesense_service` / `mock_qdrant_service` fixtures and the `make_node` / `make_relationship` helpers stubbed methods that no longer existed and were used by no test; they and the redundant `event_loop_policy` override were deleted on 2026-09-20.

The pattern the live tests rely on is three idioms, documented in §13:

1. `Service.__new__(Service)` to bypass `__init__` (which opens clients), then assign the exact attributes the method under test reads (`client`, `_enabled`, `collection`, `_index`, `_db`, `_conn`, `provider`, `_chat_model_override`, `_chat`).
2. `unittest.mock.patch.object(svc, "method", return_value=…)` to cut off the next layer down (`_reason_step_sync`, `execute_query`, `resolve_node_id`).
3. Module import under `patch("kuzu.Database")`/`patch("kuzu.Connection")` after `sys.modules.pop("app.services.graph")`, so a module-level singleton constructor never touches disk.

## 5. Unit test modules — contract pinned and why it matters

46 modules under `backend/tests/unit/`. "Status" is against the current sources.

| Module | Target | Tests | Status |
|---|---|---|---|
| `test_chat_context.py` | `LLMService.rewrite_follow_up_query` | 6 | pass |
| `test_extraction_schemas.py` | `app/schemas/extraction.py` validators, `RELATIONSHIP_TYPES` coercion | 16 | pass |
| `test_graph_layout.py` | `app/utils/graph_layout.py` | 19 | pass |
| `test_graph_queries.py` | `GraphService.get_related_nodes` | 3 | pass |
| `test_llm_json_cleaning.py` | `LLMService._clean_json` | 16 | pass |
| `test_meili_contract.py` | `MeilisearchService.index_node` (primary key, `isolated_contexts` list) | 2 | pass |
| `test_qdrant_contract.py` | `QdrantService.upsert_node_core` / `search_node_cores` | 13 | pass |
| `test_extraction_chunking.py` | `app/workflows/extraction_chunking.py` | 15 | pass |
| `test_ingestion_chunked_extraction.py` | `ingestion_agent._extract_with_chunking`, `_batch_image_titles` | 4 (async) | pass |
| `test_kb_llm_config.py` | `kb_registry.effective_llm_config`, `LLMService.get_chat_model/get_ingestion_model` | 18 | pass |
| `test_local_runtime_budget.py` | `LocalLlamaRuntime` budgeting / GGUF resolution / `response_format` pass-through / eager projector init | 17 | pass |
| `test_model_load_clock.py` | `ModelLoadClock` | 4 | pass |
| `test_vault_migration.py` | `vault_sync.migrate_vault_files` (one-time sweep v1/v2/v3 + marker) | 3 | pass |
| `test_note_created_at.py` | `schemas.note` / `POST /ingest` `created_at` parsing | 4 | pass |
| `test_ingestion_community_names.py` | `IngestionWorkflow._name_and_summary`, `_name_fits_members` | 2 | pass |
| (31 further modules) | credentials, model discovery/catalog/formats, GGUF metadata, ASR engine, attachments (link parsing, processing, subfolders, upload folder, vault folders + the `attachments/` boundary), extraction budget/placement, ingestion checkpoint/cancel/reset, KB finance toggle, desktop runtime (incl. Firefly `.env` quoting), vision routing (`find_mmproj`), … | 300+ | pass |

### 5.1 `test_chat_context.py` — follow-up query rewriting

Contract pinned for `LLMService.rewrite_follow_up_query(history, latest_query, model=None)` (in `app/services/llm.py`):

- Returns `latest_query` untouched when `history` is empty (no LLM call).
- Only the last `settings.CHAT_HISTORY_MAX_MESSAGES` turns are used (via `schemas/chat.render_history`); turns whose `role` is not `user`/`assistant`/`summary` or whose `content` is empty are dropped from the prompt (`"ignored"` must not appear; `"Fido overview."` must). `test_chat_summary.py` covers the rolling summary: `refresh_summary` runs only for messages that left the window, passes only the uncovered ones, and `get_recent_history` prepends the `summary` turn.
- Turn content longer than 600 characters is truncated to 597 + `"..."` (test uses 800 × `x`).
- The rewrite goes through `self._reason_step_sync(prompt, model=model)`; the tests patch that method so no provider is contacted.
- On any exception from the LLM the original query is returned (never raise into the chat endpoint).
- Accepted rewrites are stripped of surrounding quotes and rejected (falling back to the original) if longer than 300 characters — not tested.

Why it matters: this is the only guard that multi-turn chat (`POST /api/v1/chat` with `conversation_id`, added in `ac7f4e4`, 2026-07-02) degrades to single-turn retrieval instead of failing when the local model is busy or offline. See [Retrieval and chat](16-retrieval-and-chat.md).

`test_returns_latest_when_empty_query` matches the stripping behaviour (`latest = (latest_query or "").strip()` → `""`); the older assertion that expected `"   "` back was fixed on 2026-09-19.

### 5.2 `test_extraction_schemas.py` — tolerant Pydantic validators

Pins the pre-validators in `app/schemas/extraction.py` that absorb the many shapes local models emit for the extraction JSON (every benchmark report in `Results/` lists a different malformation — LM Studio `json_object` fallbacks, "relationships as plain strings", `question_attribute` as a list):

| Class / validator | Contract |
|---|---|
| `Node.handle_none` (`field_validator("*")`, `TestNodeHandleNone`) | `None` → `""` for every field except `type` → `"thing"`; a valid `type` is preserved. |
| `ExtractedRelationship.closed_vocabulary` (`TestRelationshipTypeVocabulary`) | `None`/empty and unknown predicates → `"related_to"`; a listed predicate is normalised (case, whitespace → `_`), not rejected; every entry of `RELATIONSHIP_TYPES` round-trips unchanged. |
| `Extraction.normalize_keys` | `None` → empty; unwrap `{"extraction"|"data"|"result": {...}}` when the inner dict has `nodes` or `relationships`; Gemma two-list `[nodes, rels]`; bare list of dicts/strings → nodes (strings become `{"name": s}`); a node's embedded `"relationships"` list is hoisted to the top level. |
| `Extraction.ensure_list` | `nodes`/`relationships` `None` or scalar → `[]`; string items in `nodes` → `{"name": …}`. |

Why it matters: the ingestion pipeline (`app/workflows/agents/ingestion_agent.py`, see [Ingestion pipeline](10-ingestion-pipeline.md)) calls `Extraction.model_validate` on whatever `_clean_json` returns; a validator regression turns into "note failed" for a whole model family. Per-edge scores (`strength`/`confidence`/`relevance`) no longer exist on the schema (doc 26 D2), so there is no score scale to preserve.

### 5.3 `test_graph_layout.py` — deterministic 3-D layout

Pure functions in `app/utils/graph_layout.py` used by `GET /api/v1/graph/3d` (see [Frontend chat, graph and pages](20-frontend-chat-graph-and-pages.md)):

- `_fibonacci_sphere(n, radius)`: `[]` for `n ≤ 0`; exactly `n` distinct points all within 1 % of `radius`.
- `_deterministic_jitter(seed, max_offset)`: same seed → same tuple; different seeds differ; each component within `±max_offset`; `0.0` offset → zeros.
- `compute_solar_positions(communities, node_level_map, all_node_ids)`: every node id and every `community_id` gets a position; level-0 community centres sit at ≈`SOLAR_UNIVERSE_RADIUS` (1800.0, `rel=0.05`); nodes with no community ("orphans") still get positions; output is identical across calls.
- `compute_spring_layout_3d(nodes, edges, iterations=…)`: `{}` for no nodes; a single node at the origin; deterministic; every node positioned.

Why it matters: the graph canvas must not reshuffle on every reload, so no `random` without a seed anywhere in this module.

### 5.4 `test_graph_queries.py` — Cypher-shape regression guards

Built around `_get_graph_service_class()` (re-imports `app.services.graph` with `kuzu.Database`/`kuzu.Connection` patched, because the module constructs a singleton at import) and `_make_graph_service()` (instance via `__new__` with `_db`/`_conn` mocks).

Contracts:

- **depth = 1 fast path** must not use variable-length `*1..` syntax nor `all(`. Still true: `get_related_nodes(max_depth=1)` now issues two directed queries from `_hop_query("->")` and `_hop_query("<-")`, tags rows with `edge_direction`, dedupes by `node_id` (outgoing wins) and sorts by name. The assertion `result == expected` still holds only because the mock returns the same list object for both calls.
- **depth > 1** must not put `all(rel IN relationships(path) WHERE …)` in the `WHERE` clause — the code comment explains it "triggers a `KU_UNREACHABLE` parser assertion in this Kuzu build". Still true; confidence is returned as `confidence_path` and (per the comment) filtered in Python.
- The `min_confidence` post-filter and `find_paths_between_nodes` tests were removed on 2026-09-19 together with the APIs they pinned (both deleted in `fbcafe7`; the current signature is `get_related_nodes(node_name, max_depth=2, node_id=None)` and the depth>1 branch returns rows unfiltered). Check callers in `retrieval.py` before re-adding either.
- Unknown node (`resolve_node_id → None`) → `[]`. Passes.

Why it matters: Kuzu's Cypher dialect differs from Neo4j's; the Neo4j-era hyphenated-relationship-type errors (32 per run, `Results/Results (Sub Questions Approach)/…/GEMINI_INGESTION_REPORT.md`) and the Kuzu `KU_UNREACHABLE` assertion were both discovered by benchmark runs, and these tests are the only thing that stops the query text from drifting back. See [Graph storage (Kuzu)](14-graph-storage-kuzu.md).

### 5.5 `test_llm_json_cleaning.py` — `_clean_json`

`LLMService._clean_json(json_str) -> str`, in order: (1) if ` ``` ` present, take the first fenced block (` ```json ` or bare) with `re.DOTALL`; (2) map `‘ ’ ‛` → `'` and `“ ” „` → `"` (`json_repair` escapes stray control characters itself); (3) `json_repair.repair_json(...)` — a hard import. Tests cover fence extraction with surrounding prose, smart-quote normalisation, missing closing brace, trailing comma, and a combined case.

Why it matters: every structured LLM call (ingestion extraction, query analysis) goes through this; it is the first line of defence behind the validators in §5.2.

### 5.6 `test_meili_contract.py` — primary-key invariant

`_make_meili_service()` sets the backing `_client` (the `client` property is a lazy reconnecting getter), `index_name="test_nodes"`, `is_available`, and replaces `_index()` with a mock whose `add_documents` returns a task with `task_uid=1`. Contract:

- `index_node(node_id, name, node_type, isolated_contexts=None, relationship_natural_language="", community_level=None)` → document has `node_id` and `name`; a passed `isolated_contexts` list is stored as a list, not joined.
- Index errors are caught and logged — never raised into ingestion.

Why it matters: Meilisearch's index is created with `primaryKey: "node_id"`; a document without it is rejected, silently dropping the node from keyword search. See [Search indexes](15-search-indexes-qdrant-meilisearch.md).

### 5.7 `test_qdrant_contract.py` — payload and filter contract

`_make_service()` sets only `_enabled=True` and `client=MagicMock()`. Contracts for `upsert_node_core(node_id, name, node_type, description_vector, description="", community_level=None, extra_payload=None)` (tests pass everything by keyword, so the positional order change is harmless):

- Payload always has `node_id`, `name`, `type`; `description` only when truthy; `community_level` only when not `None`; `extra_payload` merged last (can override).
- `_enabled=False` → `client.upsert` not called; `client=None` → returns without raising (both via `is_available()`).
- `search_node_cores(query_vector, limit, min_score, node_type=None, community_level=None)` → `client.query_points(..., query_filter=…)` where the filter is `None` when neither filter is set, else a `Filter(must=[FieldCondition(key="type"|"community_level", match=MatchValue(...))])`; disabled → `[]` and no call.

Caveat: the real methods also read `self._col_cores` (collection name) and call `self._prepare_vector`. If those are instance attributes assigned in `__init__`, `_make_service()` must set them too; verify when you next run the suite.

Why it matters: the `type` and `community_level` payload keys are the filter keys retrieval relies on; renaming either breaks entity-type-filtered search silently.

### 5.8 `test_relationships.py` — deleted 2026-09-19

It imported `can_evolve`, `get_contradicting_types`, `get_expected_relationships`, `get_inverse_type`, `is_bidirectional` from `app/schemas/relationships.py` — the bi-temporal relationship ontology added in `033589d` (2026-02-01) and deleted in `da75dfc` (2026-05-28) — and errored at collection for four months before it was removed with the other stale modules (§3.4).

### 5.9 `test_extraction_chunking.py` (new) — paragraph-bounded chunking and merge

Targets `app/workflows/extraction_chunking.py`:

- `split_for_extraction(text, budget_tokens, count_tokens) -> list[str]`: short text returned as one chunk; whitespace-only → `[]`; splits on `\n\n` paragraph boundaries first, packing whole paragraphs up to the budget; an oversized paragraph falls back to sentence splitting (chunks end with `.`); no chunk is empty; the concatenation of all chunks preserves every word in order.
- `chunk_token_budget(context_tokens, expected_output_tokens) -> int`: `budget × 3.5 ≤ context − output` (input plus ~2.5× output must fit the window); capped at **4000**; floored at `MIN_SPLIT_TOKENS`; the `EXTRACTION_CHUNK_TOKENS` setting overrides the cap in either direction.
- `merge_extractions(parts) -> Extraction`: `None` parts skipped; nodes deduplicated case-insensitively by name with `isolated_context` concatenated (duplicates not repeated); a generic `"thing"` type is upgraded by a later specific type; relationships deduplicated by case-insensitive `(source, target, relationship_type)`, first occurrence wins (there are no per-edge scores); the first non-empty `title` wins.

### 5.10 `test_ingestion_chunked_extraction.py` (new) — the agent's chunk/retry loop

Imports `app.workflows.agents.ingestion_agent` directly (the package `__init__` no longer re-exports anything). A `_StubLLM` documents the **duck-typed LLM protocol** the agent needs: `provider`, `ingestion_count_tokens(text)`, `ingestion_context_tokens()`, `get_ingestion_model()`, `_clean_json(raw)`, `async ingestion_generate_with_meta(prompt, temperature, max_tokens) -> (content, {"finish_reason", "truncated"})`, `async ingestion_generate(prompt, temperature, max_tokens)`. The stub finds the note as the text after the prompt's final `"nothing else:\n\n"` — a coupling to the extraction prompt's last line.

Contracts for `await agent._extract_with_chunking(llm, note_text, images) -> (Extraction, chunk_count)`:

- A short note is a single LLM call, `chunk_count == 1`.
- A 1000-word, 20-paragraph note with `EXTRACTION_CHUNK_TOKENS=400` produces ≥3 chunks, one call per chunk, and the merged extraction has exactly 1000 nodes (nothing lost, nothing duplicated) with the first chunk's title.
- When a chunk's response comes back `truncated=True` (unparseable JSON, `finish_reason="length"`), the agent halves the chunk and re-extracts **without sleeping** (`asyncio.sleep` is patched and must not be called) and without incrementing `chunk_count`; exactly one oversized call is made.
- `await agent._batch_image_titles(llm, items) -> dict[token, title]`: parses a JSON array (tolerating leading prose like `"Sure! "`), maps 1-based `index` to the `{{ORB_IMAGE_TITLE_n}}` tokens, drops empty titles and out-of-range indices, and returns `{}` on any exception.

Why it matters: this is the fix for the "Invalid JSON: EOF while parsing a value" ingestion failures recorded in `Results/Results (After Optimizations)/Gemma4_E4B_Logs/errors.log` and the Pydantic "relationships as strings" failure in the Final Implementation ingestion — small local context windows truncate long notes. See [Ingestion pipeline](10-ingestion-pipeline.md) and [Multimedia enrichment](11-multimedia-enrichment.md).

### 5.11 `test_kb_llm_config.py` (new) — per-KB model overrides

- `kb_registry.effective_llm_config(kb_row) -> {"provider", "model", "ingestion_model", "inherited"}`: `{}` or blank strings → inherit system settings (`inherited=True`), model = `CHAT_MODEL` if set else `LLM_MODEL`, ingestion follows the chat model; `llm_model` alone keeps the system provider and pins both chat and ingestion; `llm_provider` alone switches to that provider's system default model (`GEMINI_MODEL` for gemini).
- `LLMService.get_chat_model()` / `get_ingestion_model()`: `_chat_model_override` beats even a global `CHAT_MODEL`; `_ingestion_model_override` is independent; with no override, ingestion falls back to the pinned chat model, then settings. An instance created with `__new__` and **no** override attributes still works (the getters use safe attribute access).

Why it matters: the KB → provider/model resolution is what `?kb=` selects on every chat/ingest call; see [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) and [LLM providers and prompting](13-llm-providers-and-prompting.md). Model ids appearing here (`gemma4-12b-q4`, `gemma4-e4b-q4`, `qwen35-4b-q4`) are catalog ids from `app/services/model_catalog.py`.

### 5.12 `test_local_runtime_budget.py` (new) — llama.cpp budgeting without llama.cpp

A `_FakeLlama` exposing only `n_ctx()` and `tokenize()` is injected as `runtime._chat`.

- `LocalLlamaRuntime._remaining_output_budget(messages)` = `n_ctx − (Σ tokens + 8 per message + 4) − lm._GEN_SAFETY_MARGIN`; raises `lm.PromptTooLongError` when the prompt fills the window.
- `count_tokens(text)`: with no resident model uses the heuristic `len(text) // 4 + 1` (`""` → 0); with a model uses its tokenizer.
- `lm._default_chat_max_tokens()`: env `LLAMA_MAX_TOKENS` — unset → `None` (dynamic budget), integer → that cap, garbage → `None`.
- `resolve_chat_gguf(model_id)`: `None`/`""`/`"local-chat"` → `None` (meaning "use the Setup selection"); known catalog id not on disk → `RuntimeError("… not downloaded")`; known id present (`_gguf_looks_complete` true) → `resolve_models_dir()/gguf/<option.hf_file>`; an embedding id (`qwen3-embed-0.6b-q8`) → `RuntimeError("… not a chat model")`; an explicit existing `.gguf` path → that `Path`; unknown name → `None`.

Why it matters: these are the invariants behind "one heavy model resident at a time" and the dynamic `max_tokens` that keeps long chats from overflowing the context — see [Local models and inference](12-local-models-and-inference.md) and [Data directory layout](22-data-directory-layout.md) for `models_dir/gguf/`.

### 5.13 `test_model_load_clock.py` (new) — load-time accounting

`ModelLoadClock.record(kind, seconds)`, `snapshot()`, `ModelLoadClock.diff(before, after) -> {"total_seconds", "seconds": {kind: s}, "counts": {kind: n}}` (only loads inside the window; tolerates `{}` as `before`), and `describe(delta)` → `"none"` or a stable, sorted, compact string like `"chat×1,rerank×2"`. Used to separate model-load time from inference time in chat/ingestion logs — see [Logging and observability](23-logging-and-observability.md).

## 6. Lint and type tooling

None of the tools below run in CI. They are developer-invoked only.

### 6.1 Backend — pylint (`backend/.pylintrc`)

Introduced in commit `335a253` (2026-05-15, "Remove feedback feature; add linting & refactors"), which also added the `# pylint: disable=…` inline directives found throughout `backend/app`.

| Section | Key | Value | Effect / rationale |
|---|---|---|---|
| `[MAIN]` | `init-hook` | `import sys; sys.path.insert(0, ".")` | Lets pylint resolve `app.*` imports when run from `backend/`. Run as `cd backend && pylint app`. |
| `[MESSAGES CONTROL]` | `disable` | `logging-fstring-interpolation`, `logging-not-lazy` | **Intentional decision** documented in the file: f-strings in `logger.*()` calls are kept for readability; the lazy-`%` micro-optimisation only matters when suppressed log levels are hit at high frequency, which "neither condition applies here". Do not "fix" f-string logging in this codebase. |
| `[FORMAT]` | `max-line-length` | `120` | Matches the existing code; do not reflow to 79/88. |
| `[DESIGN]` | `max-attributes` | `20` | Service singletons (`LLMService`, `QdrantService`, …) carry many attributes. |
| `[DESIGN]` | `max-public-methods` | `30` | Same reason. |
| `[SIMILARITIES]` | `min-similarity-lines` | `10` | Duplicate-code detector threshold. |

Inline suppressions you will see and should keep consistent when adding code: `# pylint: disable=too-many-arguments,too-many-positional-arguments` on wide service signatures (`index_node`, `upsert_node_core`, `search_node_cores`), `# pylint: disable=broad-exception-caught` on the many "log and continue" handlers, `# pylint: disable=too-many-locals,too-many-branches,too-many-statements` on the layout and retrieval functions.

`pylint` is **not** listed in `backend/requirements.txt`; install it into the venv separately. There is no `ruff`, `black`, `mypy` or `isort` configuration anywhere in the repo (the `.gitignore` entries for `.ruff_cache/` and `.mypy_cache/` are generic boilerplate).

### 6.2 Frontend — ESLint (`frontend/eslint.config.mjs`)

Flat config (ESLint 9, `defineConfig`/`globalIgnores` from `eslint/config`):

```js
export default defineConfig([
  globalIgnores(["dist/**"]),
  { files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    languageOptions: { globals: globals.browser },
    rules: { "@typescript-eslint/no-unused-vars": ["warn", {
        vars: "all", args: "after-used", ignoreRestSiblings: true,
        argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "react-hooks/set-state-in-effect": "warn" } },
]);
```

- The only project-specific rule downgrades `no-unused-vars` to **warn** and whitelists `_`-prefixed identifiers, so `catch (_e)` / `(_req, res)` patterns are lint-clean.
- Invocation: `cd frontend && npm run lint` (`eslint .`). Versions pinned in `frontend/package.json`: `eslint ^9`, `typescript-eslint ^8`, `eslint-plugin-react-hooks ^7`, `typescript ^6`.
- No Prettier config; formatting is not enforced.

### 6.3 Frontend — TypeScript strictness (`frontend/tsconfig.json`)

| Option | Value | Note |
|---|---|---|
| `strict` | `true` | Full strict family (`strictNullChecks`, `noImplicitAny`, …). |
| `target` / `lib` | `ES2020` / `dom`, `dom.iterable`, `esnext` | |
| `module` / `moduleResolution` | `esnext` / `bundler` | Vite default. |
| `noEmit`, `isolatedModules`, `skipLibCheck`, `allowJs`, `esModuleInterop`, `resolveJsonModule` | `true` | Type-checking only; Vite/oxc does the transpile. |
| `jsx` | `react-jsx` | |
| `types` | `["vite/client"]` | `import.meta.env` typing. |
| `paths` | `"@/*": ["./src/*"]` | The `@/` alias used across `frontend/src`. |
| `include` | `src`, `vite.config.ts` | |

There is no separate `typecheck` script, but `npm run build` is `tsc --noEmit && vite build`, so type errors fail the build (and therefore `build.py prepare`). See [Frontend architecture](18-frontend-architecture.md).

### 6.4 What is *not* configured

| Tool | Status |
|---|---|
| Python formatter (black/ruff-format) | none |
| Python type checker (mypy/pyright) | none |
| Frontend unit tests (jest/vitest/@testing-library) | none — no dev dependency, no test files under `frontend/` |
| E2E (playwright/cypress) | none |
| Desktop (`desktop/`) tests or lint | none |
| Pre-commit hooks | none (`.pre-commit-config.yaml` absent) |
| CI test/lint job | none — `desktop-release.yml` only runs `build.py prepare` (which includes `npm run build` → `tsc --noEmit`), `build.py dist` and a release-draft job |



## 7. Testing gaps

| Gap | Detail | Risk |
|---|---|---|
| Unit suite needs a full environment | The suite imports every service module, so `requirements.txt` (including `llama-cpp-python`) must be installed alongside `requirements-dev.txt`; `backend/.venv` is partial. | New contributors must build a venv first. |
| Frontend tests cover pure logic only | `frontend/src/**/*.test.ts` (vitest 5, `environment: "node"`, `@` alias from `vitest.config.ts`) pin `lib/utils.ts` (file URLs, media sniffing incl. the deliberate mkv/avi exclusion, YouTube embeds, `errMessage`), `parse-note-attachments`, `wikilinks` (normalise/resolve/create-target), `folder-tree` and `rewrite-vault-urls`. No component, hook or DOM tests. | Editor (CodeMirror wikilink autocomplete), chat polling and graph canvas logic are still unverified; `parseSegments` in `components/segmented-note-content.tsx` is not exported, so it stays untested. |
| Desktop runtime partly covered, shell not at all | `tests/unit/test_desktop_runtime.py` (Meili key, status file, Firefly `.env` quoting) and `tests/integration/test_desktop_runtime_ports.py` (`_port` env override, `free_ports` never kills its own pid and counts strangers, Firefly `.env` under a temp data dir, sticky `APP_KEY`, mode 0600) import `app.desktop_runtime` without running `main()`. `desktop/src-tauri` has no tests; sidecar boot and downloads are exercised only by hand. | |
| Integration smoke only, stores offline | `tests/integration/test_api_smoke.py` boots the real app with `TestClient` against a temp `DATA_DIR` with `QDRANT_PORT`/`MEILI_PORT` pointed at a dead port: `/health`, KB list/create, note create → vault file → GET round-trip → PUT → DELETE, `/vault/folders`, upload into `attachments/<folder>/`, and the 400 on moving across the `attachments/` boundary. Marked `integration`; `pytest.ini` sets `testpaths = tests/unit` so it only runs when asked. | Kuzu/Qdrant/Meili write paths, vault sync and ingestion status transitions are still covered only by the benchmark scripts, which need a full running app and an LLM. |
| No coverage, no type checking, no formatter | See §6.4. | |

CI: `.github/workflows/ci.yml` (added 2026-09-20) runs on every push to `main` and every pull request — `backend` (Python 3.12, `pip install --prefer-binary` of both requirements files with `CMAKE_ARGS=-DGGML_NATIVE=OFF` so `llama-cpp-python` builds a portable CPU wheel, `pytest tests/unit -q` then `pytest tests/integration -q` as a separate process, each with its own `DATA_DIR` under `runner.temp`), `frontend` (Node 24, `npm ci`, `npm run lint`, `npm run build` — which includes `tsc --noEmit` — and `npm test`, vitest) and `desktop` (`cargo check --locked` in `desktop/src-tauri` on ubuntu-22.04 with the release job's webkit packages). The backend job's first run compiles llama.cpp (~8–10 min); later runs hit the pip cache and take ~2 min. `pylint` is still not run in CI.

## 8. How to add tests for a new service

Follow the idioms the passing modules use.

1. **File placement and naming**: `backend/tests/unit/test_<area>.py`, test classes `Test<Behaviour>`, functions `test_<expectation>`. Keep one module per production module. Module docstring: what is under test and why no I/O is needed.
2. **Instantiate without `__init__`**:

   ```python
   from app.services.my_service import MyService
   def _make_service():
       svc = MyService.__new__(MyService)   # skip client construction
       svc.client = MagicMock()
       svc._enabled = True
       # set every attribute the methods under test read (check __init__)
       return svc
   ```

   If the module builds a singleton at import time (like `app.services.graph`), import it under `patch(...)` and pop it from `sys.modules` first (see `_get_graph_service_class`).
3. **Cut the next layer with `patch.object`** (`_reason_step_sync`, `execute_query`, `resolve_node_id`) rather than mocking HTTP libraries; assert on the *arguments* passed downward (query text, payload dict, filter object) — that is the contract worth pinning.
4. **Async code**: use `@pytest.mark.asyncio` on `async def` tests (strict mode; no ini file sets `asyncio_mode=auto`). For LLM-driven code, write a small duck-typed stub class (see `_StubLLM`) implementing only the methods the code calls — and update it when the `LLMService` protocol changes.
5. **Settings and env**: use `monkeypatch.setattr(config.settings, "KEY", value, raising=False)` and `monkeypatch.setenv/delenv`. `patch_settings` already pins `LLM_PROVIDER="local"`.
6. **Filesystem**: `tmp_path`, and patch resolver functions (`resolve_models_dir`, `_gguf_looks_complete`) instead of touching `DATA_DIR`.
7. **Determinism**: no wall-clock, no randomness (see `graph_layout` tests), no sleeps (patch `asyncio.sleep` and assert it was not called, as the chunking test does).
8. **Run it**: `cd backend && python -m pytest tests/unit/test_<area>.py -q`.
9. **When you remove or rename a production function**, grep `backend/tests/unit` for it in the same commit (the three stale modules exist because this step was skipped in `da75dfc` and `fbcafe7`).
10. **Response-shape changes**: if you change the chat response shape (`answer`, `context[].text/linked_notes/note_id/title`) or the notes status fields (`processed`, `failed`), update the harness on the `orb-testing` branch to match.

### 8.1 Running each suite

```bash
# Backend unit (default: pytest.ini has testpaths = tests/unit, asyncio_mode = strict)
cd backend && DATA_DIR=/tmp/orb-test ORB_DATA_DIR=/tmp/orb-test python -m pytest -q

# Backend integration (opt-in; run in its own process — the app binds DATA_DIR at import)
cd backend && python -m pytest tests/integration -q          # ~2 s, no Qdrant/Meili/models needed

# Frontend (vitest, node environment, pure modules only)
cd frontend && npm test                                       # = vitest run
cd frontend && npx vitest src/lib                             # watch one folder
```

`test_api_smoke.py` skips itself if `app.core.config` was already imported in the same process (e.g. `pytest tests`), because `DATA_DIR`, the SQLite engine and the Kuzu file are bound at import time.

## 9. Invariants, gotchas and non-obvious behaviours

- **`Service.__new__` is the test contract.** Any attribute a method reads that is normally assigned in `__init__` (`client`, `_enabled`, `_col_cores`, `collection`, `_conn`, `_chat`, `_chat_model_override`) is part of the implicit interface the tests set up by hand; renaming one breaks tests without touching behaviour.
- **`patch("kuzu.Database")` imports kuzu.** The graph tests are not free of the native dependency, only of a database file.
- **Kuzu query text is pinned**: never reintroduce `WHERE all(...)` over `relationships(path)` or `*1..N` in the depth-1 path — `KU_UNREACHABLE` is a hard crash, not an exception.
- **Extraction validators are order-sensitive**: `Extraction.normalize_keys` (before) → `ensure_list` (before, per field) → `Node`/`ExtractedRelationship` validators. Wrapper unwrapping only happens when the inner dict has `nodes` or `relationships`.
- **`_clean_json` picks the *first* fenced block** — a response containing two fences returns only the first.
- **Meilisearch documents must carry `node_id`** (index primary key); `update_nodes_community` synthesises `{"node_id": …}` if `get_node` returns nothing.
- **Retrieval F1 is F1 of means**, not mean of F1s.
- **Results JSON lacks per-case F1/contains** — do not try to recompute report tables from it alone.
- **`*.log` is gitignored except under `Results*/`** — a new results folder must be named `Results…` at the repo root (or the `Results/` subfolders) for its logs to be tracked.
- **Log analysis scripts used for the reports (`analyze_test_logs.py`, `batch_ingest.py`, `backfill_failed_relationships.py`, `run_community_detection.py`) are not in the repo** (some were pruned in `3ab4f9d`, 2026-03-04, and `fbcafe7`; `run_community_detection.py` was removed on 2026-09-19).

## 10. History / rationale

| Date | Commit | Testing / benchmark relevance |
|---|---|---|
| 2026-01-17 → 01-30 | `6fd0224` … `401e570` | Working versions 1–3; first reranker config keys (`RERANK`, `6fd0224`). |
| 2026-02-01 | `033589d` | Bi-temporal relationships + symbolic ranking; introduces `app/schemas/relationships.py` and `find_paths_between_nodes`. |
| 2026-02-05/06 | `b3fd891`, `a0fea5e`, `20f043b`, `044052a` | "Benchmark testing" — `tests/benchmark/` (manifests, `prepare_dataset.py`, `evaluate.py`) created; `BENCHMARK_MODE` added. |
| 2026-02-19, 02-24 | `536cbd4`, `38c9b5b` | HotpotQA reports (Sub-Questions era) and optimisation pass. |
| 2026-03-04 | `3ab4f9d` | "Prune scripts/tests, add results" — analysis scripts dropped, Looping results added. |
| 2026-04-18/19 | `559e988`, `097c822` | Joint Approach: graph expansion in retrieval, reranker/extraction improvements. |
| 2026-05-07 | `68494b7` | Final Implementation: Kuzu + Typesense migration; `MAX_LOOP_ITERATIONS`, `RERANKER_*` config; conftest rewritten (Typesense fixture). |
| 2026-05-10, 05-13 | `6ed2eb0`, `2655bb8`, `6b6bd54`, `09e35e3` | Final-run logs archived; README; results reorganised into the current `Results/` folders; config accidentally deleted and restored; ingestion-specific LLM clients and the `local` provider alias. |
| 2026-05-15 | `335a253` | Feedback feature removed; `.pylintrc`; `tests/integration/` and `test_llm_contracts.py` deleted; `test_extraction_schemas.py`, `test_graph_layout.py`, `test_llm_json_cleaning.py`, `test_qdrant_contract.py`, `test_relationships.py` added. |
| 2026-05-18/19 | `e6ce10d`, `8eba91d`, `948aa33`, `acb19a5` | "Final Tests" (After-Optimizations run archived); benchmark mode + refiner removed (conftest monkeypatch dropped); chat context / segmented notes. |
| 2026-05-28 | `da75dfc` | `app/schemas/relationships.py` deleted → `test_relationships.py` orphaned. |
| 2026-06-12 | `a8587e6`, `4408a72` | Local model services + ingestion status; async chat polling (Results folder touched for report links). |
| 2026-07-02 | `ac7f4e4` | Persistent conversations + follow-up rewrite → `test_chat_context.py`. |
| 2026-08-02/03 | `3f21e08`, `6162be2`, `fbcafe7` | Docker-free desktop app; LifeOS/LiveOS → Orb rename across `Results/` and tests; legacy paths dropped — `find_paths_between_nodes` and `min_confidence` removed from `graph.py` (orphaning half of `test_graph_queries.py`), benchmark notes/cache/progress untracked, `fetch_notes.py` added, README rewritten. |
| 2026-08 → 09 | `b4d14cd`… | Five new unit modules for chunked extraction, per-KB LLM config, llama.cpp budgeting and the model-load clock. |
| 2026-09-19 | `1d5c7d7`, `69330c7` | Over-engineering sweep and band-aid removal: stale modules deleted (`test_relationships.py`, `test_timing_helpers.py`, `test_chat_runtimes.py`), `test_vault_migration.py`, `test_note_created_at.py`, `test_ingestion_community_names.py` added; 455 → 473 passing. |
| 2026-09-20 | `f54809b` … `5a60d40` | Release prep: `test_upload_folder.py`; new cases for the `attachments/` boundary (`test_vault_folders.py`), the v2/v3 sweeps (`test_vault_migration.py`), eager projector init and strict `find_mmproj` (`test_local_runtime_budget.py`, `test_vision_routing.py`), Firefly `.env` quoting (`test_desktop_runtime.py`); 485 passing. |

Why the archive is kept in git despite its size: the reports are the only record of *why* the retrieval loop, reranker thresholds, Kuzu migration and model choices look the way they do; the raw logs let the numbers be re-derived. Why unit tests avoid infrastructure entirely: the project has no CI runner with Qdrant/Meilisearch, and the desktop packaging bundles its own binaries, so "fast, deterministic, import-only" was the only feasible contract (conftest docstring).
