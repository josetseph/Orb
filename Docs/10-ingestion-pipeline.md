# Ingestion Pipeline

**What this covers.** The end-to-end path a note takes from "saved in a vault" to "queryable knowledge": how ingestion is triggered and queued, the LangGraph agent that orchestrates it (`multimodal → extraction → storage → summarization`), the LLM extraction prompt and JSON normalisation, entity resolution against the existing graph, relationship scoring, and exactly what gets written to Kuzu, Qdrant, Meilisearch and SQLite at each step. It also covers re-ingest cleanup, the `ingestion_tracker` idle timer that drives community (Leiden-style) recomputation and temporal digests, every config key the pipeline reads, and the failure semantics of each stage. Multimedia attachment handling (Florence/Whisper/Marlin) is only summarised here; see the sibling doc for the details.

**Related docs:** [Multimedia enrichment](11-multimedia-enrichment.md) · [Notes, wikilinks & vault files](09-notes-wikilinks-and-vault-files.md) · [Knowledge bases & vaults](08-knowledge-bases-and-vaults.md) · [Graph storage (Kuzu)](14-graph-storage-kuzu.md) · [Search indexes (Qdrant/Meilisearch)](15-search-indexes-qdrant-meilisearch.md) · [LLM providers & prompting](13-llm-providers-and-prompting.md) · [Local models & inference](12-local-models-and-inference.md) · [Retrieval & chat](16-retrieval-and-chat.md) · [API reference](07-api-reference.md) · [Configuration reference](21-configuration-reference.md) · [Decisions & constraints](26-decisions-and-constraints.md)

---

## 1. Responsibilities & boundaries

**Owns**

- The `IngestionWorkflow` class (`backend/app/workflows/ingestion.py`) — one instance per Knowledge Base, created lazily by `KBContext.get_ingestion_workflow()` in `backend/app/services/kb_registry.py` with that KB's `GraphService`, `QdrantService` and `MeilisearchService`.
- The LangGraph agent (`backend/app/workflows/agents/ingestion_agent.py`): state schema, four nodes, routing, the extraction prompt, chunked extraction of long notes (`backend/app/workflows/extraction_chunking.py`), JSON clean-up, batched image titling and garbage-name recovery.
- Entity/relationship persistence (`_write_ontology`), context accumulation and indexing (`_update_neighborhoods` / `_update_node_summary`), note status bookkeeping in SQLite, and the enriched-body write-back to the vault `.md`.
- Post-ingestion maintenance: community recomputation (`rebuild_leiden_communities`), temporal digests (`build_temporal_digests`), `get_maintenance_status()`, and the process-wide `IngestionTrackerService` (`backend/app/services/ingestion_tracker.py`) that debounces them.
- The `Extraction` / `Node` / `ExtractedRelationship` / `NoteInput` pydantic schemas (`backend/app/schemas/extraction.py`).

**Does not own**

- HTTP routing (`backend/app/api/notes.py`, `api/admin.py`, `api_desktop.py`) — documented in [07-api-reference.md](07-api-reference.md); this doc only describes what those routes hand to the pipeline.
- Note file semantics, wikilinks, `note_links`, the vault watcher ([09](09-notes-wikilinks-and-vault-files.md)).
- Multimedia extraction internals: `services/multimedia.py`, `services/multimodal_runtime.py` ([11](11-multimedia-enrichment.md)).
- Kuzu schema/queries beyond the writes listed here ([14](14-graph-storage-kuzu.md)), Qdrant/Meili client mechanics ([15](15-search-indexes-qdrant-meilisearch.md)), LLM provider selection and `llm_service` internals ([13](13-llm-providers-and-prompting.md)), GGUF residency ([12](12-local-models-and-inference.md)).
- Retrieval: nothing in this doc reads the graph for answering ([16](16-retrieval-and-chat.md)).

## 2. Files

| Path | Purpose | Key exports |
|---|---|---|
| `backend/app/workflows/ingestion.py` | Orchestrator: `process_note`, SQLite status writes, `_write_ontology`, `_update_neighborhoods`, `_update_node_summary`, community + digest builders, maintenance status | `IngestionWorkflow`, `ingestion_workflow` (default-KB singleton), `clean_rel_type`, `EntityLockManager` |
| `backend/app/workflows/agents/ingestion_agent.py` | LangGraph `StateGraph`: `multimodal_node`, `extraction_node`, `storage_node`, `summarization_node`; extraction prompt; enrichment-block stripping; global multimedia semaphore | `ingestion_agent` (compiled graph), `IngestionState`, `multimedia_concurrency_limit`, `_strip_prior_multimedia_enrichment`, `_ENRICHMENT_BLOCK_RE`, `should_route_after_extraction` |
| `backend/app/workflows/extraction_chunking.py` | Pure, I/O-free helpers for long-note extraction: token budget per chunk, paragraph-bounded splitting, merging of per-chunk `Extraction`s | `chunk_token_budget`, `split_for_extraction`, `merge_extractions`, `MIN_SPLIT_TOKENS` (=400) |
| `backend/app/services/ingestion_tracker.py` | Process-global counter of active ingestions; idle-timer debounce for community recompute; cooperative cancellation events | `IngestionTrackerService`, `ingestion_tracker`, `COMMUNITY_IDLE_SECONDS` (=120) |
| `backend/app/schemas/extraction.py` | Pydantic models + tolerant validators for LLM JSON; `NoteInput` wire model | `Extraction`, `Node`, `ExtractedRelationship`, `NoteInput`, `_normalize_score`, `_SCORE_LABELS` |
| `backend/app/services/multimedia.py` | Attachment → text (PDF/image/audio/video/docx/xlsx/csv); vault path resolution; temp-file rules | `multimedia_service` (see [11](11-multimedia-enrichment.md)) |
| `backend/app/services/graph.py` | Kuzu writes used by ingestion: `execute_query`, `create_or_update_relationship`, `find_nodes_by_exact_names`, `find_nodes_by_name`, community/digest node builders | `GraphService`, `graph_service`, `_SCHEMA_STMTS` |
| `backend/app/services/qdrant_service.py` | Three per-KB collections; batch upserts; name→id resolution; context append; scroll helpers | `QdrantService`, `qdrant_service` |
| `backend/app/services/meilisearch_service.py` | One per-KB index (`primaryKey: node_id`); `index_node`, `get_node`, `update_nodes_community`, `delete_node` | `MeilisearchService`, `meilisearch_service` |
| `backend/app/services/embedding.py` | `embed_documents` (no instruction prefix) / `embed_query` (Qwen3 instruction prefix) over the in-process GGUF | `embedding_service` |
| `backend/app/services/llm.py` | `ingestion_generate`, `_clean_json`, `generate_title`, `reason`, `generate_text`, `get_ingestion_model`, `_init_ingestion_clients` | `llm_service` |
| `backend/app/api/notes.py` | `POST /api/v1/notes/{id}/ingest`, `POST /api/v1/ingest`, `GET /api/v1/notes/{id}/status`, note delete (graph orphan cleanup) | `router`, `_delete_note_impl` |
| `backend/app/api/admin.py` | `reingest-all`, `reset-ingestion-data`, `rebuild-communities`, `build-temporal-digests`, `maintenance-status` | `router` |
| `backend/app/api_desktop.py` | `POST /api/v1/notes/reingest-vault` | `router` |
| `backend/app/models/note.py` | SQLite `notes` columns the pipeline mutates: `processed`, `failed`, `processing_stage`, `processing_model`, `title` | `Note` |
| `backend/tests/unit/test_extraction_schemas.py` | Unit tests for the tolerant extraction schema | — |
| `backend/tests/unit/test_extraction_chunking.py` | Tests for splitting/budget/merge helpers (word-count tokenizer stub) | — |
| `backend/tests/unit/test_ingestion_chunked_extraction.py` | Tests `_extract_with_chunking` / `_extract_chunk` / `_batch_image_titles` against a stub LLM that truncates large chunks | — |
| `backend/tests/benchmark/prepare_dataset.py` | Batch client: create → `/ingest` → poll `/status` | `_create_and_ingest`, `_wait_for_completion` |

## 3. Architecture / flow

### 3.1 Sequence (single note)

```mermaid
sequenceDiagram
    autonumber
    participant API as api/notes.py (BackgroundTasks)
    participant WF as IngestionWorkflow.process_note (per KB)
    participant TR as ingestion_tracker (global)
    participant AG as LangGraph ingestion_agent
    participant MM as multimedia_service / multimodal_runtime
    participant LLM as llm_service.ingestion_generate
    participant EMB as embedding_service
    participant KZ as Kuzu (GraphService)
    participant QD as Qdrant (3 collections)
    participant ME as Meilisearch index
    participant DB as SQLite notes row
    participant VLT as vault .md

    API->>DB: processed=false, failed=false, stage="Queued for ingestion"
    API->>WF: process_note(NoteInput, note_id)
    WF->>TR: begin_ingestion() (count+1, cancel timers, set cancel events)
    WF->>DB: stage="Queued for ingestion"
    WF->>WF: acquire _process_semaphore (INGESTION_PIPELINE_CONCURRENCY)
    WF->>DB: stage="Starting ingestion"
    WF->>AG: ainvoke(state{input, note_id, workflow=self})
    AG->>AG: multimodal_node: strip prior enrichment blocks, parse attachment links
    AG->>MM: PDFs/images (Florence) → docx/xlsx → audio/video (Whisper) → video (Marlin)
    MM-->>AG: text sections appended to content
    AG->>VLT: _persist_note_body(enriched content) if changed
    AG->>DB: stage="Extracting knowledge graph", model=<ingestion model>
    AG->>LLM: extraction prompt per chunk (temperature 0.1, ≤3 attempts, split in half on truncation)
    LLM-->>AG: JSON → _clean_json → Extraction.model_validate_json
    AG->>LLM: optional batch rename of garbage-named nodes
    AG->>DB: stage="Writing graph and note metadata"
    AG->>WF: _write_ontology (in thread)
    WF->>EMB: embed_documents(node stub texts)
    WF->>QD: find_node_ids_by_names (cores) ; KZ fallback find_nodes_by_exact_names
    WF->>KZ: MERGE note Node(kind='note') ; UNWIND MERGE entity Nodes ; MERGE REFERENCES
    WF->>QD: upsert_node_cores(stubs for NEW nodes)  (abort on failure)
    WF->>KZ: create_or_update_relationship × N (SEMANTIC_REL)
    WF->>EMB: embed_documents(relationship NL)
    WF->>QD: upsert_node_relationships(batch)
    WF-->>AG: title
    AG->>DB: title=<resolved title>
    AG->>DB: stage="Indexing entity contexts", model="Embeddings"
    AG->>WF: _update_neighborhoods (concurrency 4, per-entity asyncio locks)
    loop per unique entity
        WF->>QD: find_node_id_by_name / get_node_content_by_id
        WF->>EMB: embed new contexts + merged passage (one batch)
        WF->>KZ: MERGE Node(kind='indexable') SET name,type
        WF->>QD: append_node_item(contexts) ; upsert_node_core(merged vector)
        WF->>ME: index_node(name,type,isolated_contexts,relationship_nl)
    end
    AG-->>WF: final_state
    WF->>DB: processed=true, failed=false, stage="Ingestion complete"
    WF->>KZ: neighbours of note → tracker.queue_nodes_for_community_recompute
    WF->>TR: end_ingestion(rebuild_leiden_communities) → idle timer (120 s)
    WF->>WF: log [Timing] line (model_load vs inference); models stay resident
```

### 3.2 Flowchart (agent + failure routing)

```mermaid
flowchart TD
    A[process_note] --> B[tracker.begin_ingestion]
    B --> C{semaphore slot}
    C --> D[multimodal_node]
    D -->|media_errors| X1[RuntimeError raised out of ainvoke]
    D --> E[extraction_node]
    E -->|errors set| END1((END))
    E -->|extraction ok| F[storage_node]
    F -->|Storage failed → errors| G[summarization_node returns {} ]
    F -->|ok| H[summarization_node]
    H -->|Context indexing failed → errors| END2((END))
    H -->|ok| END3((END))
    END1 --> R{final_state.errors?}
    G --> R
    END2 --> R
    END3 --> R
    X1 --> FAIL
    R -->|yes| FAIL[stage=Ingestion failed, failed=true]
    R -->|no| OK[_mark_note_processed → _queue_leiden_recompute_if_due]
    OK --> FIN[finally: tracker.end_ingestion; maybe unload models]
    FAIL --> FIN
```

Routing facts (from `ingestion_agent.py`): entry point `multimodal`; unconditional edge `multimodal → extraction`; conditional edge after `extraction` via `should_route_after_extraction` (`"end"` if `state["errors"]` else `"store"`); unconditional `storage → summarization → END`. `storage_node` and `summarization_node` each short-circuit when `state["errors"]` is already set or `extraction` is missing, so a storage failure passes through summarization untouched and surfaces as `final_state["errors"]`.

## 4. Entry points and enqueueing

### 4.1 Who calls `process_note`

Every trigger builds a `NoteInput(content, created_at, title, skip_ingestion)` and schedules `kb.get_ingestion_workflow().process_note(note_input, note_id)` through Starlette `BackgroundTasks`. Nothing else in the codebase calls `process_note`.

| Trigger | File / route | Which notes | Flags reset before queueing | `NoteInput` built from | Gate |
|---|---|---|---|---|---|
| Explicit (re)ingest of one note | `api/notes.py` → `POST /api/v1/notes/{note_id}/ingest` | one, must belong to `?kb` | `processed=False, failed=False, processing_stage="Queued for ingestion", processing_model=None` | `content=note_body(note, kb)` (vault file), `created_at=note.created_at.isoformat()`, `title=note.title or None` | `require_ai()` |
| Legacy create+ingest | `api/notes.py` → `POST /api/v1/ingest` | new note row + vault file created first (`persist_note_body`, `refresh_note_links`) | row created with `processing_stage="Queued for ingestion"` (or `"Saved"` when `skip_ingestion`) | the request body itself; `created_at` filled with the parsed/created timestamp if absent; **`title` is whatever the caller sent (may be None → LLM title)** | `require_ai()` unless `skip_ingestion=True` |
| Re-ingest all unprocessed/failed | `api/admin.py` → `POST /api/v1/admin/reingest-all` | `processed == False OR failed == True` in KB | none (flags left as-is; `process_note` will overwrite stage) | vault body, `created_at`, `title=note.title` | `require_ai()` |
| Re-ingest whole vault | `api_desktop.py` → `POST /api/v1/notes/reingest-vault` | every note in KB | `processed=False, failed=False, processing_stage="Queued for vault re-ingest", processing_model=None` (committed before queueing); bodies read in one `asyncio.to_thread` batch | vault body, `created_at`, `title=n.title` | `require_ai()` |
| Benchmark client | `backend/tests/benchmark/prepare_dataset.py` | HotPotQA/MuSiQue note files | via HTTP: `POST /api/v1/notes` then `POST /api/v1/notes/{id}/ingest`, then polls `GET /api/v1/notes/{id}/status` until `processed` or `failed` (no timeout) | — | — |

Not triggers (common misconception): `POST /api/v1/notes` (create) and `PUT /api/v1/notes/{id}` (autosave) never ingest. The vault watcher/sync (`services/vault_watcher.py`, `services/vault_sync.py`) only writes advisory stages (`"Saved"`, `"Changed on disk — re-ingest when ready"`, `"External delete detected — review in Orb"`) and never queues work. `POST /api/v1/admin/reset-ingestion-data` wipes stores and clears flags but does not queue anything.

`NoteInput.skip_ingestion` is only consulted by `POST /api/v1/ingest`; the pipeline itself ignores it.

### 4.2 Execution model (asyncio vs threads)

- `BackgroundTasks` run **after the HTTP response**, on the request's event loop, sequentially: Starlette's `BackgroundTasks.__call__` is literally `for task in self.tasks: await task()`. Consequences:
  - Within one request (e.g. `reingest-all` with 990 notes) the notes are processed strictly one after another regardless of `INGESTION_PIPELINE_CONCURRENCY`, because task *k+1* is not even started until `process_note` for task *k* returns.
  - `process_note` **re-raises** on failure (after marking the note failed). Because Starlette awaits tasks in a plain loop with no try/except, an exception from note *k* propagates out of the ASGI app (uvicorn logs "Exception in ASGI application") and **the remaining tasks queued by that same request are never run**. Notes after the failing one stay at `"Queued for ingestion"` / `"Queued for vault re-ingest"` with `processed=False`. A second `reingest-all` call picks them up (they are unprocessed). This is a live behaviour of the code as written; see Open questions.
  - Concurrency across *different* requests (two users, or the UI firing several `/ingest` calls) is bounded by the per-KB `asyncio.Semaphore(INGESTION_PIPELINE_CONCURRENCY)`; waiters are FIFO in `asyncio.Semaphore` order.
- Inside `process_note` everything blocking is pushed off the loop: `_write_ontology` runs via `asyncio.to_thread` (one thread for the whole ontology write); `_update_node_summary` wraps each Qdrant/Kuzu/Meili call in `asyncio.to_thread`; multimedia handlers use `asyncio.to_thread` too. LLM calls in `ingestion_generate` are `asyncio.to_thread` around synchronous clients. Kuzu access is serialised by `GraphService._lock` (an `RLock`), so parallel threads never execute Cypher concurrently on one KB.
- Community recompute runs in a worker thread (`asyncio.to_thread(callback)` from the tracker's debounce task, or a `BackgroundTasks` thread from the admin endpoint); temporal digests run on a `threading.Timer` thread. Both use `threading.Event`s for cancellation because they are not on the loop.

### 4.3 Semaphores, locks and their scope

| Primitive | Where | Scope | Default | Effect |
|---|---|---|---|---|
| `IngestionWorkflow._process_semaphore = asyncio.Semaphore(settings.INGESTION_PIPELINE_CONCURRENCY)` | `ingestion.py` `__init__` | **per KB** (one workflow per `KBContext`) | 1 | Max notes of one KB past "Queued" simultaneously. Different KBs do not share it (they still serialise on model locks and `GraphService._lock` of their own graph). |
| `multimedia_concurrency_limit = asyncio.Semaphore(settings.MULTIMEDIA_CONCURRENCY)` | module-level in `ingestion_agent.py` | **process-global** | 1 | Only one note at a time is inside `multimodal_node`'s body across all KBs, so Florence/Whisper/Marlin are never loaded twice. Note the semaphore is held for the whole multimodal phase even when the note has no attachments (cheap). |
| `asyncio.Semaphore(4)` in `_update_neighborhoods` | per call | per note | 4 | Up to 4 entity summaries of the same note update concurrently. |
| `EntityLockManager` (`defaultdict(asyncio.Lock)` keyed `(label, name.lower().strip())`) | per workflow | per KB | — | Two notes touching the same entity name serialise their `_update_node_summary`. Locks are never evicted (dict grows with distinct entity names). |
| `GraphService._lock` (`threading.RLock`) | per graph | per KB | — | All Kuzu statements serialised. |
| `IngestionTrackerService._lock` (`asyncio.Lock`) | global singleton | process | — | Guards counters and the debounce task. |
| `_community_run_state_lock` (`threading.Lock`) + `_community_run_seq/_active_seq/_running` | per workflow | per KB | — | Single-flight for `rebuild_leiden_communities`; newest request wins, older waits/cancels. |
| `_temporal_digest_timer_lock` + `threading.Timer` | per workflow | per KB | — | Debounce for `build_temporal_digests`. |
| `settings.INGESTION_AGENT_CONCURRENCY` | `core/config.py` | — | 2 | **Declared but unused** anywhere in `backend/app` (legacy from the HTTP-sidecar era). |

Because the tracker is a **process-global singleton** while workflows are per KB, `begin_ingestion`/`end_ingestion` counts notes from all KBs together; the callback passed to `end_ingestion` is the `rebuild_leiden_communities` bound method of whichever KB's note finished last (see Gotchas).

### 4.4 `process_note` contract

```python
async def process_note(self, note_input: NoteInput, note_id: str = None) -> dict
```

- `note_id` defaults to a fresh `uuid4` when omitted (only meaningful for the row that already exists; all HTTP callers pass the SQLite id, which is also the Kuzu note-node id).
- Order of operations: `tracker.begin_ingestion()` → stage `"Queued for ingestion"` → **acquire semaphore** → stage `"Starting ingestion"` → `ingestion_agent.ainvoke(initial_state)` → if `final_state["errors"]` non-empty raise `RuntimeError("Ingestion Agent Failed: …")` → `_mark_note_processed` → `_queue_leiden_recompute_if_due` → return `{"note_id", "extraction": Extraction.model_dump(), "status": "success", "processed_content": final_state["content"]}`.
- `except Exception`: stage `"Ingestion failed"`, `_mark_note_failed` (sets `failed=True`, `processed=False`, stage `"Ingestion failed"`), re-raise.
- `finally`: `await tracker.end_ingestion(self.rebuild_leiden_communities)`. Models are **not** unloaded here any more: the GGUF idle watcher (`ORB_MODEL_IDLE_SECONDS`, default 5 min, see [12](12-local-models-and-inference.md)) evicts them, and loading any other family evicts them anyway. (History: originally unloaded per note; commit `f8f527f` moved that to "when the batch drains"; the current working tree removes it entirely because even a single-note ingest re-read multi-GB GGUFs.)
- Timing: `process_note` snapshots `model_load_clock` before `ainvoke` and afterwards logs one line `[Timing] ingest note_id=… total=…s model_load=…s inference=…s loads=<per-family load seconds> | extraction=… chunks=N` using `final_state["timings"]` (`_load_snapshot` / `_log_timing`). This is the only place stage timings surface; they are not persisted.
- LLM access: every LLM call in the pipeline goes through `self._llm` (constructor arg `llm`, defaulting to the global `llm_service`). `KBContext._ensure_lazy()` passes the KB's pinned `LLMService` (per-KB `llm_provider` / `llm_model` / `llm_ingestion_model` overrides, see [08](08-knowledge-bases-and-vaults.md)) so a KB can extract with a different provider/model than the system default. `require_ai(kb)` in the API gates on that KB's provider.
- `initial_state = {"input", "content": "", "extraction": None, "note_id", "created_at": None, "errors": [], "workflow": self}`. `status` and `logs` are not initialised here; `multimodal_node` uses `state.get("logs", [])` for that reason, later nodes use `state["logs"]` (safe because multimodal always runs first).

## 5. The LangGraph agent

### 5.1 State

```python
class IngestionState(TypedDict):
    input: NoteInput            # what the caller passed
    content: str                # note body after multimedia enrichment (stripped)
    extraction: Optional[Extraction]
    note_id: Optional[str]
    created_at: Optional[str]   # set by storage_node: input.created_at or now().isoformat()
    errors: List[str]           # any non-empty value == failure
    status: str                 # "MULTIMEDIA_DONE" | "EXTRACTED" | "INDEXED" | "FAILED"
    logs: List[str]             # human-readable timeline, not persisted anywhere
    workflow: Optional[Any]     # REQUIRED: KB-scoped IngestionWorkflow
    timings: dict               # per-stage seconds; extraction_node adds extraction + extraction_chunks
```

`_require_workflow(state)` raises `RuntimeError` when `workflow` is missing — there is deliberately no fallback to the default-KB singleton (a note must never be written into the wrong KB's stores).

### 5.2 Nodes

| Node | Function | Reads | Writes to state | Side effects | Failure signalling |
|---|---|---|---|---|---|
| `multimodal` | `multimodal_node` | `input.content`, `note_id` | `content` (stripped+enriched, `.strip()`ed), `logs`, `status="MULTIMEDIA_DONE"` | vault `.md` rewritten when enrichment changed; model loads/unloads; stage updates | **raises** `RuntimeError("Multimedia processing failed for: …")` if any attachment handler failed (all attachments are still attempted first) |
| `extraction` | `extraction_node` | `content`, `input.title` | `extraction`, `logs`, `status="EXTRACTED"`, `timings.extraction`, `timings.extraction_chunks` | LLM calls (extraction ×≤3 per chunk, halves re-extracted on truncation, rename ×≤1); stage update | returns `{"errors": [...]}` → routed to END |
| `storage` | `storage_node` | `content`, `extraction`, `input.created_at`, `input.title` | `note_id`, `created_at` | `_write_ontology` (Kuzu/Qdrant), `_update_note_title` (SQLite) | returns `{"errors": ["Storage failed: …"]}`; partial writes are **not** rolled back |
| `summarization` | `summarization_node` | `extraction.nodes`, `content`, `created_at` | `logs`, `status="INDEXED"` (or `"FAILED"`) | `_update_neighborhoods` (Kuzu/Qdrant/Meili) | returns `{"errors": ["Context indexing failed: …"], "status": "FAILED"}` |

`extraction_node` and `storage_node` set `created_at` semantics differently: the **extraction prompt does not include the note date** any more (commit `682755f` added a `[Note date: …]` prefix; the current code no longer has it), but `created_at` is still threaded to `append_node_item(note_created_at=…)` so each isolated-context vector carries the note date for temporal digests and for the `"{content} - {date}"` rendering in `get_node_content_by_id`.

## 6. Stage-by-stage walkthrough

Status strings below are the exact values written to `notes.processing_stage` / `notes.processing_model` through `IngestionWorkflow._update_note_processing_status(note_id, stage, model)` → `_update_note_fields` (a single `UPDATE notes SET … WHERE id=?` in its own `AsyncSessionLocal` session). `GET /api/v1/notes/{id}/status` returns them verbatim.

### 6.1 Queueing and start (`process_note`)

| Moment | `processing_stage` | `processing_model` |
|---|---|---|
| API handler, before response | `"Queued for ingestion"` (or `"Queued for vault re-ingest"`) | `None` |
| `process_note` before semaphore | `"Queued for ingestion"` | `None` |
| after semaphore acquired | `"Starting ingestion"` | `None` |

The tracker is registered **before** waiting for the semaphore so the 120 s community idle timer can never fire while notes are still queued.

### 6.2 `multimodal_node` (summary — full detail in [11-multimedia-enrichment.md](11-multimedia-enrichment.md))

1. Stage `"Preparing multimedia attachments"` / `None`, then acquire the global `multimedia_concurrency_limit`.
2. `content = _strip_prior_multimedia_enrichment(input.content)`: finds the **first** occurrence of `_ENRICHMENT_BLOCK_RE` and truncates the body there (everything after the first `\n\n[PDF Extraction (…)]`, `\n\n[Image:…]`, `\n\n[Audio Transcript (…)]`, `\n\n[Video Audio Transcript (…)]`, `\n\n[Video Visual Analysis (…)]`, `\n\n[Word Extraction (…)]`, `\n\n[Spreadsheet Extraction (…)]` is dropped, `rstrip()`ed). This is the *only* "re-ingest cleanup" of note content; user text after an enrichment block is lost on re-ingest (see Gotchas).
3. Attachment discovery: `[📎 name](url)` / `[🎤 name](url)` via `_ATTACH_RE`, and `![alt](url)` via `_IMAGE_MD_RE`, where `url` is `https?://…` or `/vault-files/…` up to the closing `)` (spaces allowed). De-duplicated case-insensitively on the unquoted URL without query string.
4. Classification by lower-cased URL suffix into `pdfs`, `images` (`.jpg .jpeg .png .webp .gif`), `docx_files`, `spreadsheets` (`.xlsx .xls .csv .tsv`), `audio_files` (`.m4a .mp3 .wav .ogg .aac` **or** emoji `🎤`, and not a video suffix), `videos` (`.mp4 .mov .webm .mkv .avi`). Anything else is logged `Skipped (Unsupported Type)`.
5. Phases, each via `_run_phase(stage, model, items, handler)` which sets the stage once and then processes items sequentially, collecting exceptions into `media_errors` instead of aborting:

   | Phase | Stage string | Model string | Handler → appended block |
   |---|---|---|---|
   | 1a | `"Reading PDF pages and images"` | `"Florence-2"` | `_handle_pdf` → `\n\n[PDF Extraction (<filename>)]: <text>` (PDF progress callbacks additionally write `"PDF: page i/N, extracting text"`, `"PDF: page i/N, describing image j/M"`/`"Florence-2"`, `"PDF: page i/N, describing page render"`/`"Florence-2"`, `"PDF: page i/N complete"`) |
   | 1b | `"Describing images"` | `"Florence-2"` | `_handle_image` → `\n\n[Image: {{ORB_IMAGE_TITLE_n}}]\nThe image titled "{{ORB_IMAGE_TITLE_n}}" shows the following: <caption>` (placeholder token, replaced in phase 5) |
   | 1c (if any PDFs/images) | `"Unloading image model"` | `"Florence-2"` | `multimedia_service.unload_local_models("florence")` |
   | 2a | `"Extracting documents"` | `None` | `_handle_docx` → `\n\n[Word Extraction (<filename>)]: <text>` |
   | 2b | `"Extracting spreadsheets"` | `None` | `_handle_spreadsheet` → `\n\n[Spreadsheet Extraction (<filename>)]: <text>` |
   | 3a | `"Transcribing audio"` | `"Whisper"` | `_handle_audio` → `\n\n[Audio Transcript (<filename>)]: <text>` |
   | 3b | `"Transcribing video audio"` | `"Whisper"` | `_handle_video_audio` → `\n\n[Video Audio Transcript (<filename>)]:\n\n<text>` (empty → nothing appended) |
   | 3c (if any audio/videos) | `"Unloading speech model"` | `"Whisper"` | `unload_local_models("whisper")` |
   | 4a | `"Analyzing video visuals"` | `"Marlin"` | `_handle_video_visual` → `\n\n[Video Visual Analysis (<filename>)]:\n\n### Visual Analysis\n**Scene:** …\n\n**Events:**\n- 0:00–0:05 — …` |
   | 4b (if any videos) | `"Unloading video model"` | `"Marlin"` | `unload_marlin()` |
   | 5 (if any images) | `"Naming images"` | `_llm.get_ingestion_model() or "LLM"` | `_batch_image_titles(_llm, pending)` — ONE chat call naming every image (`[{"index":1,"title":"…"}]`, titles must be 1 < len ≤ 80); each `{{ORB_IMAGE_TITLE_n}}` token in `content` is replaced by its title, or by the filename when the call failed / returned nothing for it. Batched deliberately: a chat-GGUF call per image inside the Florence phase evicted Florence (exclusive residency) and forced a multi-GB reload per image. |

6. If `media_errors` is non-empty → `raise RuntimeError("Multimedia processing failed for: <file>: <err>; …")`. The note fails; no vault write happens (enrichment that succeeded for other attachments is discarded).
7. If content changed (blocks appended, or prior blocks stripped and nothing re-appended) and `note_id` is set: stage `"Saving extracted attachment text"`, then `workflow._persist_note_body(note_id, content)` — loads the `Note` row, resolves its KB via `kb_registry.get_kb(note.kb_id)`, and calls `note_files.persist_note_body(note, kb, content)` (writes the vault `.md`, keeps `notes.content` empty). Wrapped in `tenacity.retry(stop_after_attempt(3), wait_exponential(min=2, max=10))`. Raises `RuntimeError` if the note has no vault.
8. Returns `content.strip()` — so the extraction sees the enriched text, and `processed_content` in the return value equals the vault body modulo trailing whitespace.

### 6.3 `extraction_node`

- Stage `"Extracting knowledge graph"`, model `_llm.get_ingestion_model() or "LLM"` (the KB's ingestion model id; falls back to the literal `"LLM"` when no model is configured).
- Input text: `extraction_content = state["content"]`; if `input.title` is set it is prepended as `# {title}\n\n`. (Re-ingested notes always carry their current `note.title`, so in practice the title is present after the first ingest.)
- **Chunked extraction** (`_extract_with_chunking(_llm, extraction_content, logs)` → `(Extraction, chunk_count)`):
  1. `count = _llm.ingestion_count_tokens` (real tokenizer of the loaded/selected GGUF for local via `local_llama_runtime.count_tokens`; an estimate for cloud) and `overhead = count(_build_extraction_prompt(""))` (the prompt skeleton, ~1.9k tokens).
  2. `budget = chunk_token_budget(_llm.ingestion_context_tokens(), overhead)` from `extraction_chunking.py`: `room = context − overhead − 64`; `fits = room / (1 + 2.5)` (extraction JSON is ~2–3× the input); result `= clamp(min(ceiling, fits), MIN_SPLIT_TOKENS=400, …)` where `ceiling = ORB_EXTRACTION_CHUNK_TOKENS` env (int, min 400) or the default `4000`. Local context comes from `_default_chat_n_ctx()` (`ORB_LLAMA_N_CTX`); cloud providers report a large window so the 4000 ceiling binds.
  3. `split_for_extraction(content, budget, count)`: returns `[content]` if it fits; otherwise splits on blank lines (`\n\s*\n`) and greedily packs whole paragraphs (`_pack`, joined with `\n\n`). A paragraph that alone exceeds the budget is recursively broken lines → sentences (`(?<=[.!?])\s+`) → words → fixed-width character slices (`_split_oversized`). Never returns empty chunks; every word survives (unit-tested).
  4. Each chunk goes through `_extract_chunk(llm, chunk, count)`; with >1 chunk a log/`logs` line `[Extraction] Note is ~N tokens — extracting in K chunks of ≤B tokens` is emitted and results are combined with `merge_extractions`.
- `_extract_chunk` (per chunk, `_MAX_EXTRACTION_ATTEMPTS = 3`, `_MAX_SPLIT_DEPTH = 3`): calls `await llm.ingestion_generate_with_meta(_build_extraction_prompt(text), temperature=0.1)` → `(raw, {"finish_reason", "truncated"})`. If `meta["truncated"]` (local/OpenAI `finish_reason == "length"`, Anthropic `max_tokens`, Gemini `MAX_TOKENS`) and `depth < 3` and the chunk is > 400 tokens, the chunk is split at `max(400, tokens//2)` and each half is extracted recursively (depth+1) and merged — **without** sleeping (tested: truncation is not a retry). If it cannot be split further the partial JSON is repaired and accepted (logged warning). Any exception (LLM error, JSON repair/validation failure) triggers the retry path: wait `30·(attempt+1)` s (30 s, 60 s), then retry; after the third failure `RuntimeError("Extraction failed after 3 attempts: …")`, which `extraction_node` converts into `{"errors": [str(e)]}` → END → note failed.
- `ingestion_generate_with_meta` / `ingestion_generate` (`services/llm.py`) route to the **ingestion** provider client (`INGESTION_PROVIDER` or the KB/instance override, else the chat provider). For local there is **no fixed `max_tokens` cap any more**: `LocalLlamaRuntime` sizes the output to `n_ctx − prompt_tokens − safety margin` and raises when the prompt alone (nearly) fills the window ("split the input or raise ORB_LLAMA_N_CTX"). Cloud defaults: Anthropic `max_tokens=16384`; OpenAI/HF unlimited unless passed. Grammar-constrained / structured output (`ingestion_extract_structured`) is deliberately not used: small GGUFs tend to emit empty nested arrays under schema enforcement.
- JSON repair: `llm._clean_json(raw)` unwraps ```` ```json ```` fences, strips control chars `[\x00-\x08\x0b\x0c\x0e-\x1f]`, normalises curly quotes, then `json_repair.repair_json` (raw string if `json_repair` is missing). Then `Extraction.model_validate_json(cleaned)`.
- Schema normalisation (`schemas/extraction.py`), applied by pydantic `mode="before"` validators:
  - `Extraction.normalize_keys`: `None` → empty; unwraps `{"extraction"|"data"|"result": {...}}`; Gemma-style `[nodes_list, rels_list]`; a bare list of nodes (strings become `{"name": s}`; embedded `"relationships"` inside node dicts are hoisted). `ensure_list` coerces non-lists to `[]` and string node items to `{"name": …}`. `sentiment` defaults `"Neutral"`.
  - `Node.normalize_keys`: `name` ← `trait` or `title` when missing; `isolated_context` ← `evidence_quote` or `context`; every `None` field → `""` (type → `"thing"`).
  - `ExtractedRelationship.normalize_keys`: `source_name` ← `entity1`, `target_name` ← `entity2`, `natural_language` ← `description`; `None`/blank `relationship_type` → `"relates_to"`; `strength`/`confidence`/`relevance` via `_normalize_score` (labels `very high=9, high=8, medium|moderate=6, low=4, very low=2`; 0–1 floats ×10; clamp 1–10; unparsable → defaults **strength 5.0, confidence 7.0, relevance 5.0**).
  - **The prompt never asks for `strength`/`confidence`/`relevance`** (its output schema lists `source_name, target_name, relationship_type, natural_language, reasoning`). Unless the model volunteers them, every relationship gets the defaults and `edge_weight = 5×0.5 + 7×0.3 + 5×0.2 = 5.6`.
  - `title` (optional) and `type_reasoning` / `reasoning` are requested; the reasoning strings are only logged (`[Entity] … reasoning=…`, `[Relationship] … reasoning=…`) and never persisted.
- `merge_extractions(parts)` (chunk merge): nodes de-duplicated by `name.lstrip("#").strip().lower()`; the first occurrence wins, later chunks may upgrade a generic `type == "thing"` and their `isolated_context` strings are appended (exact-duplicate contexts skipped) and finally joined with a single space; relationships de-duplicated on `(norm(source), norm(target), relationship_type.strip().lower())` keeping the highest `confidence`; `title` = first non-empty chunk title; `sentiment` = first non-empty. `None` parts are skipped.
- Garbage-name handling: nodes whose `name.strip().lower()` ∈ `{"untitled","none","unknown",""}` but with an `isolated_context` are sent in one batch to a rename prompt (`_llm.ingestion_generate(temperature=0.0)`, expects a JSON array `[{"index":1,"name":"…"|null}]`; the first `[...]` in the response is parsed). Accepted names must be `2 < len ≤ 80` and not garbage. Nodes with neither name nor context are dropped silently. Rename failures are logged, never fatal.
- Dedup: after renames, nodes are de-duplicated by `name.strip().lower()` keeping the first occurrence. Relationships are **not** de-duplicated here (Kuzu upsert treats repeats as "reinforced").
- Output: `{"extraction", "logs", "status": "EXTRACTED", "timings": {…, "extraction": seconds, "extraction_chunks": K}}`.

### 6.4 `storage_node` → `_write_ontology`

`storage_node` sets stage `"Writing graph and note metadata"` / `None`, computes `created_at = input.created_at or datetime.now().isoformat()`, then runs `workflow._write_ontology(note_id, content, extraction, created_at, custom_title=input.title)` in a worker thread, and finally `_update_note_title(note_id, title)` (SQLite `title` column, tenacity 3 attempts). Any exception → `{"errors": ["Storage failed: …"]}`.

`_write_ontology` (synchronous; returns the resolved title):

**Step 0 — title resolution.** `custom_title` (user/existing title) > `extraction.title` (from the LLM JSON) > `self._llm.generate_title(content, model=self._llm.get_ingestion_model())` (prompt: "Generate a concise, descriptive title for this note. Do not use quotes." — result stripped of `"`; returns `"Untitled Note"` for empty content). `note_title = title.strip() or "Untitled"`.

**Step 0b — note node.** `MERGE (n:Node {id: $note_id}) ON CREATE SET n.kind='note', n.name=$title ON MATCH SET n.kind='note', n.name=$title`. The note node id **is the SQLite note id**. Its `name` is refreshed on every ingest so "Mentioned in notes" panels show the real title.

**Step 1 — entity resolution & node writes** (only if `extraction.nodes`):

1. `normalize_name(name) = name.lstrip("#").strip().lower()`. Entities are keyed by this string everywhere (Kuzu `name`, Qdrant `name` payload, Meili `name`).
2. Batch-embed one stub text per unique normalised name: `f"{norm_name} ({n.type}): {n.isolated_context}"` via `embedding_service.embed_documents` (documents are embedded **without** the Qwen3 instruction prefix; only queries get `"Instruct: Given a question, retrieve relevant context.\nQuery: "`). One llama-cpp `create_embedding` call for the whole list (commit `8de5cda`).
3. Resolve existing ids: `qdrant.find_node_ids_by_names(unique_names)` — one filtered scroll over `node_cores` with `MatchAny(name)`, paging in 500s until every name is found or exhausted; first non-empty mapping per name wins. Names still `None` are looked up in Kuzu with `graph.find_nodes_by_exact_names` (`kind='indexable' AND toLower(name) IN $names`, smallest id wins for duplicates). This fallback prevents minting a new UUID when a previous run wrote Kuzu but its Qdrant stub failed.
4. Assign ids: existing id, else `f"node_{uuid4()}"`. `seen_in_batch` collapses duplicates within the extraction. `node_type = (type or "").strip().lower() or "thing"`.
5. Kuzu write (single statement):
   ```cypher
   MERGE (note:Node {id: $note_id})
   ON CREATE SET note.kind = 'note', note.name = $note_title
   ON MATCH SET note.kind = 'note', note.name = $note_title
   WITH note
   UNWIND $data AS item
   MERGE (n:Node {id: item.id}) ON CREATE SET n.kind = 'indexable'
   SET n.name = item.name, n.type = item.type
   MERGE (note)-[r:REFERENCES]->(n)
   SET r.note_id = $note_id
   ```
   `item.type` falls back to `"unknown"` when empty. `REFERENCES` is idempotent per (note, entity).
6. Qdrant stub seeding: `upsert_node_cores([{node_id, name, node_type, description_vector: stub_embedding} for NEW nodes with an embedding])` — one batched upsert. **If it returns False the ingest aborts** (`RuntimeError("Failed to seed N Qdrant node_cores stub(s) — aborting ingest to avoid Kuzu/Qdrant ID split-brain…")`). The stub exists so that `_update_node_summary`, running minutes later, resolves the same id via `find_node_id_by_name` instead of minting another; it is overwritten by the merged-context vector in step 6b of §6.5.

There is **no fuzzy/embedding-similarity entity resolution** at ingest time: matching is exact on the normalised lowercase name. `SEMANTIC_REL.is_similarity` exists in the Kuzu schema and `ingestion_tracker.force_similarity_detection()` exists as a no-op, both leftovers of the Neo4j-era "post-ingestion similarity detection" (see History). Nothing in the current backend writes `is_similarity`.

**Step 6 — relationships** (numbered "6" in code; only if `extraction.relationships`):

1. Endpoint names normalised with `lower().strip()` (**not** `lstrip("#")` — a mismatch with node normalisation; a relationship whose endpoint was extracted as `#hashtag` will not resolve).
2. Names not present in `name_to_id` (i.e. entities from earlier notes that this extraction only references in relationships) are batch-resolved once via `qdrant.find_node_ids_by_names`.
3. Per relationship: skip (counted) when a name is empty, when the source or target id cannot be resolved; blank type → `"relates_to"`; `clean_rel_type(rel_type, source_name, target_name)` removes any underscore-separated token of the predicate that also appears (case-insensitively, tokens > 1 char) in either entity name — `plays_corliss_archer` → `plays`; an all-entity predicate becomes `relates_to`.
4. `graph.create_or_update_relationship(source_name, "Indexable", target_name, "Indexable", relationship_type, confidence, strength, relevance, natural_language=(nl or "").replace("_"," "), note_id, source_id, target_id)`:
   - sanitises the type with `re.sub(r"[^A-Za-z0-9_]", "_", …)`; mints `relationship_id = uuid4()`; computes `edge_weight = round(strength*0.5 + confidence*0.3 + relevance*0.2, 4)`; `ingestion_time = datetime.utcnow().isoformat()`.
   - looks up an existing edge with the same `(source_id, target_id, rel_type)`. **Exists → `action="reinforced"`**: `last_updated = now`, `mention_count = coalesce(mention_count,0)+1`, `confidence = max(old,new)`, and `relationship_id/strength/relevance/edge_weight` are only filled when NULL (first writer wins; `note_id` is not touched, so the edge keeps the id of the first note that stated it). **Missing → `action="created"`**: `MERGE` both endpoint nodes as `kind='indexable'` (defensive), then `CREATE (source)-[r:SEMANTIC_REL]->(target)` with `rel_type, confidence, strength, relevance, edge_weight, relationship_id, ingested_at=now, last_updated=now, mention_count=1, note_id`.
   - Returns a dict including `action`, `relationship_id`, `natural_language`. (`"evolved"` is checked by the caller but never produced by the current implementation; `"failed"` is returned only when ids are unresolvable, which the caller already prevents.)
   - Semantics: `ingested_at` = first time this (src,tgt,type) triple was seen in this KB; `last_updated` = last time any note restated it; `mention_count` = number of ingest runs that stated it — **re-ingesting the same note increments it again** (there is no per-note dedup).
5. Every `created` result is queued for Qdrant with `nl_text = result.natural_language or rel_type.replace("_"," ")`, then all NL texts are embedded in one call and written with one `upsert_node_relationships([...])` (point id `uuid5(NAMESPACE_OID, relationship_id)`, payload `{natural_language, source_node_id, target_node_id}`). Reinforced edges are not re-written to Qdrant (their point already exists under the original `relationship_id`).
6. Exceptions inside the per-relationship loop are collected; if any occurred, `RuntimeError("Failed to write N relationship(s): …")` is raised **after** the loop (so the successful ones stay written, and the Qdrant batch for them is skipped because the raise happens before it). Summary log line: `[Relationship] note_id=… total= written= skipped= (no_name= no_source= no_target=)`.

Note: `_write_ontology` does **not** write `Extraction.sentiment`, `type_reasoning`, `reasoning`, or the note body anywhere. It also writes no `note_created_at`; dates travel only with isolated contexts (§6.5).

### 6.5 `summarization_node` → `_update_neighborhoods` → `_update_node_summary`

Despite the names (kept from an earlier design that generated per-node LLM summaries), this stage generates **no LLM text**. It accumulates verbatim isolated contexts per entity, embeds them, and indexes them.

`summarization_node`: skips (`return {}`) when `errors` is set or `extraction` is missing; otherwise stage `"Indexing entity contexts"` / `"Embeddings"`, then `await workflow._update_neighborhoods(extraction.nodes, content, note_created_at=state["created_at"])`. Any exception → `{"errors": ["Context indexing failed: …"], "status": "FAILED"}`; success → `status="INDEXED"`.

`_update_neighborhoods(nodes, new_content, note_created_at)`:

- Groups nodes by `name.lstrip("#").strip().lower()`; per name keeps the first node's `type` (lower-cased, default `"thing"`) and a de-duplicated list of contexts (`node.isolated_context or new_content` — an entity with no context gets **the whole note body** as its context).
- Runs `_update_node_summary("Indexable", name, contexts, node_type, note_created_at)` for each unique name under `asyncio.Semaphore(4)` via `asyncio.gather` (so one failure cancels nothing — `gather` without `return_exceptions` raises the first exception after all tasks finish or are cancelled by the loop; the note is then failed).

`_update_node_summary(label, name, new_contexts, node_type, note_created_at)` — under `EntityLockManager.get_lock(label, name)`:

| Step | Action | Store call |
|---|---|---|
| 1 | Resolve `node_id`: `qdrant.find_node_id_by_name(name)` (one filtered scroll on `node_cores`, `limit=1`). Found → `qdrant.get_node_content_by_id(node_id)` gives `_core_content` (`name,type,description,community_level,isolated_contexts[]` where each context is rendered `"{content} - {note_created_at}"` when dated). Not found → `graph.find_nodes_by_name([name], fuzzy=False)` filtered to `labels[0]=="indexable"`, smallest id wins; if found, reuse it (warning logged) and still fetch contexts from Qdrant. Not found anywhere → mint `node_{uuid4}` and `MERGE (n:Node {id}) ON CREATE SET n.kind='indexable' SET n.name, n.type`. | Qdrant scroll/retrieve; Kuzu MERGE |
| 2 | Append new contexts whose `strip()`ed text is not already among existing contexts. Because existing contexts come back as `"{content} - {date}"`, **the same context text from a re-ingested note does not match** and is appended again (see Gotchas). | — |
| 3 | `node_type = _core_content.type or node_type or "thing"` — the type stored on first sighting sticks; later extractions cannot change it via this path (Kuzu `n.type` *is* overwritten by `_write_ontology`'s `SET n.type = item.type` though — Kuzu and Qdrant `type` can therefore diverge). | — |
| 4 | Embed `[new contexts…, merged_ctx_text]` in one `embed_documents` call, where `merged_ctx_text = " ".join(all contexts incl. new)`. | llama-cpp batch |
| 5 | `MERGE (n:Node {id}) ON CREATE SET n.kind='indexable' SET n.name=$name, n.type=$type` (idempotent; ensures Kuzu has name/type without needing Qdrant for labels). | Kuzu |
| 6 | `qdrant.append_node_item(col_contexts, node_id, content, vector, note_created_at)` per new context → point id `uuid4`, payload `{parent_node_id, content, note_created_at?}`. Counts successes; **any shortfall raises** `RuntimeError("Failed to persist contexts to Qdrant for '…' (k/n)…")`. | Qdrant upsert ×n |
| 6b | If `merged_ctx_text` is non-empty: `qdrant.upsert_node_core(node_id, name, node_type, description_vector=merged_vector, description=merged_ctx_text)` — overwrites the stub from §6.4 (same point id `uuid5(node_id)`) with a vector of **all** accumulated contexts joined, so multi-constraint queries can match the whole entity at once. `False` → `RuntimeError`. | Qdrant upsert |
| 7 | Meilisearch, only after Qdrant succeeded: `rel_nl = " ".join(natural_language of qdrant.get_relationships_for_node_ids([node_id]))`; `contexts_text = " ".join(all contexts)`; if that is empty, fall back to the existing Meili doc's `isolated_contexts` (because `index_node` → `add_documents` **replaces** the whole document, an empty upsert would wipe it); `meili.index_node(node_id, name, node_type, isolated_contexts_text, relationship_natural_language)` (failures here are logged inside `index_node`, not raised). | Meili add_documents |

Log line on success: `[NodeSummary] ✓ COMPLETE: '<name>' (type='…', id=…)`.

Note the asymmetry: the Qdrant `description` payload and the Meili `isolated_contexts` field are **denormalised copies** of the per-context points; they are rebuilt from the in-memory `existing_contexts` list each time an entity is touched, never from a fresh scroll after the appends.

### 6.6 Mark processed / failed

| Outcome | Function | SQLite `notes` update |
|---|---|---|
| Agent finished with empty `errors` | `_mark_note_processed` (tenacity ×3) | `processed=True, failed=False, processing_stage="Ingestion complete", processing_model=None` |
| Agent returned `errors`, or raised (multimedia, LangGraph internals), or `_mark_note_processed` / `_queue_leiden_recompute_if_due` raised | `_update_note_processing_status(note_id, "Ingestion failed")` then `_mark_note_failed` (tenacity ×3) | `processed=False, failed=True, processing_stage="Ingestion failed", processing_model=None` |

`GET /api/v1/notes/{id}/status` derives `status`: `completed` if `processed`, else `failed` if `failed`, else `processing` — so `"Saved"` notes also report `processing`; clients must read `processing_stage`.

The `title` column was already updated by `storage_node` (`_update_note_title`) before this point, so a note that fails in summarization can still have received a new LLM-generated title. `_update_note_title` writes only `notes.title`; it does **not** rename the vault `.md` (unlike `PUT /api/v1/notes/{id}` which calls `rename_note_file_for_title`), so an untitled note ingested through `POST /api/v1/ingest` ends up with an LLM title in SQLite but an `Untitled.md`-style filename until the user re-titles it.

## 7. Re-ingest semantics and cleanup

There is **no per-note "delete previous graph output" step**. Re-ingesting a note relies on idempotent writes plus one content-level cleanup:

| Artifact | What happens on re-ingest of the same note |
|---|---|
| Vault `.md` body | Enrichment blocks are stripped from the *first* marker onward (`_strip_prior_multimedia_enrichment`), multimedia is re-run, and a single fresh enrichment section is re-appended and written back. User text typed *after* an enrichment block is lost. |
| Note node (`kind='note'`) | `MERGE` by id; `name` refreshed to the current title. |
| Entity nodes | Resolved by normalised name → same ids reused (Qdrant name lookup, Kuzu fallback). Entities that the new extraction no longer mentions **remain** in Kuzu/Qdrant/Meili, still linked by `REFERENCES` from this note. |
| `REFERENCES` edges | `MERGE` — idempotent; stale ones are never removed. |
| `SEMANTIC_REL` edges | Same `(src, tgt, rel_type)` → `reinforced`: `mention_count += 1`, `last_updated` bumped, `confidence = max`. A differently-worded predicate for the same pair creates an **additional** edge. Edges the new extraction no longer produces remain. |
| Qdrant `node_relationships` | New edges → new points; reinforced edges untouched. |
| Qdrant `node_isolated_contexts` | Contexts are appended when their text differs from the stored `"{content} - {date}"` rendering — a re-extracted identical sentence **is appended again** (the comparison is against the date-suffixed rendering, so it never matches when the note has a `created_at`). Context points carry no `note_id`, so nothing can delete "this note's contexts" later. |
| Qdrant `node_cores` | `description` / vector rebuilt from the accumulated context list. |
| Meilisearch doc | Replaced with the rebuilt `isolated_contexts` / `relationship_natural_language`. |
| SQLite | flags reset by the API before queueing; title possibly rewritten. |

Real cleanup happens only in two places outside the pipeline:

- `DELETE /api/v1/notes/{id}` / `POST /api/v1/notes/batch-delete` (`api/notes.py::_delete_note_impl`): removes vault file + row + `note_links`, then best-effort: finds entities referenced **only** by this note (`MATCH (note)-[:REFERENCES]->(entity) WHERE NOT EXISTS {other note → entity}`), `DETACH DELETE`s the note node, `qdrant.delete_node(note_id)` + `meili.delete_node(note_id)` (no-ops in practice — notes have no Qdrant/Meili docs), then for each orphan entity `DETACH DELETE` in Kuzu and `qdrant.delete_node(entity_id)` (core point by `uuid5(id)` + all context points with `parent_node_id`) and `meili.delete_node(entity_id)`. Relationship points in `node_relationships` for those orphans are **not** deleted (`delete_node` only touches cores and contexts). Attachments referenced by the body are removed via `remove_upload`.
- `POST /api/v1/admin/reset-ingestion-data`: `graph.wipe_all_nodes()` (`MATCH (n:Node) DETACH DELETE n`), `qdrant.reset_all()` (drop + recreate the three collections), `meili.reset_all()` (drop + recreate index), and `UPDATE notes SET processed=false, failed=false` for the KB. Vault files are untouched (enrichment blocks stay in the `.md`, which is fine because the next ingest strips them).

Consequences for anyone reasoning about counts: `mention_count` and the number of contexts per entity grow with every re-ingest; `ingested_at` never moves; entity `type` in Qdrant/Meili is frozen at first sighting.

## 8. Post-ingestion triggers: tracker, communities, temporal digests

### 8.1 `_queue_leiden_recompute_if_due(note_id)` (runs after `_mark_note_processed`)

- If `COMMUNITY_DETECTION_ENABLED` (default **False**): `MATCH (:Node {id:$note_id})-[*1]-(n:Node) WHERE n.kind='indexable' RETURN DISTINCT n.id` → `tracker.queue_nodes_for_community_recompute(ids)`. The ids are only used for logging/status (`pending_community_nodes`); the recompute is always a **full** rebuild.
- If `TEMPORAL_DIGESTS_ENABLED` (default **False**): (re)start a per-workflow `threading.Timer(COMMUNITY_IDLE_SECONDS, self.build_temporal_digests)` (daemon). Both switches are independent.

### 8.2 `IngestionTrackerService` (`services/ingestion_tracker.py`, global singleton `ingestion_tracker`)

| Member | Semantics |
|---|---|
| `COMMUNITY_IDLE_SECONDS = 120` | Module constant (not an env var). Idle window before community recompute and digest build. |
| `_active_ingestion_count` | Notes currently inside `process_note` (queued or running), across **all KBs**. |
| `begin_ingestion()` (async) | `count += 1`; cancel any pending debounce task; **set** `cancel_recompute` and `cancel_temporal` events (a running recompute/digest stops at its next checkpoint). |
| `end_ingestion(callback)` (async) | `count = max(0, count−1)`; when it reaches 0 and no recompute is running and `COMMUNITY_DETECTION_ENABLED`: `schedule_recompute(callback)`. |
| `queue_nodes_for_community_recompute(ids)` (async) | Adds to `_pending_node_ids`, sets `_recompute_needed=True`; if a recompute is running, sets `cancel_recompute`. Returns `([], queue_size)`. |
| `schedule_recompute(callback)` | Debounce: cancel existing task, `loop.create_task(_debounce_recompute(callback))`. Needs a running loop (else warns). |
| `_debounce_recompute(callback)` (async) | `await asyncio.sleep(120)`; skip if a recompute is running or nothing pending and not `_recompute_needed`; else mark running, clear pending, clear `cancel_recompute`, `await asyncio.to_thread(callback)`; afterwards `mark_community_recompute_complete()`; if the run was cancelled early (`cancel_recompute` set), set `_recompute_needed=True` and reschedule if no ingestion is active. |
| `cancel_recompute`, `cancel_temporal` | `threading.Event`s polled by the worker-thread jobs. |
| `has_active_ingestions()`, `mark_ingestion_complete()` (unused), `force_similarity_detection()` (compat no-op) | — |
| `get_status_snapshot()` | `{active_ingestions, pending_community_nodes, community_recompute_running, community_recompute_needed, community_idle_seconds, last_ingestion_at, community_timer_armed}` |

`IngestionWorkflow.get_maintenance_status()` (served by `GET /api/v1/admin/maintenance-status`) wraps that snapshot: `{"community_detection": {running, pending_nodes, needed, timer_armed, idle_seconds}, "temporal_digests": {"running"}, "ingestion": {"active", "last_completed_at"}, "healthy": true}`. `running` ORs the per-KB `_community_run_running` with the tracker flag; `last_completed_at` is always `None` in practice because `mark_ingestion_complete()` is never called.

### 8.3 `rebuild_leiden_communities()` (sync, worker thread; also `POST /api/v1/admin/rebuild-communities`)

Historical name — the current implementation is **agglomerative clustering on embeddings** (`sklearn.cluster.AgglomerativeClustering`, cosine metric, average linkage, `n_clusters=None`), not Leiden.

1. Single-flight hand-off: bump `_community_run_seq`; if another run is active set `cancel_recompute` and spin (0.25 s) until it releases; a request superseded by a newer one returns 0. Clear `cancel_recompute` for the claimed run, but re-set it immediately if ingestion is active.
2. Input: `graph.get_indexable_nodes_for_communities()` (all `kind='indexable'` nodes) enriched with `isolated_contexts` from `qdrant.get_nodes_content_by_ids` (one retrieve + one bulk scroll).
3. `graph.clear_all_communities()`: `DELETE` all `MEMBER_OF`, `DETACH DELETE` all `kind='community'` nodes, `qdrant.delete_community_relationships()` (points with `is_community_rel=true`); then per old community id `qdrant.delete_node` + `meili.delete_node`.
4. Levels (all embed with `embedding_service.embed_documents`, L2-normalised so cosine distance = 1 − cos):
   - **L2** over entities, text `"{name}: ctx1 | ctx2 …"`, `distance_threshold=0.25`.
   - **L1** over L2 communities, text `"{name}: {summary}"`, `distance_threshold=0.35` (the **default** argument — the code comment claims 0.50; the call passes nothing).
   - **L0** over L1 communities, `distance_threshold=0.75`. Orphan entities never propagate upward (the L0 orphan branch is dead code because `l0_super_ids` only contains L1 ids).
5. `_commit_community(level, member_entity_ids, rollup_rows, label)`: skip singletons and single-rollup clusters; name+summary via one `self._llm.reason(prompt, model=get_ingestion_model())` call — `_build_community_summary` (L2, from member contexts) or `_build_rollup_summary` (L1/L0, from child summaries); reply parsed by `_parse_name_summary` (`NAME:` / `SUMMARY:` lines). Generic names (`_is_generic_community_name`: "community …", `community l2-14`, banned phrases like "isolated conceptual cluster", or names made only of filler tokens) trigger up to 2 strict retries, then `_derive_fallback_community_name` (`"About X"`, `"X and Y"`, `"X and related topics"`, or `"Related knowledge topics"`). Missing summary → `"Community at level N containing K related nodes."`.
6. Writes per community `community_id = f"community_l{level}_{uuid4().hex}"`: Kuzu `MERGE (c:Node {id}) SET kind='community', type='community', name` + `MERGE (c)-[:CONTAINS]->(member)`; Qdrant `node_cores` point (`type="community"`, `description=summary`, `community_level`); Meili doc (`type="community"`, `community_level`); Qdrant `node_relationships` points `community_rel_{community_id}_{nid}` with NL `"{member} is a member of the '{name}' community."` and `is_community_rel=True`; Kuzu `MERGE (n)-[r:MEMBER_OF]->(c) SET r.level`.
7. Cooperative cancellation is checked before every cluster; on cancel the function returns the count built so far (partial hierarchy stays; the tracker reschedules a full run).
8. Afterwards: one batched `meili.update_nodes_community(rows)` refreshing `name` + `relationship_natural_language` (from two bulk Qdrant fetches) for every entity that got a community; then `compute_spring_layout_3d` over `graph.get_all_node_ids_and_edges()` and `graph.store_node_positions` (non-fatal).

### 8.4 `build_temporal_digests(period=None)` (sync, timer thread; also `POST /api/v1/admin/build-temporal-digests`)

- Returns 0 immediately if `TEMPORAL_DIGESTS_ENABLED` is False (even for the manual endpoint — unlike communities, the switch gates the function itself) or if ingestion is active.
- `qdrant.scroll_all_isolated_contexts_with_dates()` → bucket `content` by `note_created_at` into `period` ∈ `month` (`%Y-%m`, default from `TEMPORAL_DIGEST_PERIOD`) | `week` (`%G-W%V`) | `year` (`%Y`).
- `graph.clear_all_temporal_digests()` + `qdrant.delete_node` + `meili.delete_node` for old digest ids.
- Per bucket (sorted): concatenate contexts with `\n---\n`, truncate to 12 000 chars, `self._llm.generate_text(system="You are a knowledge synthesis assistant…", user="The following contexts are from notes created during {label}: …")` (fallback summary `"Notes from {label}."`); `node_id = f"digest_{period}_{key}"` with `-`→`_` and `W`→`w` (e.g. `digest_month_2026_05`, `digest_week_2026_w21`); `name = f"{label} — {Period} Digest"` (e.g. `"May 2026 — Month Digest"`); Kuzu `MERGE (d:Node {id}) SET kind='temporal_digest', type='temporal_digest', name`; Qdrant core with `extra_payload={"period_key": key}`; Meili doc `type="temporal_digest"`, `isolated_contexts=summary`.
- Checks `cancel_temporal` between buckets; on cancel reschedules itself after 120 s if no ingestion is active.
- Note: the Kuzu module docstring lists `kind ∈ {note, indexable, community}`; `temporal_digest` is a fourth value written by this function.

## 9. Stage → function → writes → failure behaviour

| # | Stage (`processing_stage`) | Function(s) | Writes | On failure |
|---|---|---|---|---|
| 0 | `Queued for ingestion` / `Queued for vault re-ingest` | API handler | SQLite flags | request errors only (404/503) |
| 1 | `Queued for ingestion` → `Starting ingestion` | `process_note` | SQLite stage; tracker counter | — |
| 2 | `Preparing multimedia attachments` … `Unloading video model`, `Naming images`, `Saving extracted attachment text` | `multimodal_node`, `multimedia_service.*`, `_batch_image_titles`, `_persist_note_body` | vault `.md` (only if changed) | any attachment error → collected → `RuntimeError` after all attachments tried → note **failed**; vault not written. Image-title failure → filename fallback (non-fatal). Vault write retried ×3 then fatal. |
| 3 | `Extracting knowledge graph` | `extraction_node`, `_extract_with_chunking`, `_extract_chunk`, `merge_extractions` | none | per chunk: truncation → split & merge; other errors → 30 s / 60 s retries; 3rd failure → `errors` → END → **failed**. Rename failure non-fatal. |
| 4 | `Writing graph and note metadata` | `storage_node` → `_write_ontology`, `_update_note_title` | Kuzu note node, entity nodes, `REFERENCES`, `SEMANTIC_REL`; Qdrant `node_cores` stubs, `node_relationships`; SQLite `title` | Qdrant stub batch failure → abort (Kuzu nodes already written remain). Relationship errors collected → raise after loop (successful edges remain; Qdrant rel batch skipped). Any exception → `errors` → **failed**; no rollback. |
| 5 | `Indexing entity contexts` (`Embeddings`) | `summarization_node` → `_update_neighborhoods` → `_update_node_summary` | Kuzu entity MERGE; Qdrant `node_isolated_contexts` (+), `node_cores` (overwrite); Meili doc (replace) | context append shortfall or core upsert `False` → `RuntimeError` → **failed** (earlier entities of the same note are already indexed). Meili errors logged, non-fatal. |
| 6 | `Ingestion complete` | `_mark_note_processed` | SQLite `processed=1, failed=0` | SQLite failure retried ×3, then note marked failed |
| 7 | (none) | `_queue_leiden_recompute_if_due` | tracker pending set; digest timer | Kuzu query error → propagates → note marked **failed** even though data is written (edge case) |
| 8 | `Ingestion failed` | `_mark_note_failed` | SQLite `processed=0, failed=1` | exception re-raised → aborts later `BackgroundTasks` of the same request |
| 9 | (none) | `tracker.end_ingestion` | debounce task | — |

## 10. Exactly what is written where

### 10.1 Kuzu (per-KB database; schema in `graph.py::_SCHEMA_STMTS`)

Node table `Node(id PK, kind, name, type, pos_x, pos_y, pos_z)`:

| `kind` | `id` | `name` | `type` | Written by |
|---|---|---|---|---|
| `note` | SQLite note id | note title (`"Untitled"` fallback) | NULL | `_write_ontology` |
| `indexable` | `node_<uuid4>` | normalised lowercase entity name | LLM type lower-cased (`thing` / `unknown` fallbacks) | `_write_ontology`, `_update_node_summary`, `create_or_update_relationship` (defensive MERGE without name) |
| `community` | `community_l{0,1,2}_<hex>` | LLM/fallback name | `community` | `create_leiden_community` |
| `temporal_digest` | `digest_{period}_{key}` | `"May 2026 — Month Digest"` | `temporal_digest` | `create_temporal_digest_node` |

`pos_x/y/z` are set only by `store_node_positions` after a community rebuild.

Relationship tables:

| Table | From → To | Properties | Written by |
|---|---|---|---|
| `REFERENCES` | note → indexable | `note_id` | `_write_ontology` (MERGE) |
| `SEMANTIC_REL` | indexable → indexable | `rel_type, confidence, strength, relevance, edge_weight, relationship_id, ingested_at, last_updated, mention_count, note_id, is_similarity (never set), created_at (never set)` | `create_or_update_relationship` |
| `CONTAINS` | community → indexable | — | `create_leiden_community` |
| `MEMBER_OF` | indexable → community | `level` | `set_node_community_membership` |

### 10.2 Qdrant (three per-KB collections; names from the KB registry: default KB uses `node_cores` / `node_relationships` / `node_isolated_contexts`, other KBs `{slug}_node_cores` etc.; vector size `EMBEDDING_DIMENSIONS`, cosine)

| Collection | Point id | Vector of | Payload | Writer |
|---|---|---|---|---|
| cores | `uuid5(NAMESPACE_OID, node_id)` — one point per node | stub: `"{name} ({type}): {isolated_context}"`; later: all contexts joined | `node_id, name, type, description?, community_level? (communities only), period_key? (digests only)` | `upsert_node_cores` (stubs), `upsert_node_core` (merged; communities; digests) |
| relationships | `uuid5(NAMESPACE_OID, relationship_id)` | `natural_language` text | `natural_language, source_node_id, target_node_id, is_community_rel? (true only for membership sentences)` | `upsert_node_relationships` |
| isolated_contexts | `uuid4` (append-only) | one context sentence/paragraph | `parent_node_id, content, note_created_at?` | `append_node_item` |

Embedding instruction: documents are embedded **without** any prefix (`embed_documents`); queries get `"Instruct: Given a question, retrieve relevant context.\nQuery: "` only when the embed model name contains `qwen3` (`embed_query`). `USE_DYNAMIC_EMBEDDING_INSTRUCTION` (default True) is declared in `core/config.py` but **not referenced anywhere in `backend/app`** — a leftover; ingestion never embeds with an instruction. `QdrantService._prepare_vector` raises `ValueError` if a vector's length ≠ `EMBEDDING_DIMENSIONS` (collections are never resized mid-ingest; that belongs to `sync_embedding_infrastructure` at startup / model change).

### 10.3 Meilisearch (per-KB index, `primaryKey: node_id`; searchable `name, type, isolated_contexts, relationship_natural_language`; filterable `type, community_level`)

Document written by `index_node`: `{node_id, name, type, isolated_contexts?: <all contexts joined by " ">, relationship_natural_language?: <all rel NL joined>, community_level?}` — `add_documents` replaces the whole document. Notes are **not** indexed in Meili (only entities, communities, digests).

### 10.4 SQLite `notes` (metadata only)

`processed`, `failed`, `processing_stage`, `processing_model`, `title`. Never `content` (kept `""`). Full stage vocabulary:

| Source | Strings |
|---|---|
| API / watcher | `Saved`, `Queued for ingestion`, `Queued for vault re-ingest`, `Changed on disk — re-ingest when ready`, `External delete detected — review in Orb` |
| `process_note` | `Queued for ingestion`, `Starting ingestion`, `Ingestion complete`, `Ingestion failed` |
| `multimodal_node` | `Preparing multimedia attachments`, `Reading PDF pages and images`, `PDF: page i/N, extracting text`, `PDF: page i/N, describing image j/M`, `PDF: page i/N, describing page render`, `PDF: page i/N complete`, `Describing images`, `Unloading image model`, `Extracting documents`, `Extracting spreadsheets`, `Transcribing audio`, `Transcribing video audio`, `Unloading speech model`, `Analyzing video visuals`, `Unloading video model`, `Naming images`, `Saving extracted attachment text` |
| agent nodes | `Extracting knowledge graph`, `Writing graph and note metadata`, `Indexing entity contexts` |

`processing_model` values: `Florence-2`, `Whisper`, `Marlin`, `Embeddings`, the ingestion model id (or `LLM`), else `None`.

### 10.5 Vault

Only the note's own `.md` (enriched body via `note_files.persist_note_body`, which normalises vault-file refs and marks the write as self-originated for the watcher). Attachments are never modified or deleted by ingestion.

### 10.6 Prompt skeletons

Extraction (`_build_extraction_prompt(extraction_content)`, ~1.9k tokens of fixed text; full text in `ingestion_agent.py`):

```
You are a precision knowledge extraction engine. …
## CORE RULES  (extract every entity; no outside knowledge; no hallucinated relationships;
                co-reference resolution; directional relationships; canonical names)
## STEP-BY-STEP PROCESS
### STEP 1 — Entity Extraction   → name, type (examples: Person, Place, Organization, Event,
                                    Work, Thing, Concept, Time Period — not exhaustive), type_reasoning
### STEP 2 — Relationship Extraction → source_name, target_name, relationship_type (snake_case verb),
                                    natural_language, reasoning
### STEP 3 — Node Context Generation → isolated_context per node, note-only, entity-centric
## OUTPUT FORMAT   {"title": …, "nodes":[{name,type,type_reasoning,isolated_context}],
                    "relationships":[{source_name,target_name,relationship_type,natural_language,reasoning}]}
## WORKED EXAMPLE  (Ama / Kofi / Primary School / Neighborhood / Weekend / Homework)
Now apply this entire process to the following note and return only the JSON output, nothing else:

{extraction_content}
```

Rename prompt: `"For each numbered excerpt below, provide the most specific descriptive name for the node it describes (1–5 words each). … Return ONLY a JSON array: [{"index": 1, "name": "Name Here"}, {"index": 2, "name": null}, …]"` followed by `1. (type=…) "<isolated_context>"` lines.

Image titling prompt: `"For each numbered image description below, give a concise, specific title (2–6 words) that would serve as a unique entity name. … Return ONLY a JSON array: [{"index": 1, "title": "..."}, ...]"` with `n. (file: <filename>) "<caption[:600]>"` lines.

Title prompt (`llm_service.generate_title`): system `"Generate a concise, descriptive title for the provided note content. Do not use quotes."`, user `"Note content:\n{text}\n\nTitle:"` (single-turn variants for Gemini/Anthropic). Community and digest prompts are quoted in §8.

## 11. Configuration

| Key (env / `settings`) | Default | Used by | Effect |
|---|---|---|---|
| `INGESTION_PIPELINE_CONCURRENCY` | `1` | `IngestionWorkflow.__init__` | Size of the per-KB `asyncio.Semaphore` gating `process_note`. |
| `MULTIMEDIA_CONCURRENCY` | `1` | `ingestion_agent.py` module import | Global semaphore around `multimodal_node`. Read once at import. |
| `INGESTION_AGENT_CONCURRENCY` | `2` | — | **Unused.** |
| `INGESTION_PROVIDER` | `None` (→ chat provider) | `LLMService._init_ingestion_clients` | Separate provider for extraction (`local`, `openai`, `gemini`, `anthropic`, `huggingface`; `ollama`/`lm_studio` map to `local`). Per-KB override wins. |
| `INGESTION_MODEL` | `None` | `get_ingestion_model` | Wins over provider-specific keys (after per-instance override). |
| `INGESTION_LLM_MODEL` | `"local-chat"` | `get_ingestion_model` (local) | Local ingestion model id; falls back to `LLM_MODEL`. |
| `INGESTION_GEMINI_MODEL` | `None` | `get_ingestion_model` (gemini) | Falls back to `GEMINI_MODEL`. |
| `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `HUGGINGFACE_MODEL`, `LLM_MODEL` | `None` / `"local-chat"` | `get_ingestion_model` | Provider fallbacks. |
| `ORB_EXTRACTION_CHUNK_TOKENS` (env only) | `4000` | `extraction_chunking.chunk_token_budget` | Ceiling on input tokens per extraction chunk (min 400). |
| `ORB_LLAMA_N_CTX` (env, via `_default_chat_n_ctx`) | see [12](12-local-models-and-inference.md) | `ingestion_context_tokens` | Local context window → chunk budget and output budget. |
| `ORB_MODEL_IDLE_SECONDS` (env) | 300 | GGUF idle watcher | When resident models are unloaded after ingestion. |
| `COMMUNITY_DETECTION_ENABLED` | `False` | `_queue_leiden_recompute_if_due`, `tracker.end_ingestion` | Automatic post-ingestion community rebuild. Manual endpoint ignores it. |
| `COMMUNITY_RECOMPUTE_BATCH_SIZE` | `100` | — | **Unused.** |
| `TEMPORAL_DIGESTS_ENABLED` | `False` | `_queue_leiden_recompute_if_due`, `build_temporal_digests` | Gates both the timer and the function (manual endpoint included). |
| `TEMPORAL_DIGEST_PERIOD` | `"month"` | `build_temporal_digests` | `month` \| `week` \| `year`. |
| `COMMUNITY_IDLE_SECONDS` | `120` (constant) | tracker, digest timer | Not configurable. |
| `EMBEDDING_DIMENSIONS` | `1024` (overridden from the local manifest's `embedding_dims`) | `QdrantService._prepare_vector` | Vector length check on every upsert. |
| `EMBEDDING_MODEL` | `"local-embed"` | `EmbeddingService` | `is_qwen3` (query instruction) derived from its basename. |
| `USE_DYNAMIC_EMBEDDING_INSTRUCTION` | `True` | — | **Unused** in backend. |
| `QDRANT_COLLECTION_NODE_CORES` / `_RELATIONSHIPS` / `_ISOLATED_CONTEXTS` | `node_cores` / `node_relationships` / `node_isolated_contexts` | default KB `QdrantService` | Other KBs use `{slug}_…` names from the registry. |
| `MEILI_INDEX_NAME` | `orb_nodes` | default KB `MeilisearchService` | Other KBs `{slug}_nodes`. |
| `LLM_PROVIDER` (via `ai_gate`) | `"local"` | `require_ai(kb)` derives readiness from GGUFs / keys / `LLM_BASE_URL`; `chat_is_local_only()` decides the cloud-vision fallback in `multimedia.describe_image` | `AI_SETUP_MODE` no longer gates either. |
| `PDF_VISUAL_*`, `FLORENCE_MAX_IMAGE_PIXELS`, `MODEL_*_LOCAL/HF`, `VIDEO_MAX_PIXELS`, `FPS*` | see [11](11-multimedia-enrichment.md) | multimedia | — |

## 12. Interfaces with other subsystems

| Direction | Contract |
|---|---|
| API → pipeline | `kb.get_ingestion_workflow().process_note(NoteInput, note_id)` scheduled with `BackgroundTasks`; note row must already exist with `kb_id` matching the KB (used to find the vault in `_persist_note_body`). |
| Pipeline → `kb_registry` | `kb_registry.get_kb(note.kb_id)` for vault path; `KBContext` supplies `graph`, `qdrant`, `meili`, `llm`. |
| Pipeline → `llm_service` (or per-KB `LLMService`) | `ingestion_generate_with_meta`, `ingestion_generate`, `ingestion_count_tokens`, `ingestion_context_tokens`, `_clean_json`, `generate_title`, `reason`, `generate_text`, `get_ingestion_model`, `provider`. Any object with these methods works (the unit tests use a stub). |
| Pipeline → `embedding_service` | `embed_documents(list[str]) -> list[list[float]]` (one llama batch call; raises on length mismatch). |
| Pipeline → `GraphService` | `execute_query`, `find_nodes_by_exact_names`, `find_nodes_by_name(fuzzy=False)`, `create_or_update_relationship`, `get_indexable_nodes_for_communities`, `clear_all_communities`, `create_leiden_community`, `set_node_community_membership`, `clear_all_temporal_digests`, `create_temporal_digest_node`, `get_all_node_ids_and_edges`, `store_node_positions`. |
| Pipeline → `QdrantService` | `find_node_ids_by_names`, `find_node_id_by_name`, `get_node_content_by_id`, `get_nodes_content_by_ids`, `upsert_node_cores`, `upsert_node_core`, `upsert_node_relationships`, `append_node_item`, `get_relationships_for_node_ids`, `delete_node`, `delete_community_relationships`, `scroll_all_isolated_contexts_with_dates`, `col_contexts`. Return-value conventions: upserts return `bool` (cores) or `None` (rels, failures only logged); lookups swallow errors and return empty. |
| Pipeline → `MeilisearchService` | `index_node`, `get_node`, `update_nodes_community`, `delete_node` — all swallow errors. |
| Pipeline → multimedia | `multimedia_service.extract_text_from_pdf(url, progress_cb)`, `describe_image`, `extract_text_from_docx`, `extract_text_from_spreadsheet`, `transcribe_audio`, `transcribe_video_audio`, `describe_video_visual`, `unload_local_models(family)`, `unload_marlin()`. |
| Pipeline → local models | `model_load_clock` snapshots (timing only). No explicit load/unload calls remain in `process_note`. |
| Retrieval ← pipeline | Retrieval ([16](16-retrieval-and-chat.md)) depends on: lowercase `name` payloads in cores, `parent_node_id` on contexts, `is_community_rel` flag, `edge_weight`/`confidence` on `SEMANTIC_REL`, `kind` values, Meili field names. Changing any of these here breaks search silently. |
| Frontend ← pipeline | `segmented-note-content.tsx` splits note bodies on `[Image: …]`, `[PDF Extraction (…)]:`, `[Audio Transcript (…)]:` and `[Video Transcript (…)]:`. The backend writes `[Video Audio Transcript (…)]:` / `[Video Visual Analysis (…)]:` / `[Word Extraction (…)]:` / `[Spreadsheet Extraction (…)]:`, which the frontend renders as plain text (marker mismatch; see [11](11-multimedia-enrichment.md)). |

## 13. Invariants and locked decisions

- **A note's Kuzu id equals its SQLite id**; entity ids are `node_<uuid4>`; never derive ids from names.
- **Qdrant `node_cores` is the source of truth for name → id.** Kuzu is a fallback only. Never mint an entity id without first seeding a cores stub — that is what the abort-on-stub-failure protects.
- **Names are stored lowercase and `#`-stripped** everywhere (Kuzu `name`, Qdrant `name`, Meili `name`, lock keys). Any new lookup must normalise the same way.
- **Documents are embedded without instruction; queries with.** Do not add a prefix to `embed_documents` calls.
- **Never resize/recreate Qdrant collections during ingest** (`_prepare_vector` fails closed). Dimension changes go through startup `sync_embedding_infrastructure`.
- **Meilisearch is written only after Qdrant succeeded** and always with the full document (replace semantics).
- **Free-form generation + JSON repair, not grammar-constrained sampling**, for extraction.
- **Long notes are chunked at paragraph boundaries and merged**; truncated chunk output is split, not repaired (repair only when a chunk is already ≤ 400 tokens).
- **One heavy model resident at a time**: multimodal phases are ordered Florence → (docs) → Whisper → Marlin → chat GGUF (image titling, extraction) precisely to minimise swaps; do not interleave LLM calls inside the Florence/Whisper phases.
- **The tracker is registered before the semaphore** so the idle timer cannot fire with queued work.
- **Community and digest jobs are cooperative and pre-emptible by ingestion**; they must check the cancel events between units of work.
- **`workflow` must be present in the agent state**; never fall back to the default-KB singleton.
- Autosave (`PUT`) and vault-watcher events must never trigger ingestion.

## 14. Failure modes and edge cases

| Situation | Behaviour |
|---|---|
| Qdrant down | `QdrantService._enabled=False`/`is_available()` False → `find_*` return empty (every entity looks new) → `upsert_node_cores` returns False → **abort** with the split-brain message. Nothing is written except the note node and (already-MERGEd) entity nodes/REFERENCES in Kuzu. |
| Meilisearch down | Logged warnings; ingestion succeeds; keyword search misses the note's entities until re-ingest. |
| Kuzu locked / query error | `execute_query` logs and re-raises → storage or summarization failure → note failed. |
| Embed model dims ≠ collections | `ValueError` from `_prepare_vector` → failure at the first upsert. |
| Local LLM returns empty content | `ValueError("Local LLM returned empty content (0 output tokens)…")` → retry after 30 s / 60 s → fail. |
| Prompt alone exceeds `n_ctx` | `LocalLlamaRuntime` raises ("split the input or raise ORB_LLAMA_N_CTX") — should not happen for chunks ≤ budget; can happen for the rename/title prompts on pathological content. |
| Extraction JSON truncated | split in half (≤ 3 levels) and merge; below 400 tokens repaired. |
| LLM returns zero nodes | Valid `Extraction` with empty lists → storage writes only the note node; summarization does nothing; note marked processed. |
| Entity name only in relationships (not in `nodes`) | Resolved via Qdrant if it exists from a previous note; otherwise the relationship is skipped (`no_source`/`no_target` counters). |
| Duplicate entity within one extraction | Collapsed to the first id in `_write_ontology`; contexts merged in `_update_neighborhoods`. |
| Attachment URL unresolvable / missing file | `FileNotFoundError` from `_download_temp_file` → collected → note failed. |
| Remote `http(s)` attachment to private/loopback host or > 512 MiB | Rejected (`_assert_public_http_url`, size cap) → note failed. |
| Note has no vault (`kb.vault_path` empty) | `_persist_note_body` raises → note failed at multimodal stage (only when enrichment changed content). |
| Second `/ingest` while the first is running | Both register with the tracker; second waits on the per-KB semaphore; both run fully (no coalescing) → duplicated contexts and `mention_count` increments. |
| Failure in one note of `reingest-all` / `reingest-vault` | Exception escapes `process_note` → remaining queued tasks of that request are skipped (Starlette semantics); those notes keep their `Queued…` stage. |
| Process restart mid-ingest | Notes stay at whatever stage string was last written, `processed=0`, `failed=0`; nothing resumes automatically. `reingest-all` picks them up. |
| Community recompute during ingest | `begin_ingestion` sets `cancel_recompute`; the run stops at the next cluster; partial hierarchy remains until the next idle rebuild. |

## 15. Gotchas (things an assistant would get wrong)

1. **Re-ingest does not clean up.** Stale entities, `REFERENCES`, `SEMANTIC_REL`s and contexts persist; `mention_count` inflates. Only note deletion (orphans) and `reset-ingestion-data` remove data.
2. **Isolated-context dedup is broken by the date suffix**: stored contexts are compared as `"{content} - {date}"` against raw new text, so re-ingesting appends duplicates whenever the note has a `created_at` (i.e. always via the HTTP triggers).
3. **Relationship scores are defaults**: the prompt never asks for `strength/confidence/relevance`, so `edge_weight` is effectively constant (5.6). Retrieval code that ranks on `edge_weight` is ranking on noise unless the model volunteers scores.
4. **Node-name normalisation differs between nodes (`lstrip("#")`) and relationship endpoints (no `#` strip)**.
5. **Entity `type` is frozen at first sighting in Qdrant/Meili** (`_core_content.type` wins) but overwritten in Kuzu on every ingest.
6. **No embedding-similarity entity resolution** and no `is_similarity` edges are produced today, despite the schema column and old reports mentioning "similarity detection".
7. **"Leiden" is agglomerative clustering**; L1 threshold is 0.35 (default arg), not the 0.50 in the comment.
8. **`COMMUNITY_DETECTION_ENABLED` and `TEMPORAL_DIGESTS_ENABLED` default to False** — in a default install nothing runs after ingestion. The community endpoint works regardless; the digest endpoint does not.
9. **`multimedia_concurrency_limit` and `_process_semaphore` read settings at import/construction** — changing env at runtime has no effect.
10. **Per-request sequencing**: `BackgroundTasks` run one after another; `INGESTION_PIPELINE_CONCURRENCY > 1` only helps across separate HTTP requests. And one failure aborts the rest of the request's queue.
11. **The tracker is global, workflows are per KB**: the community callback that eventually runs is the last-finished note's KB; other KBs' pending nodes are just counted.
12. **`storage_node` sets `created_at` to `now()` when the input has none** — only `POST /api/v1/ingest` without `created_at` hits this, and even then the endpoint fills it first; so contexts always carry a date.
13. **Enrichment markers vs frontend**: the frontend parser recognises `[Video Transcript …]:` but the backend writes `[Video Audio Transcript …]:` and `[Video Visual Analysis …]:`; Word/Spreadsheet blocks are not recognised either.
14. **`sentiment`, `type_reasoning`, `reasoning` are never persisted** — do not look for them in any store.
15. **`_update_note_title` does not rename the vault file**; only `PUT /notes/{id}` keeps title and filename in sync.
16. **`llm_service` is not necessarily what the pipeline uses** — `self._llm` may be a per-KB `LLMService`; patching the global singleton in tests/tools may not affect a KB with overrides.
17. **Status `processing` is also reported for never-ingested notes**; check `processing_stage`.
18. **Extraction chunk budget uses the real tokenizer only for local**; for cloud providers `ingestion_count_tokens` is an estimate and the 4000-token ceiling applies.
19. `_ENRICHMENT_BLOCK_RE` requires `\n\n` before the marker; a marker at the very start of a body or after a single newline is not stripped and would be re-extracted as note text.

## 16. Extension points / how to modify safely

| Goal | Touch | Keep in mind |
|---|---|---|
| Change the extraction schema (new node/rel field) | `schemas/extraction.py` (field + `None` handling), `_build_extraction_prompt` output format, `_write_ontology` / `_update_node_summary` to persist it, `merge_extractions` to merge it, `test_extraction_schemas.py` | Validators must tolerate `None` and missing keys; anything not persisted is lost. |
| Ask the LLM for relationship scores | Add `strength/confidence/relevance` to the prompt's relationship object; `_normalize_score` already handles labels/0–1 floats | `edge_weight` formula lives in `GraphService.create_or_update_relationship`. |
| Add a new attachment type | New handler in `MultimediaService`, classification list + `_run_phase` call in `multimodal_node` (choose the phase by which model it needs), new alternative in `_ENRICHMENT_BLOCK_RE`, frontend `MARKER_RE` | See [11](11-multimedia-enrichment.md). |
| Add a new agent node | `ingestion_agent.py`: function returning partial state, `workflow.add_node/add_edge`; write a stage string via `_wf._update_note_processing_status`; short-circuit on `state["errors"]` | The graph is compiled at import; no per-KB variation possible. |
| Change chunking policy | `extraction_chunking.py` constants (`_OUTPUT_TO_INPUT_RATIO`, `_DEFAULT_CHUNK_TOKENS`, `MIN_SPLIT_TOKENS`) or `ORB_EXTRACTION_CHUNK_TOKENS`; `_MAX_SPLIT_DEPTH` in the agent | Keep helpers pure; extend `test_extraction_chunking.py`. |
| Write to another store | Add to `KBContext`, pass into `IngestionWorkflow.__init__`, write in `_write_ontology`/`_update_node_summary` **after** Qdrant succeeds; add cleanup to `_delete_note_impl`, `reset_ingestion_data`, `clear_all_communities` | Also reset in `admin/reset-ingestion-data`. |
| Change entity resolution (fuzzy / embedding) | `_write_ontology` step 1 (`find_node_ids_by_names` + Kuzu fallback) and `_update_node_summary` step 1 | Both paths must agree or ids split; keep the stub-seeding invariant. |
| Make idle windows configurable | `COMMUNITY_IDLE_SECONDS` in `ingestion_tracker.py` is imported by `ingestion.py`; replace with a setting in both places | `get_status_snapshot` reports it. |
| Per-KB LLM | Already supported via `KBContext.llm` → `IngestionWorkflow(llm=…)`; new LLM calls must use `self._llm` / `_wf._llm`, never the global `llm_service` | Tests can inject a stub. |
| Resume after crash | Currently none; a startup hook would query `processed=0 AND failed=0 AND processing_stage LIKE 'Queued%' OR 'Starting%'` and re-queue | Ensure `require_ai(kb)` semantics. |

## 17. History / rationale

- **Mar 2026 — "Joint Approach"** (`559e988`, report in `Results/Results (Joint Approach)/JOINT_APPROACH_INGESTION_REPORT.md`): the HotPotQA benchmark pipeline on Neo4j + Elasticsearch + Gemini. "Joint" named the combination of (1) *iterative convergence-based refinement* — the extractor re-ran up to 10 passes until no new entities appeared — and (2) *graph-neighbourhood expansion with LLM relationship selection* at retrieval time. It also introduced the `failed` column, relationship-type sanitisation, and multi-type edges between the same pair. The iterative refiner no longer exists; the current single-pass-per-chunk extraction dates from the **"Final Implementation"** (`68494b7`, May 2026) which migrated to embedded **Kuzu** and Typesense (later Meilisearch), removed the `domain` field, and produced `FINAL_IMPLEMENTATION_INGESTION_REPORT.md`. The `is_similarity` column and `force_similarity_detection()` are remnants of the Joint-era post-ingestion similarity pass.
- **`09e35e3`** (May 2026): separate ingestion LLM clients (`INGESTION_*` keys, `ingestion_generate`, `ingestion_extract_structured`), "local" alias.
- **`682755f`** (May 2026): prepended `[Note date: …]` to the extraction input and made `_mark_note_processed` clear `failed`. The date prefix has since been removed; the flag reset remains.
- **`a8587e6`** (Jun 2026): `processing_stage` / `processing_model` columns and user-facing progress; `INGESTION_PIPELINE_CONCURRENCY`, `MULTIMEDIA_CONCURRENCY`; multimedia moved to HTTP sidecars (later folded back in-process, see [12](12-local-models-and-inference.md)).
- **`f8f527f`** (Aug 6 2026, audit): SSRF/size guards on attachment downloads; models kept resident across a batch instead of unloaded per note; `_update_note_fields` helper; blocking Kuzu/vault work moved to threads.
- **`8de5cda`** (Aug 7 2026): batching — stub cores in one upsert, relationship NL embedded and upserted in one batch each, `embed_batch` in llama-cpp, node type resolved from the already-fetched core content, merged-context vector computed in the same embed call as new contexts, community Meili refresh in one write with two bulk Qdrant fetches.
- **Uncommitted working tree (Sep 2026)**: chunked extraction (`extraction_chunking.py`, `_extract_chunk` split-on-truncation, `ingestion_generate_with_meta`, real token counting, no fixed local `max_tokens`), batched image titling after all multimodal phases (avoids evicting Florence per image), per-KB `LLMService` (`IngestionWorkflow(llm=…)`, `require_ai(kb)`), `[Timing]` log line with `model_load_clock`, and removal of the post-batch model unload in favour of the idle watcher.
