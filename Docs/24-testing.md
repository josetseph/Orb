# Testing

**What this covers.** Everything that verifies Orb's behaviour: the pytest unit suite under `backend/tests/unit/` (what each module pins, what `conftest.py` stubs and why), the lint/type tooling (`backend/.pylintrc`, `frontend/eslint.config.mjs`, `frontend/tsconfig.json`), the current testing gaps (no CI test job, no frontend tests, stale unit tests), and how to add tests for new services following the existing patterns.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Packaging, build and release](05-packaging-build-and-release.md) · [Backend core and configuration](06-backend-core-and-configuration.md) · [API reference](07-api-reference.md) · [Notes, wikilinks and vault files](09-notes-wikilinks-and-vault-files.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Local models and inference](12-local-models-and-inference.md) · [LLM providers and prompting](13-llm-providers-and-prompting.md) · [Graph storage (Kuzu)](14-graph-storage-kuzu.md) · [Search indexes (Qdrant / Meilisearch)](15-search-indexes-qdrant-meilisearch.md) · [Retrieval and chat](16-retrieval-and-chat.md) · [Configuration reference](21-configuration-reference.md) · [Logging and observability](23-logging-and-observability.md) · [Development history](25-development-history.md) · [Decisions and constraints](26-decisions-and-constraints.md) · [Development guide](27-development-guide.md) · [Glossary](28-glossary.md)

---

## 1. Responsibilities and boundaries

| Owns | Does NOT own |
|---|---|
| `backend/tests/unit/` — the only automated test suite in the repository (pytest, pure-Python, no live infrastructure). | Runtime behaviour of the services under test — see the per-subsystem docs linked above. |
| Lint/type configuration: `backend/.pylintrc`, `frontend/eslint.config.mjs`, `frontend/tsconfig.json`. | CI enforcement — there is none. `.github/workflows/desktop-release.yml` is the only workflow and it builds installers only (see [Packaging, build and release](05-packaging-build-and-release.md)). |

Boundary facts that matter when modifying code:

- Unit tests never start Qdrant, Meilisearch, Kuzu, SQLite, or an LLM. Every service is instantiated with `Class.__new__(Class)` to skip `__init__` (which would open connections), then the attributes the method under test reads are set by hand. Any change to a service's `__init__`-assigned attribute names (`client`, `_enabled`, `collection`, `_index`, `_db`, `_conn`) silently breaks the corresponding test helper.
- The harness is single-KB: it never sends `?kb=`, so it operates on whatever the backend resolves as the default KB. Run it against a dedicated KB by making that KB the default before starting (see [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md)).


## 2. Files

| Path | Purpose | Key exports / contents |
|---|---|---|
| `backend/tests/__init__.py`, `backend/tests/unit/__init__.py` | Make `tests` a package so pytest's default (`prepend`) import mode inserts `backend/` on `sys.path`; `app.*` imports then work from any cwd. | — |
| `backend/tests/unit/conftest.py` | Shared fixtures: autouse settings patch, `MagicMock`/`AsyncMock` service stubs, sample-data helpers. | `event_loop_policy`, `patch_settings` (autouse), `mock_llm_service`, `mock_graph_service`, `mock_meili_service`, `mock_typesense_service` (alias), `mock_qdrant_service`, `make_node()`, `make_relationship()` |
| `backend/tests/unit/test_chat_context.py` | Follow-up query rewrite (`LLMService.rewrite_follow_up_query`). | 6 tests in `TestRewriteFollowUpQuery` |
| `backend/tests/unit/test_extraction_schemas.py` | Pydantic pre-validators in `app/schemas/extraction.py` (key aliasing, `None` handling, score normalisation, wrapper unwrapping). | 5 classes, ~33 tests |
| `backend/tests/unit/test_graph_layout.py` | Pure geometry in `app/utils/graph_layout.py`. | `TestFibonacciSphere`, `TestDeterministicJitter`, `TestComputeSolarPositions`, `TestComputeSpringLayout3d` |
| `backend/tests/unit/test_graph_queries.py` | Cypher-shape regression guards for `GraphService.get_related_nodes` (and the since-removed `find_paths_between_nodes`). | `_get_graph_service_class()` (imports `app.services.graph` with `kuzu.Database`/`kuzu.Connection` patched), `_make_graph_service()`, `_row()` |
| `backend/tests/unit/test_llm_json_cleaning.py` | `LLMService._clean_json` (fence stripping, control chars, smart quotes, `json_repair`). | 4 classes, 15 tests |
| `backend/tests/unit/test_meili_contract.py` | Meilisearch document contract: `node_id` (primary key) always present in `index_node` / `update_node_community` payloads; errors logged not raised. | `_make_meili_service()` |
| `backend/tests/unit/test_qdrant_contract.py` | Qdrant payload/filter contract for `upsert_node_core` and `search_node_cores`. | `_make_service()` |
| `backend/tests/unit/test_relationships.py` | Relationship-type ontology helpers (`can_evolve`, `get_inverse_type`, …) from `app/schemas/relationships.py` — **module no longer exists** (deleted in `da75dfc`, 2026-05-28). | Fails at collection with `ModuleNotFoundError`. |
| `backend/tests/unit/test_extraction_chunking.py` (untracked) | Paragraph-bounded chunking, token budget, and extraction merge in `app/workflows/extraction_chunking.py`. | `TestSplitForExtraction`, `TestChunkTokenBudget`, `TestMergeExtractions` |
| `backend/tests/unit/test_ingestion_chunked_extraction.py` (untracked) | Chunk/truncate/retry loop and batched image titling in `ingestion_agent`; documents the duck-typed LLM protocol via `_StubLLM`. | 4 async tests |
| `backend/tests/unit/test_kb_llm_config.py` (untracked) | Per-KB provider/model override resolution (`kb_registry.effective_llm_config`) and `LLMService` model getters. | `TestEffectiveLLMConfig`, `TestLLMServiceOverrides` |
| `backend/tests/unit/test_local_runtime_budget.py` (untracked) | `LocalLlamaRuntime` output budgeting, token counting, `ORB_LLAMA_MAX_TOKENS`, GGUF resolution. | `TestOutputBudget`, `TestMaxTokensEnv`, `TestResolveChatGguf` |
| `backend/tests/unit/test_model_load_clock.py` | `ModelLoadClock` snapshot/diff/describe. | `TestModelLoadClock` |
| `backend/tests/unit/test_gguf_metadata.py` | GGUF header parsing against **synthesised** fixtures (`build_gguf` writes spec-conformant bytes), including the guards for corrupt/hostile headers and the `pooling_type` vs `chat_template` role distinction. | 24 tests |
| `backend/tests/unit/test_model_discovery.py` | Disk scanning: shard grouping, AppleDouble/partial/dotfile filtering, venv + depth pruning, `MODELS_DIR`-relative refs, the metadata cache, `inspect_chat_model`. | 31 tests |
| `backend/tests/unit/test_byo_model_selection.py` | End-to-end bring-your-own model: `resolve_chat_gguf` for catalog ids / relative refs / absolute paths, loud failure for missing or unusable files, `n_ctx` clamping, KB-API validation, payload listing. | 18 tests |
| `backend/tests/unit/test_credentials.py` | `CredentialStore` semantics, env seeding and precedence, version bumps, endpoint URL identity, and the invariant that **no status output contains key material**. | 24 tests |
| `backend/tests/unit/test_finance_chat_llm.py` | Finance synthesis routes through the KB's own LLM and runs off the event loop (a concurrent poller must keep ticking). | 6 async tests |
| `backend/tests/unit/test_timing_helpers.py` | `[Timing]` line composition; load vs inference split never goes negative. | `TestLogStageTiming` |
| `backend/tests/unit/test_credentials.py`, `test_credentials_keyring.py` | `CredentialStore`: endpoint URL normalisation, env seeding, keychain persistence through `keyring` (mocked), session-only fallback when no backend is usable. | — |
| `backend/.pylintrc` | Backend lint policy. | see §6 |
| `frontend/eslint.config.mjs`, `frontend/tsconfig.json` | Frontend lint + TS strictness. | see §6 |
| `Results/Results (Sub Questions Approach)/…` | Feb 2026 experiments (Neo4j/Postgres era, sub-question decomposition retrieval). | 6 reports, 4 results JSON, 6 log folders |
| `Results/Results (Looping approach)/…` | Mar 2026 experiments (iterative retrieval loop). | 3 reports, 2 results JSON, 3 log folders |
| `Results/Results (Joint Approach)/…` | Mar–Apr 2026 (joint node+relationship extraction). | 1 report, 2 side-test results JSON, 3 log folders |
| `Results/Results (Final Implementation)/…` | May 2026 (Kuzu/Typesense migration, GGUF reranker). | 5 reports, 3 results JSON, 4 log folders |
| `Results/Results (After Optimizations)/…` | 17 May 2026 (post ingestion/retrieval optimisation pass). | 1 report, 1 results JSON, 1 log folder |
| `.github/workflows/desktop-release.yml` | The only CI workflow: four `build.py prepare` + `build.py dist` (cargo tauri build) jobs on tag `desktop-v*`. **No job runs pytest, pylint or eslint** (`npm run build` inside `prepare` does run `tsc --noEmit`). | — |


## 3. Running the unit tests

### 3.1 Invocation

```bash
cd backend
source .venv/bin/activate            # or use .venv/bin/python -m pytest
pip install pytest pytest-asyncio    # NOT in requirements.txt — see below
python -m pytest tests/unit -q
python -m pytest tests/unit/test_extraction_schemas.py -q      # one module
python -m pytest tests/unit -q -k "chunk"                       # by keyword
```

- Run from `backend/` (or pass `backend/tests/unit` as the path). `backend/tests/__init__.py` and `backend/tests/unit/__init__.py` make `tests` a package, so pytest's default `prepend` import mode inserts `backend/` (the first ancestor without an `__init__.py`) on `sys.path`; `import app...` then resolves without `PYTHONPATH`.
- There is **no** `pytest.ini`, `pyproject.toml`, `setup.cfg` or `tox.ini` in `backend/`, so every pytest option is at its default. In particular `asyncio_mode` is `strict`, which means `async def` tests must carry `@pytest.mark.asyncio` — the four async tests in `test_ingestion_chunked_extraction.py` do.
- No markers, no `-p` plugins, no coverage configuration. `.gitignore` ignores `.pytest_cache/`.

### 3.2 Dependencies the suite needs

| Package | Why | In `requirements.txt`? |
|---|---|---|
| `pytest` | runner | **No** |
| `pytest-asyncio` | `@pytest.mark.asyncio` tests + the `event_loop_policy` fixture in `conftest.py` | **No** |
| `instructor`, `json-repair`, `openai`, `google-genai`, … | imported at module level by `app/services/llm.py` (five test modules import `LLMService`) | yes |
| `qdrant-client` | `app/services/qdrant_service.py` (`test_qdrant_contract.py` also imports `qdrant_client.models` directly) | yes |
| `kuzu` | `test_graph_queries.py` does `patch("kuzu.Database")`, which imports the real `kuzu` module before patching | yes |
| `meilisearch` | `app/services/meilisearch_service.py` | yes |
| `langgraph` and the rest of the ingestion stack | `app/workflows/agents/ingestion_agent.py` (`test_ingestion_chunked_extraction.py`) | yes |
| `llama-cpp-python` | `app/services/local_models.py` (`test_local_runtime_budget.py`, `test_model_load_clock.py`) — only if imported at module top; the tests never load a GGUF | yes (multimodal extras separate) |

In short: the unit tests need the **full** backend environment installed, even though they never open a connection. A venv built from `requirements.txt` plus `pytest pytest-asyncio` is the minimum.

### 3.3 Environment and settings

- `conftest.py`'s autouse `patch_settings` fixture sets `settings.LLM_PROVIDER = "lm_studio"` for every test, so no `.env` is required. Importing `app.core.config` still evaluates `Settings()` once: it reads `backend/.env` **if present** (`env_file` in `model_config`, `extra="ignore"`) and resolves `DATA_DIR`/`KUZU_DB_PATH` defaults — see [Backend core and configuration](06-backend-core-and-configuration.md). If you have a real `.env` in `backend/`, its values leak into the tests except for the keys individual tests monkeypatch.
- `test_kb_llm_config.py` has its own autouse fixture that overrides `LLM_PROVIDER` to `"local"` and pins `LLM_MODEL`, `CHAT_MODEL`, `INGESTION_MODEL`, `INGESTION_LLM_MODEL`, `GEMINI_MODEL`, `OPENAI_MODEL` — it runs after `patch_settings` and wins.
- `normalize_base_url` lives only in `backend/app/services/credentials.py` (the UI sends the raw URL); `test_credentials.py` covers the URL corpus and `test_credentials_keyring.py` the keychain persistence with `keyring` mocked.
- Env vars read by code under test and controlled via `monkeypatch.setenv/delenv`: `ORB_EXTRACTION_CHUNK_TOKENS`, `ORB_LLAMA_MAX_TOKENS` (and its legacy alias `LIVEOS_LLAMA_MAX_TOKENS`). Nothing else touches the OS environment.
- Nothing writes to disk except `tmp_path` fixtures in `test_local_runtime_budget.py`.

### 3.4 Observed state of the suite (2026-09-02)

Recorded factually; this is what happens today, not what should happen.

- `backend/.venv` (Python 3.14.7) is a **partial** install: it has `fastapi 0.141.1`, `pydantic 2.13.4`, `SQLAlchemy 2.0.51`, but no `kuzu`, `qdrant_client`, `instructor`, `langgraph`, `meilisearch`, `json_repair`, and no `pytest`. `python -m pytest` therefore fails immediately with `No module named pytest`.
- After `pip install pytest pytest-asyncio` into that venv, `python -m pytest tests/unit -q` **fails at collection with 7 errors**:
  - `ModuleNotFoundError: No module named 'instructor'` — 5 modules (`test_chat_context.py`, `test_llm_json_cleaning.py`, `test_kb_llm_config.py`, and the two that pull in `ingestion_agent` / `graph`), all via `app/services/llm.py`.
  - `ModuleNotFoundError: No module named 'qdrant_client'` — `test_qdrant_contract.py`.
  - `ModuleNotFoundError: No module named 'app.schemas.relationships'` — `test_relationships.py`. This one is **independent of the venv**: the module was deleted in commit `da75dfc` (2026-05-28, "Add temporal digest features and admin APIs") and the test was never removed.
- With a complete environment, static reading of the current sources shows these additional failures are expected (see §5 for the reasoning per test):
  - `test_graph_queries.py`: 3 of 6 tests — `get_related_nodes()` no longer accepts `min_confidence`, and `find_paths_between_nodes()` no longer exists (both removed in `fbcafe7`, 2026-08-03).
  - `test_chat_context.py::test_returns_latest_when_empty_query`: asserts the whitespace-only query `"   "` is returned verbatim, but `rewrite_follow_up_query` now strips first and returns `""`.
- Everything else (extraction schemas, graph layout, JSON cleaning, Meili/Qdrant contracts, and the five new modules) is consistent with the current code as far as static inspection can tell.

Nothing runs these tests automatically: the only GitHub workflow builds installers (§6.4, §12).

## 4. `conftest.py` — fixtures and what they stub

`backend/tests/unit/conftest.py` is the only conftest (the `tests/integration/` package and its conftest were deleted in `335a253`). Its module docstring still says services are "Kuzu, Qdrant, Typesense, Postgres" — Typesense became Meilisearch and Postgres became SQLite; the fixtures predate both changes.

| Fixture / helper | Scope | What it provides | What it stubs and why | Used by current tests? |
|---|---|---|---|---|
| `event_loop_policy` | session | `asyncio.DefaultEventLoopPolicy()` | The pytest-asyncio hook that decides which loop policy async tests run under. Harmless without the plugin (it is just an unused fixture). | Implicitly by the 4 `@pytest.mark.asyncio` tests in `test_ingestion_chunked_extraction.py` |
| `patch_settings` | function, **autouse** | — | `monkeypatch.setattr(config.settings, "LLM_PROVIDER", "lm_studio", raising=False)`. Prevents any provider branch in `LLMService` from selecting a cloud client and removes the need for a `.env`. `"lm_studio"` is a legacy provider name from the Feb–Mar 2026 LM Studio era; the current default in `config.py` is `"local"`. | Every test (autouse); overridden by `test_kb_llm_config.py` |
| `mock_llm_service` | function | `MagicMock` with `AsyncMock` methods `select_relevant_relationships → []`, `select_relevant_docs_with_reasoning → {"selected": [], "reasoning": ""}`, `generate_node_enrichment_async → {description,title,facts,questions}` | Stands in for `LLMService` in Joint-Approach-era retrieval tests. **None of these three methods exist on `LLMService` today** (removed with the Final Implementation refactor `68494b7` and the desktop cleanup `fbcafe7`). | No |
| `mock_graph_service` | function | `MagicMock` with `get_related_nodes → []`, `find_paths_between_nodes → []`, `resolve_node_id → None`, `execute_query → []` | Stands in for `GraphService`. `find_paths_between_nodes` no longer exists. | No |
| `mock_meili_service` | function | `MagicMock` with `is_available → True`, `index_node`, `update_node_community`, `delete_node` | Stands in for `MeilisearchService`; method names are still accurate. | No |
| `mock_typesense_service` | function | alias returning `mock_meili_service` | Backward-compat alias from the Typesense era ("Kuzu/Typesense migration", `68494b7`, 2026-05-07). | No |
| `mock_qdrant_service` | function | `MagicMock` with `find_node_id_by_name → None`, `upsert_node` (`AsyncMock → True`), `search_node_cores → []` | Stands in for `QdrantService`. The real write method is the **sync** `upsert_node_core`; `upsert_node` does not exist. | No |
| `make_node(name, kind="indexable", node_id=None)` | helper | `{"id", "name", "kind", "description"}` dict | Sample node row shaped like a Kuzu `Node` (`kind` ∈ indexable/note/community). | No |
| `make_relationship(source, target, rel_type="RELATED_TO", confidence=0.9)` | helper | `{"source_name", "target_name", "rel_type", "confidence"}` dict | Sample edge. Note the key is `rel_type` (Kuzu edge property) not `relationship_type` (Pydantic `ExtractedRelationship`). | No |

The pattern the live tests actually rely on is **not** these fixtures but three idioms, documented in §13:

1. `Service.__new__(Service)` to bypass `__init__` (which opens clients), then assign the exact attributes the method under test reads (`client`, `_enabled`, `collection`, `_index`, `_db`, `_conn`, `provider`, `_chat_model_override`, `_chat`).
2. `unittest.mock.patch.object(svc, "method", return_value=…)` to cut off the next layer down (`_reason_step_sync`, `execute_query`, `resolve_node_id`).
3. Module import under `patch("kuzu.Database")`/`patch("kuzu.Connection")` after `sys.modules.pop("app.services.graph")`, so a module-level singleton constructor never touches disk.

## 5. Unit test modules — contract pinned and why it matters

Nine tracked modules plus five untracked (uncommitted as of 2026-09-02) modules. "Status" is against the current sources.

| Module | Target | Tests | Status |
|---|---|---|---|
| `test_chat_context.py` | `LLMService.rewrite_follow_up_query` | 6 | 5 pass, 1 stale assertion |
| `test_extraction_schemas.py` | `app/schemas/extraction.py` validators | ~33 | pass |
| `test_graph_layout.py` | `app/utils/graph_layout.py` | 19 | pass |
| `test_graph_queries.py` | `GraphService.get_related_nodes` / `find_paths_between_nodes` | 6 | 3 fail (API removed) |
| `test_llm_json_cleaning.py` | `LLMService._clean_json` | 15 | pass (needs `json_repair`) |
| `test_meili_contract.py` | `MeilisearchService.index_node` / `update_node_community` | 4 | pass |
| `test_qdrant_contract.py` | `QdrantService.upsert_node_core` / `search_node_cores` | 13 | pass if `_col_cores` resolves (see below) |
| `test_relationships.py` | `app/schemas/relationships.py` | 31 | **ImportError** — module deleted |
| `test_extraction_chunking.py` (untracked) | `app/workflows/extraction_chunking.py` | 14 | pass |
| `test_ingestion_chunked_extraction.py` (untracked) | `ingestion_agent._extract_with_chunking`, `_batch_image_titles` | 4 (async) | pass |
| `test_kb_llm_config.py` (untracked) | `kb_registry.effective_llm_config`, `LLMService.get_chat_model/get_ingestion_model` | 10 | pass |
| `test_local_runtime_budget.py` (untracked) | `LocalLlamaRuntime` budgeting / GGUF resolution | 13 | pass |
| `test_model_load_clock.py` (untracked) | `ModelLoadClock` | 4 | pass |

### 5.1 `test_chat_context.py` — follow-up query rewriting

Contract pinned for `LLMService.rewrite_follow_up_query(history, latest_query, model=None)` (in `app/services/llm.py`):

- Returns `latest_query` untouched when `history` is empty (no LLM call).
- Only the last `settings.CHAT_HISTORY_MAX_MESSAGES` turns are used; turns whose `role` is not `user`/`assistant` or whose `content` is empty are dropped from the prompt (`"ignored"` must not appear; `"Fido overview."` must).
- Turn content longer than 600 characters is truncated to 597 + `"..."` (test uses 800 × `x`).
- The rewrite goes through `self._reason_step_sync(prompt, model=model)`; the tests patch that method so no provider is contacted.
- On any exception from the LLM the original query is returned (never raise into the chat endpoint).
- Accepted rewrites are stripped of surrounding quotes and rejected (falling back to the original) if longer than 300 characters — not tested.

Why it matters: this is the only guard that multi-turn chat (`POST /api/v1/chat` with `conversation_id`, added in `ac7f4e4`, 2026-07-02) degrades to single-turn retrieval instead of failing when the local model is busy or offline. See [Retrieval and chat](16-retrieval-and-chat.md).

Stale assertion: `test_returns_latest_when_empty_query` expects `"   "` back for a whitespace query; the implementation now does `latest = (latest_query or "").strip()` first and returns `""`. Fix the test, not the code.

### 5.2 `test_extraction_schemas.py` — tolerant Pydantic validators

Pins the pre-validators in `app/schemas/extraction.py` that absorb the many shapes local models emit for the extraction JSON (every benchmark report in `Results/` lists a different malformation — LM Studio `json_object` fallbacks, "relationships as plain strings", `question_attribute` as a list):

| Class / validator | Contract |
|---|---|
| `Node.normalize_keys` (`mode="before"`) | `trait` or `title` → `name` when `name` missing (`name` wins); `evidence_quote` or `context` → `isolated_context` (`isolated_context` wins). Non-dict input passes through. |
| `Node.handle_none` (`field_validator("*")`) | `None` → `""` for every field except `type` → `"thing"`. |
| `ExtractedRelationship.normalize_keys` | `entity1`→`source_name`, `entity2`→`target_name`, `description`→`natural_language`. |
| `ExtractedRelationship.handle_none_strings` | `None`/blank `relationship_type` → `"relates_to"`; other string fields `None` → `""`. |
| `ExtractedRelationship.normalize_scores` via `_normalize_score` | Defaults: confidence 7.0, strength 5.0, relevance 5.0. Labels: very high 9, high 8, medium/moderate 6, low 4, very low 2. Floats in `[0,1]` are ×10. Everything clamped to `[1.0, 10.0]` (`0.0 → 1.0`, `15 → 10`). Numeric strings are parsed; unparseable strings → default. |
| `Extraction.normalize_keys` | `None` → empty; unwrap `{"extraction"|"data"|"result": {...}}` when the inner dict has `nodes` or `relationships`; Gemma two-list `[nodes, rels]`; bare list of dicts/strings → nodes (strings become `{"name": s}`); a node's embedded `"relationships"` list is hoisted to the top level. |
| `Extraction.ensure_list` | `nodes`/`relationships` `None` or scalar → `[]`; string items in `nodes` → `{"name": …}`. |

Why it matters: the ingestion pipeline (`app/workflows/agents/ingestion_agent.py`, see [Ingestion pipeline](10-ingestion-pipeline.md)) calls `Extraction.model_validate` on whatever `_clean_json` returns; a validator regression turns into "note failed" for a whole model family. The edge-weight formula comment (`strength×0.5 + confidence×0.3 + relevance×0.2`) is why the 1–10 scale must be preserved — see [Graph storage (Kuzu)](14-graph-storage-kuzu.md).

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
- **Post-filter by `min_confidence`**: `test_depth2_results_filtered_by_confidence` calls `get_related_nodes(..., min_confidence=0.5)`. The parameter was removed in `fbcafe7`; the current signature is `(node_name, max_depth=2, node_id=None)` and the depth>1 branch returns rows unfiltered — **TypeError today**, and the behaviour it pinned no longer exists (check callers in `retrieval.py` before re-adding).
- `find_paths_between_nodes(names, max_depth)`: both tests fail with `AttributeError`; the method was deleted in `fbcafe7`. The patch target `app.services.graph.qdrant_service` still resolves (the module imports the singleton), which is why the failure surfaces only at call time.
- Unknown node (`resolve_node_id → None`) → `[]`. Passes.

Why it matters: Kuzu's Cypher dialect differs from Neo4j's; the Neo4j-era hyphenated-relationship-type errors (32 per run, `Results/Results (Sub Questions Approach)/…/GEMINI_INGESTION_REPORT.md`) and the Kuzu `KU_UNREACHABLE` assertion were both discovered by benchmark runs, and these tests are the only thing that stops the query text from drifting back. See [Graph storage (Kuzu)](14-graph-storage-kuzu.md).

### 5.5 `test_llm_json_cleaning.py` — `_clean_json`

`LLMService._clean_json(json_str) -> str`, in order: (1) if ` ``` ` present, take the first fenced block (` ```json ` or bare) with `re.DOTALL`; (2) strip control characters `\x00-\x08 \x0b \x0c \x0e-\x1f` (newline, CR and tab survive); (3) map `‘ ’ ‛` → `'` and `“ ” „` → `"`; (4) `json_repair.repair_json(...)`, or return the cleaned string unchanged with a warning if `json_repair` is missing. Tests cover fence extraction with surrounding prose, null/bell removal, smart-quote normalisation, missing closing brace, trailing comma, and a combined case. The repair-dependent cases (`{"key": "value"` → valid JSON) fail without `json-repair==0.55.0`.

Why it matters: every structured LLM call (`extract_structured`, ingestion extraction, query analysis) goes through this; it is the first line of defence behind the validators in §5.2.

### 5.6 `test_meili_contract.py` — primary-key invariant

`_make_meili_service()` sets `client`, `collection="test_nodes"`, `_enabled=True`, `is_available`, and replaces `_index()` with a mock whose `add_documents` returns a task with `task_uid=1`. Contracts:

- `update_node_community(node_id, relationship_natural_language="", name="")` → the document sent to `add_documents` always contains `node_id` even when `get_node` returns `None` (document `{"node_id": …}` is synthesised), and contains `name` when supplied. Implementation detail now: it delegates to `update_nodes_community([...])`, which merges into the existing document and calls `add_documents(docs, primary_key="node_id")` once.
- `index_node(node_id, name, node_type, isolated_contexts_text="", relationship_natural_language="", community_level=None)` → document has `node_id` and `name`.
- Index errors are caught and logged with `logger.debug` (`update_nodes_community`) — never raised into ingestion. (`index_node` logs at `warning`; the test only covers the community path.)

Why it matters: Meilisearch's index is created with `primaryKey: "node_id"`; a document without it is rejected, silently dropping the node from keyword search. See [Search indexes](15-search-indexes-qdrant-meilisearch.md).

### 5.7 `test_qdrant_contract.py` — payload and filter contract

`_make_service()` sets only `_enabled=True` and `client=MagicMock()`. Contracts for `upsert_node_core(node_id, name, node_type, description_vector, description="", community_level=None, extra_payload=None)` (tests pass everything by keyword, so the positional order change is harmless):

- Payload always has `node_id`, `name`, `type`; `description` only when truthy; `community_level` only when not `None`; `extra_payload` merged last (can override).
- `_enabled=False` → `client.upsert` not called; `client=None` → returns without raising (both via `is_available()`).
- `search_node_cores(query_vector, limit, min_score, node_type=None, community_level=None)` → `client.query_points(..., query_filter=…)` where the filter is `None` when neither filter is set, else a `Filter(must=[FieldCondition(key="type"|"community_level", match=MatchValue(...))])`; disabled → `[]` and no call.

Caveat: the real methods also read `self._col_cores` (collection name) and call `self._prepare_vector`. If those are instance attributes assigned in `__init__`, `_make_service()` must set them too; verify when you next run the suite.

Why it matters: the `type` and `community_level` payload keys are the filter keys retrieval relies on; renaming either breaks entity-type-filtered search silently.

### 5.8 `test_relationships.py` — dead module

Imports `can_evolve`, `get_contradicting_types`, `get_expected_relationships`, `get_inverse_type`, `is_bidirectional` from `app/schemas/relationships.py` — the bi-temporal relationship ontology added in `033589d` (2026-02-01) and deleted in `da75dfc` (2026-05-28). The whole module errors at collection. Delete the test or restore the module; nothing in `app/` references those helpers.

### 5.9 `test_extraction_chunking.py` (new) — paragraph-bounded chunking and merge

Targets `app/workflows/extraction_chunking.py`:

- `split_for_extraction(text, budget_tokens, count_tokens) -> list[str]`: short text returned as one chunk; whitespace-only → `[]`; splits on `\n\n` paragraph boundaries first, packing whole paragraphs up to the budget; an oversized paragraph falls back to sentence splitting (chunks end with `.`); no chunk is empty; the concatenation of all chunks preserves every word in order.
- `chunk_token_budget(context_tokens, expected_output_tokens) -> int`: `budget × 3.5 ≤ context − output` (input plus ~2.5× output must fit the window); capped at **4000**; floored at `MIN_SPLIT_TOKENS`; env `ORB_EXTRACTION_CHUNK_TOKENS` overrides the cap in either direction.
- `merge_extractions(parts) -> Extraction`: `None` parts skipped; nodes deduplicated case-insensitively by name with `isolated_context` concatenated (duplicates not repeated); a generic `"thing"` type is upgraded by a later specific type; relationships deduplicated by case-insensitive `(source, target, relationship_type)` keeping the highest `confidence`; the first non-empty `title` wins.

### 5.10 `test_ingestion_chunked_extraction.py` (new) — the agent's chunk/retry loop

Loads the module with `importlib.import_module("app.workflows.agents.ingestion_agent")` because the package `__init__` re-exports the compiled LangGraph as the name `ingestion_agent`, shadowing the module. A `_StubLLM` documents the **duck-typed LLM protocol** the agent needs: `provider`, `ingestion_count_tokens(text)`, `ingestion_context_tokens()`, `get_ingestion_model()`, `_clean_json(raw)`, `async ingestion_generate_with_meta(prompt, temperature, max_tokens) -> (content, {"finish_reason", "truncated"})`, `async ingestion_generate(prompt, temperature, max_tokens)`. The stub finds the note as the text after the prompt's final `"nothing else:\n\n"` — a coupling to the extraction prompt's last line.

Contracts for `await agent._extract_with_chunking(llm, note_text, images) -> (Extraction, chunk_count)`:

- A short note is a single LLM call, `chunk_count == 1`.
- A 1000-word, 20-paragraph note with `ORB_EXTRACTION_CHUNK_TOKENS=400` produces ≥3 chunks, one call per chunk, and the merged extraction has exactly 1000 nodes (nothing lost, nothing duplicated) with the first chunk's title.
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
- `lm._default_chat_max_tokens()`: env `ORB_LLAMA_MAX_TOKENS` (legacy `LIVEOS_LLAMA_MAX_TOKENS`) — unset → `None` (dynamic budget), integer → that cap, garbage → `None`.
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
| CI test/lint job | none — `desktop-release.yml` only runs `build.py prepare` (which includes `npm run build` → `tsc --noEmit`) and `build.py dist` |



## 7. Testing gaps

| Gap | Detail | Risk |
|---|---|---|
| No CI test job | `.github/workflows/desktop-release.yml` is the only workflow and runs only on `desktop-v*` tags / manual dispatch; it never invokes pytest, pylint, eslint or `tsc`. | Regressions (like the three stale test modules) accumulate silently. |
| Unit suite not runnable from the checked-in environment | `pytest`/`pytest-asyncio` absent from `requirements.txt`; the working `.venv` is partial; three modules assert removed APIs (§3.4). | New contributors cannot get a green run without archaeology. |
| No frontend tests | No jest/vitest/RTL/playwright in `frontend/package.json`; only `eslint` and the `tsc --noEmit` in `npm run build`. | Editor (CodeMirror wikilink autocomplete), chat polling and graph canvas logic are unverified. |
| No desktop-shell / runtime tests | `desktop/src-tauri` has no tests and `backend/app/desktop_runtime.py` (ports, sidecar boot, Firefly bootstrap) is exercised only by hand. | |
| No integration tests | `tests/integration/` was deleted in `335a253`; nothing starts Qdrant/Meili/Kuzu/SQLite against a temp `DATA_DIR`. The API contract (`?kb=`, vault sync, ingestion status transitions) is covered only by the benchmark scripts, which need a full running app and an LLM. | Cross-service invariants (vault file ↔ SQLite row ↔ Kuzu ↔ Qdrant ↔ Meili) are untested. |
| conftest fixtures are dead | Six fixtures/helpers stub methods that no longer exist and are used by no test. | Misleading for anyone writing new tests. |
| No coverage, no type checking, no formatter | See §6.4. | |

## 8. How to add tests for a new service

Follow the idioms the passing modules use; do not reach for the dead `mock_*` fixtures.

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
5. **Settings and env**: use `monkeypatch.setattr(config.settings, "KEY", value, raising=False)` and `monkeypatch.setenv/delenv`; never write `.env`. Remember `patch_settings` already pins `LLM_PROVIDER="lm_studio"`; override it in a module-level autouse fixture if your code branches on `"local"`.
6. **Filesystem**: `tmp_path`, and patch resolver functions (`resolve_models_dir`, `_gguf_looks_complete`) instead of touching `DATA_DIR`.
7. **Determinism**: no wall-clock, no randomness (see `graph_layout` tests), no sleeps (patch `asyncio.sleep` and assert it was not called, as the chunking test does).
8. **Run it**: `cd backend && python -m pytest tests/unit/test_<area>.py -q`. Add `pytest` and `pytest-asyncio` to a dev requirements file when you get the chance — they are missing today.
9. **When you remove or rename a production function**, grep `backend/tests/unit` for it in the same commit (the three stale modules exist because this step was skipped in `da75dfc` and `fbcafe7`).
10. **Response-shape changes**: if you change the chat response shape (`answer`, `context[].text/linked_notes/note_id/title`) or the notes status fields (`processed`, `failed`), update the harness on the `orb-testing` branch to match.

## 9. Invariants, gotchas and non-obvious behaviours

- **`Service.__new__` is the test contract.** Any attribute a method reads that is normally assigned in `__init__` (`client`, `_enabled`, `_col_cores`, `collection`, `_conn`, `_chat`, `_chat_model_override`) is part of the implicit interface the tests set up by hand; renaming one breaks tests without touching behaviour.
- **`patch_settings` sets a provider that no longer exists** (`lm_studio`). Code that validates `LLM_PROVIDER` against the current enum would make every test fail; keep the fixture in mind when adding validation.
- **`patch("kuzu.Database")` imports kuzu.** The graph tests are not free of the native dependency, only of a database file.
- **Kuzu query text is pinned**: never reintroduce `WHERE all(...)` over `relationships(path)` or `*1..N` in the depth-1 path — `KU_UNREACHABLE` is a hard crash, not an exception.
- **Extraction validators are order-sensitive**: `Extraction.normalize_keys` (before) → `ensure_list` (before, per field) → `Node`/`ExtractedRelationship` validators. Wrapper unwrapping only happens when the inner dict has `nodes` or `relationships`.
- **Scores are 1–10, never 0–1** after validation; anything in `[0,1]` is scaled ×10. A model returning `0.0` confidence therefore stores `1.0`.
- **`_clean_json` picks the *first* fenced block** — a response containing two fences returns only the first.
- **Meilisearch documents must carry `node_id`** (index primary key); `update_nodes_community` synthesises `{"node_id": …}` if `get_node` returns nothing.
- **Retrieval F1 is F1 of means**, not mean of F1s.
- **Results JSON lacks per-case F1/contains** — do not try to recompute report tables from it alone.
- **`*.log` is gitignored except under `Results*/`** — a new results folder must be named `Results…` at the repo root (or the `Results/` subfolders) for its logs to be tracked.
- **Log analysis scripts used for the reports (`analyze_test_logs.py`, `batch_ingest.py`, `backfill_failed_relationships.py`, `run_community_detection.py`) are not in the repo** (some were pruned in `3ab4f9d`, 2026-03-04, and `fbcafe7`); the README still references `scripts/run_community_detection.py`.

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
| 2026-08 → 09 (uncommitted) | — | Five new unit modules for chunked extraction, per-KB LLM config, llama.cpp budgeting and the model-load clock. |

Why the archive is kept in git despite its size: the reports are the only record of *why* the retrieval loop, reranker thresholds, Kuzu migration and model choices look the way they do; the raw logs let the numbers be re-derived. Why unit tests avoid infrastructure entirely: the project has no CI runner with Qdrant/Meilisearch, and the desktop packaging bundles its own binaries, so "fast, deterministic, import-only" was the only feasible contract (conftest docstring).
