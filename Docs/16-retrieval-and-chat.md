# Retrieval and Chat

**What this covers.** The question-answering half of Orb: how a chat request enters the FastAPI backend (`POST /api/v1/chat` synchronously, or `POST /api/v1/chat/async` + `GET /api/v1/chat/status/{request_id}` polling), how conversations and messages are persisted in SQLite (`chat_conversations`, `chat_messages`), how follow-up questions are rewritten against history, and — in most depth — the multi-hop **iterative research loop** in `backend/app/services/retrieval.py` that combines LLM query analysis, Kuzu entity lookup, Meilisearch BM25, Qdrant vector search across three collections, 1-hop graph expansion, in-process GGUF cross-encoder reranking and a per-iteration LLM reasoning step to produce a final answer with note citations. It also documents the wire shapes the Next.js chat page consumes, every configuration key that influences retrieval, the log files to read when debugging, failure modes, and how to tune retrieval safely.

**Related docs:** [System architecture](02-system-architecture.md) · [Backend core & configuration](06-backend-core-and-configuration.md) · [API reference](07-api-reference.md) · [Knowledge bases & vaults](08-knowledge-bases-and-vaults.md) · [Ingestion pipeline](10-ingestion-pipeline.md) · [Local models & inference](12-local-models-and-inference.md) · [LLM providers & prompting](13-llm-providers-and-prompting.md) · [Graph storage (Kuzu)](14-graph-storage-kuzu.md) · [Search indexes (Qdrant/Meilisearch)](15-search-indexes-qdrant-meilisearch.md) · [Finance (Firefly)](17-finance-firefly.md) · [Frontend chat, graph & pages](20-frontend-chat-graph-and-pages.md) · [Configuration reference](21-configuration-reference.md) · [Logging & observability](23-logging-and-observability.md) · [Testing & benchmarks](24-testing-and-benchmarks.md) · [Decisions & constraints](26-decisions-and-constraints.md)

---

## 1. Responsibilities and boundaries

This slice **owns**:

- The HTTP surface for chat: `backend/app/api/chat.py` (conversation CRUD, sync chat, async chat job + status polling) and the chat export route that lives in `backend/app/api_desktop.py`.
- Conversation/message persistence: `backend/app/services/chat_store.py` over the SQLAlchemy models in `backend/app/models/chat.py` (tables `chat_conversations`, `chat_messages` in `DATA_DIR/orb.db`).
- The chat orchestration layer: `backend/app/workflows/chat.py` (`ChatWorkflow`) — follow-up rewrite, invoking the research loop, dedupe/truncate of evidence, reference extraction, final answer assembly.
- The retrieval engine: `backend/app/services/retrieval.py` (`RetrievalService`) — the iterative loop, per-iteration hybrid search (entity + BM25 + vector), graph expansion, candidate text formatting, reranker invocation.
- The reranker façade `backend/app/services/reranker.py` (`RerankerService`), which wraps the in-process GGUF cross-encoder `LocalGgufReranker` from `backend/app/services/local_models.py`.
- The client-side chat state machine (`frontend/src/lib/chat-context.tsx`) and the chat page renderer (`frontend/src/app/chat/page.tsx`), because their polling and citation-parsing behaviour is a contract with the backend.

This slice does **not** own (only consumes):

- LLM provider plumbing and prompt helpers in `backend/app/services/llm.py` — documented in [LLM providers & prompting](13-llm-providers-and-prompting.md). This doc covers only the contracts retrieval depends on (`analyze_query`, `iterative_step`, `rewrite_follow_up_query`, `_reason_step`, `get_chat_model`).
- Kuzu graph storage and Cypher (`backend/app/services/graph.py`) — see [Graph storage](14-graph-storage-kuzu.md). Only `find_nodes_by_name`, `find_name_variants_batch`, `get_related_nodes`, `get_linked_evidence`, `get_linked_evidence_by_node_ids` are described here, from the caller's perspective.
- Qdrant/Meilisearch index layout and write paths — see [Search indexes](15-search-indexes-qdrant-meilisearch.md). Only `search_all_collections`, `get_nodes_content_by_ids`, `get_relationships_for_node_ids`, `find_node_ids_by_names`, `search_nodes` are described.
- Model download/loading/residency (`local_models.py`) — see [Local models](12-local-models-and-inference.md); this doc describes the *consequences* of residency rules for a chat turn.
- The finance answer path (`firefly_service.answer_finance_question`) — see [Finance](17-finance-firefly.md); this doc covers only how chat routes into it and what shape comes back.
- Ingestion (how nodes/relationships/isolated contexts get written) — see [Ingestion pipeline](10-ingestion-pipeline.md).

## 2. Files

| Path | Purpose | Key exports |
|---|---|---|
| `backend/app/api/chat.py` | FastAPI router: conversations CRUD, `POST /api/v1/chat`, `POST /api/v1/chat/async`, `GET /api/v1/chat/status/{request_id}`; in-memory job registry and global job lock | `router`, `_chat_status: dict[str, dict]`, `_chat_job_lock: asyncio.Lock`, `_chat_tasks: set[asyncio.Task]`, `_answer_chat_query`, `_run_chat_job`, `_run_chat_job_serialized` |
| `backend/app/api_desktop.py` (chat export section) | `GET /api/v1/chat/conversations/{conversation_id}/export` (markdown/json) — **currently broken**, see §4.6 | `export_chat` |
| `backend/app/schemas/chat.py` | Pydantic wire schemas | `ChatTurn{role,content}`, `CreateConversationInput{title?}`, `ChatInput{query(min 1), request_id?, conversation_id?}` |
| `backend/app/models/chat.py` | SQLAlchemy ORM models | `ChatConversation`, `ChatMessage` |
| `backend/app/services/chat_store.py` | Async CRUD over the two tables, KB-scoped; history shaping; auto-title | `ChatStore`, `chat_store` singleton, `DEFAULT_TITLE = "New Chat"` |
| `backend/app/workflows/chat.py` | `ChatWorkflow`: rewrite → loop → dedupe/truncate → answer + references | `ChatWorkflow.chat`, `ChatWorkflow.retrieve_for_query`, `_doc_passage`, `_dedupe_docs`, `_truncate_context` |
| `backend/app/services/retrieval.py` | `RetrievalService`: iterative loop, hybrid search, graph expansion, rerank, text formatting | `RetrievalService`, `retrieval_service` (default-KB singleton; per-KB instances are built by `KBContext`) |
| `backend/app/services/reranker.py` | Async façade over the in-process GGUF reranker; normalises result dicts | `RerankerService.rerank`, `reranker_service`, `_normalize_results` |
| `backend/app/services/local_models.py` (reranker part) | `LocalGgufReranker` (Qwen3-Reranker yes/no logit scoring), residency rules | `local_gguf_reranker`, `reranker_gguf_path`, `_RERANK_SYSTEM`, `_RERANK_INSTRUCTION` |
| `backend/app/services/llm.py` (contracts used here) | Query analysis, iterative reasoning step, follow-up rewrite, thinking extraction, query-analysis cache | `analyze_query`, `iterative_step`, `rewrite_follow_up_query`, `_reason_step`, `_reason_step_sync`, `get_chat_model`, `_query_analysis_cache` |
| `backend/app/services/kb_registry.py` (`KBContext`) | Builds one `RetrievalService(graph, qdrant, meili)` + `ChatWorkflow(retrieval)` per KB lazily | `KBContext.get_chat_workflow`, `KBContext.get_retrieval_service` |
| `backend/app/services/ai_gate.py` | 503 gate when AI is not configured | `require_ai`, `ai_is_configured` |
| `backend/app/core/log.py` | Routes `RetrievalService`/`QdrantService`/`MeilisearchService`/`RerankerService` → `retrieval.log`; `ChatWorkflow`/`ChatStore` → `chat.log` | `COMPONENT_LOG_FILES` |
| `backend/tests/unit/test_chat_context.py` | Unit tests for `rewrite_follow_up_query` history shaping | — |
| `frontend/src/lib/chat-context.tsx` | React context: conversations, messages, `sendMessage` (async start + 1 s polling), optimistic UI | `ChatProvider`, `useChat`, `Message` |
| `frontend/src/app/chat/page.tsx` | Chat page: renders messages, stage/model bubble, `### References` → note-preview buttons, "Model thinking" dropdown, export button, entity highlighting | `ChatPage`, `AssistantMessageBody` |
| `frontend/src/lib/api.ts` / `frontend/src/lib/types.ts` | `startChat`, `getChatStatus`, `listChatConversations`, `getChatMessages`, `deleteChatConversation`, `exportChat`; `ChatStatus`, `ChatConversation`, `ChatMessageRecord` | — |
| `Results/**/*.md` | Historical HotPotQA benchmark reports for each pipeline generation (Sub-Questions → Looping → Joint → Final → After Optimizations) | — |

## 3. Architecture and flow

### 3.1 Request-level sequence (async path, the one the UI uses)

```mermaid
sequenceDiagram
    autonumber
    participant UI as chat-context.tsx
    participant API as api/chat.py
    participant CS as chat_store (SQLite)
    participant JOB as _run_chat_job_serialized (asyncio.Task)
    participant WF as ChatWorkflow
    participant RS as RetrievalService
    participant LLM as llm_service
    UI->>API: POST /api/v1/chat/async?kb=slug {query, request_id, conversation_id?}
    API->>API: require_ai() (503 if not configured)
    API->>CS: ensure_conversation(conversation_id, kb_id) → create if missing
    API->>CS: get_recent_history(conversation_id) (≤ CHAT_HISTORY_MAX_MESSAGES, excludes the new turn)
    API->>CS: add_message(user, query); maybe_set_title_from_first_message
    API->>API: _chat_status[request_id] = {stage:"Queued", done:false, conversation_id}
    API-->>UI: {request_id, conversation_id, stage:"Queued", done:false}
    API->>JOB: asyncio.create_task(...)
    JOB->>JOB: if _chat_job_lock.locked(): stage "Waiting for current chat to finish"
    JOB->>JOB: async with _chat_job_lock
    JOB->>API: stage "Starting chat request"
    JOB->>WF: _answer_chat_query → finance? : ChatWorkflow.chat(query, history, progress)
    WF->>LLM: rewrite_follow_up_query(history, query)  (sync, no stage emitted)
    WF->>RS: retrieve_with_self_correction(rewritten, top_k=50, progress, history)
    RS->>LLM: analyze_query(original) — stage "Analyzing question"
    loop MAX_LOOP_ITERATIONS (default 3)
        RS->>RS: hybrid_search(current_query) — stage "Searching knowledge base (i/N)"
        RS->>RS: _expand_relevant_neighbors — stage "Expanding graph neighbors"
        RS->>RS: rerank expansions — stage "Reranking graph neighbors"
        RS->>LLM: iterative_step(...) — stage "Reasoning over retrieved context (i/N)"
        LLM-->>RS: ANSWER → break, or NEXT_QUERY → continue
    end
    RS-->>WF: (final_answer | None, all_docs, thinking)
    WF->>WF: _dedupe_docs → _truncate_context(6) — stage "Selecting best evidence"
    WF->>CS: _extract_references (SQLite titles for linked note ids)
    WF-->>JOB: {query, rewritten_query, answer(+### References), context, thinking} — stage "Formatting answer"
    JOB->>CS: add_message(assistant, answer, thinking, metadata)
    JOB->>API: _chat_status[request_id] = {stage:"Complete", done:true, result, conversation_id}
    loop every 1000 ms (≤ 600 attempts)
        UI->>API: GET /api/v1/chat/status/{request_id}
        API-->>UI: {stage, model, done, conversation_id, result?, error?}
    end
    UI->>API: GET /api/v1/chat/conversations/{id}/messages (replace optimistic messages)
```

### 3.2 The research loop (inside `RetrievalService.retrieve_with_iterative_loop`)

```mermaid
flowchart TD
    A[Rewritten query] --> B[analyze_query original<br/>→ question_attribute for expansion rerank]
    B --> C{iteration < MAX_LOOP_ITERATIONS?}
    C -- no --> Z[EXHAUSTED: return last non-'Not found' FINDING<br/>thinking=None]
    C -- yes --> D{current_query set?}
    D -- "no (iteration 1)" --> H
    D -- yes --> E[hybrid_search current_query]
    E --> E1[analyze_query sub-query<br/>entities, keywords, concepts, attribute, types, date/period]
    E1 --> E2[embed_query enriched query]
    E2 --> E3[asyncio.gather<br/>entity branch Kuzu + Person name variants<br/>Meili BM25 per term<br/>Qdrant 3 collections w/ temporal filters]
    E3 --> E4[merge entity → BM25 → vector, dedup by lowercase name<br/>vector name variants]
    E4 --> E5[get_linked_evidence_by_node_ids → linked_notes]
    E5 --> E6[candidates: entity_match / vector_match / community_summary<br/>text = _build_node_text node, []]
    E6 --> E7[_apply_reranker_logging<br/>top_n=RERANKER_TOP_K, threshold=RERANKER_SCORE_THRESHOLD<br/>query + attribute + expected types]
    E7 --> F[_expand_relevant_neighbors<br/>1-hop Kuzu, NL from Qdrant node_relationships<br/>per-pair rerank → GRAPH_EXPAND_TOP_NEIGHBORS<br/>one graph_expansion doc per origin]
    F --> G[rerank expansion docs top_n=RERANKER_TOP_K]
    G --> G2[docs = selected + expanded<br/>accumulate into all_docs by name]
    G2 --> H[iterative_step<br/>ORIGINAL QUESTION + PRIOR FINDINGS + TRIED QUERIES + CURRENT SEARCH docs]
    H --> I{can_answer?}
    I -- ANSWER --> Y[return final_answer, all_docs, thinking]
    I -- NEXT_QUERY --> J[tried_queries += current_query<br/>current_query = next_query]
    I -- neither --> Z
    J --> C
```

## 4. Chat request lifecycle

### 4.1 Endpoints (summary)

All chat routes except `status` and `export` take the `kb` query parameter via `Depends(get_kb)` (`backend/app/api/deps.py`): `?kb=<name-or-slug>`, default `"default"`; unknown KB → **404** `"Knowledge base '<kb>' not found"`. Full request/response tables are in the [API reference](07-api-reference.md); this section explains behaviour.

| Method | Path | Gate | What it does |
|---|---|---|---|
| GET | `/api/v1/chat/conversations` | kb | `chat_store.list_conversations(kb_id)` — non-deleted, newest `updated_at` first, limit 50 |
| POST | `/api/v1/chat/conversations` | kb | `chat_store.create_conversation(kb_id, title)`; body `{title?}` optional |
| GET | `/api/v1/chat/conversations/{id}/messages` | kb | 404 if conversation missing/deleted/other KB; else all messages ascending by `created_at` |
| DELETE | `/api/v1/chat/conversations/{id}` | kb | Soft delete (`deleted_at = now`); 404 if no row matched; returns `{"status":"deleted","conversation_id"}` |
| POST | `/api/v1/chat` | kb + `require_ai()` | **Synchronous** full pipeline; returns the result dict when done |
| POST | `/api/v1/chat/async` | kb + `require_ai()` | Persists the user turn, registers a job, returns immediately |
| GET | `/api/v1/chat/status/{request_id}` | none | Reads `_chat_status[request_id]`; unknown id → `{"stage":"Waiting","model":null,"done":false}` |
| GET | `/api/v1/chat/conversations/{id}/export?format=markdown\|json` | none (desktop router) | Broken (500) — see §4.6 |

`require_ai(kb)` (`services/ai_gate.py`) raises **503** `{"error":"ai_not_configured","message":...}` unless AI is usable. Resolution order: if the KB pins its own `llm_provider` (per-KB LLM override, see §5.5), the gate passes iff that provider is configured (`provider_is_configured`: local → chat+embed GGUFs on disk; cloud → its API key set). Otherwise the gate asks whether anything is reachable at all: chat+embed GGUFs on disk, **or** any cloud provider key in the credential store, **or** a non-empty `LLM_BASE_URL`. `AI_SETUP_MODE` is not consulted.

### 4.2 Request id and conversation resolution

Both `/chat` and `/chat/async` do the same preamble:

1. `request_id = body.request_id or str(uuid.uuid4())`. The frontend always supplies one (`crypto.randomUUID()` or a `Date.now()-random` fallback) so it can start polling before the POST returns. **Nothing validates uniqueness**; a reused id overwrites the previous status entry.
2. `conversation = await chat_store.ensure_conversation(body.conversation_id, kb.kb_id)` — if `conversation_id` is given and exists **in this KB and is not soft-deleted**, it is reused; otherwise a **new** conversation is silently created (no 404). A stale/foreign conversation id therefore forks a new thread rather than erroring.
3. `history = await chat_store.get_recent_history(conversation_id)` — fetched **before** the new user message is inserted, so history never contains the current question.
4. `await chat_store.add_message(conversation_id, "user", body.query)` then `maybe_set_title_from_first_message(conversation_id, body.query)`.

### 4.3 Synchronous `POST /api/v1/chat`

- Progress callback `_progress(stage, model)` writes `_chat_status[request_id] = {"stage": stage, "model": model}` — note: **no `done`, no `conversation_id`** keys on this path, so a client polling the sync request's id would see `done` falsy forever.
- Runs `_answer_chat_query(...)` inline on the event loop, **without** taking `_chat_job_lock`. A sync chat can therefore run concurrently with an async job and both will contend for the single in-process llama runtime (model swap thrash, see §12).
- On success: persists the assistant message (with `thinking` and `metadata={"rewritten_query","context_count"}`), sets stage `"Complete"`, and returns the workflow result dict augmented with `request_id`, `conversation_id`, `assistant_message_id`.
- On any exception: sets stage `"Failed"` and re-raises → FastAPI **500**. The user message already persisted remains in the conversation (dangling turn).

### 4.4 Asynchronous `POST /api/v1/chat/async` + polling

**Job registry.** `_chat_status: dict[str, dict]` is a module-level dict keyed by `request_id`. Entries are written by `start_chat`, `_run_chat_job_serialized` and `_run_chat_job`, read by `get_chat_status`. **Entries are never evicted**; the final `"Complete"` entry embeds the whole `result` (including `context` docs with `original_obj` payloads), so memory grows with every chat in the process lifetime.

**Task lifetime.** `start_chat` does `task = asyncio.create_task(_run_chat_job_serialized(...))`, adds it to `_chat_tasks` (a strong-reference set so the task is not garbage-collected mid-flight) and removes it via `add_done_callback(_chat_tasks.discard)`. The job runs on the **app event loop** — the same loop as `AsyncSessionLocal` — which is why it is a task and not a thread (the docstring: "Run chat on the app event loop (same loop as AsyncSessionLocal)"). Commit `4408a72` originally ran jobs in a threadpool; this was changed when SQLite/aiosqlite sessions needed to stay on one loop.

**Serialisation.** `_chat_job_lock = asyncio.Lock()` is process-global: **at most one async chat job executes at a time across all KBs.** If the lock is already held when a job starts, the job first publishes stage `"Waiting for current chat to finish"` and then awaits the lock. There is no queue position, no fairness guarantee beyond `asyncio.Lock`'s FIFO wakeups, and no timeout.

**Status shape written by the job** (`_run_chat_job._progress` merges into the existing entry):

```json
{"stage": "<string>", "model": "<string|null>", "done": false, "conversation_id": "<uuid>"}
```

Terminal states:

```json
{"stage": "Complete", "model": null, "done": true, "conversation_id": "...", "result": { ...chat result... }}
{"stage": "Failed",   "model": null, "done": true, "conversation_id": "...", "error": "<str(exc) or exception class name>"}
```

`get_chat_status` returns `{"request_id": ..., **entry}`. The frontend treats `done && error` as failure and `done && !error` as success, reading only `result.answer`, `result.thinking`, `result.conversation_id`.

**Cancellation and timeouts.** There are none on the backend: no cancel endpoint, no per-stage timeout, no deadline on the LLM calls. The frontend gives up after `POLL_MAX_ATTEMPTS = 600` polls (~10 minutes) or 8 consecutive polling errors, but the backend job keeps running to completion and will still persist the assistant message. Reloading the page mid-job likewise leaves the job running; the answer shows up in the conversation once `getChatMessages` is refetched.

### 4.5 Stage strings, in emission order

The progress callback signature is `(stage: str, model: str | None) -> None`. Stages are free-form strings displayed verbatim by the UI. In order for a non-finance async request:

| # | Stage | `model` | Emitted by |
|---|---|---|---|
| 1 | `Queued` | null | `start_chat` |
| 2 | `Waiting for current chat to finish` | null | `_run_chat_job_serialized` (only if lock held) |
| 3 | `Starting chat request` | null | `_run_chat_job` (the follow-up rewrite LLM call happens under this stage — no stage of its own) |
| 3a | `Checking finance data and notes` | – | `_answer_chat_query` (finance queries only) |
| 4 | `Planning retrieval` | `"Gemma4"` | `ChatWorkflow._retrieve_context` |
| 5 | `Analyzing question` | `"Gemma4"` | `retrieve_with_iterative_loop` |
| 6 | `Retrieval loop {i}/{N}` | null | loop, each iteration |
| 7 | `Searching knowledge base ({i}/{N})` | `"Embeddings + Reranker"` | loop, iterations ≥ 2 (iteration 1 has no query yet) |
| 8 | `Expanding graph neighbors` | null | loop, iterations ≥ 2 |
| 9 | `Reranking graph neighbors` | `settings.MODEL_RERANKER_LOCAL` | loop, only if expansion produced docs |
| 10 | `Reasoning over retrieved context ({i}/{N})` | `"Gemma4"` | loop, each iteration |
| 11 | `Synthesizing final answer` | `"Gemma4"` | loop, **only on exhaustion** (label only — no extra LLM call is made) |
| 12 | `Selecting best evidence` | null | `ChatWorkflow._retrieve_context` |
| 13 | `Formatting answer` | null | `ChatWorkflow.chat` (not on the finance path) |
| 14 | `Complete` / `Failed` | null | `_run_chat_job` |

The `"Gemma4"` model label is a **hard-coded string** in `chat.py`/`retrieval.py`; it does not reflect `get_chat_model()` (cloud providers or a different local GGUF still show "Gemma4"). The chat page footer separately hard-codes "Gemma3 4B".

### 4.6 Result shape and the export endpoint

`ChatWorkflow.chat` returns:

```json
{
  "query": "<original user text>",
  "rewritten_query": "<standalone query or original>",
  "answer": "<final answer>\n\n### References\n- [Title](/notes/<note_id>)\n- ...",
  "context": [ { ...candidate doc dict, see §10.1... }, ... ],   // ≤ 6 docs
  "thinking": "<model chain-of-thought or null>"
}
```

The API layer adds `request_id`, `conversation_id`, `assistant_message_id`. The finance path (`firefly_service.answer_finance_question`) returns the same keys plus `information_needs: [query]`, `discovered_entities: {}`, and appends `{"source":"finance","summary":...,"kb_id":...}` to `context`; `thinking` is copied from the note-retrieval pass if present.

**Export.** `GET /api/v1/chat/conversations/{conversation_id}/export?format=markdown|json` in `backend/app/api_desktop.py` calls `chat_store.get_messages(db, conversation_id)`, **a method that does not exist** (`ChatStore` exposes `list_messages(conversation_id, kb_id=None)`), so every call raises `AttributeError` → 500. It also ignores `kb` and does no ownership check. The chat page's "Export" button calls `api.exportChat(activeConversationId, "markdown")`, expects a `text/markdown` body, builds a Blob download named `chat-<first 8 chars>.md`, and **swallows the error silently** (`catch { /* ignore */ }`) — so the button appears to do nothing. Intended output: JSON `[{"role","content","created_at"}]` or markdown `## <role>\n\n<content>\n` blocks joined by blank lines.

## 5. Conversation persistence (`chat_store.py`, `models/chat.py`)

### 5.1 Tables

Both tables live in the main SQLite database (`DATA_DIR/orb.db`, engine from `backend/app/core/database.py`, `AsyncSessionLocal` sessions). There is **no KB foreign key** — `kb_id` is a plain indexed string matching `KBContext.kb_id`.

`chat_conversations` (`ChatConversation`):

| Column | Type | Notes |
|---|---|---|
| `id` | String PK | `uuid4()` string |
| `kb_id` | String, indexed, not null | scope; every read filters on it |
| `title` | String, not null | default `"New Chat"` (`DEFAULT_TITLE`) |
| `created_at` | DateTime(tz) | UTC |
| `updated_at` | DateTime(tz) | UTC; bumped by `add_message` and by auto-title |
| `deleted_at` | DateTime(tz), nullable | **soft delete marker** |

`chat_messages` (`ChatMessage`):

| Column | Type | Notes |
|---|---|---|
| `id` | String PK | `uuid4()` |
| `conversation_id` | String FK → `chat_conversations.id` (ON DELETE CASCADE), indexed | |
| `role` | String | `"user"` or `"assistant"` (not enforced by a CHECK) |
| `content` | Text | the answer **including** the appended `### References` block |
| `thinking` | Text, nullable | model chain-of-thought captured from the terminating loop step |
| `metadata` (attr `metadata_json`) | JSON, nullable | `{"rewritten_query": str|null, "context_count": int}` for assistant turns; null for user turns |
| `created_at` | DateTime(tz) | ordering key everywhere |

ORM relationship `ChatConversation.messages` is `cascade="all, delete-orphan"`, ordered by `ChatMessage.created_at`. Nothing in the app loads it (the store queries messages directly), but it makes `hard_delete_conversation` semantics consistent.

### 5.2 `ChatStore` API

All methods are `async`, open their own `AsyncSessionLocal()` session, and return plain dicts (`_conversation_to_dict`: `id, kb_id, title, created_at, updated_at` ISO strings; `_message_to_dict`: `id, conversation_id, role, content, thinking, created_at` — **`metadata` is never returned to clients**).

| Method | Behaviour |
|---|---|
| `list_conversations(kb_id, limit=50)` | `kb_id` match AND `deleted_at IS NULL`, ordered `updated_at DESC`, limit 50 (the API does not expose `limit`) |
| `create_conversation(kb_id, title=None)` | Inserts with `title or "New Chat"`, `created_at = updated_at = now` |
| `get_conversation(id, kb_id=None)` | `deleted_at IS NULL`; optional KB match; `None` if missing |
| `delete_conversation(id, kb_id=None)` | `UPDATE ... SET deleted_at = now` (no `deleted_at IS NULL` guard, so re-deleting just refreshes the timestamp and still returns `True`); returns `rowcount > 0` |
| `hard_delete_conversation(id, kb_id=None)` | Deletes messages then the conversation row. **No endpoint calls it** — retained for maintenance/tests |
| `list_messages(id, kb_id=None)` | If `kb_id` given, first checks ownership + not deleted (returns `[]` otherwise); then all messages ascending |
| `add_message(conversation_id, role, content, thinking=None, metadata=None)` | Inserts and bumps the conversation's `updated_at` in the same transaction; returns the message dict |
| `get_recent_history(conversation_id, limit=None)` | Last `limit or settings.CHAT_HISTORY_MAX_MESSAGES` (24) messages by `created_at DESC`, reversed to chronological, filtered to `role in {user, assistant}` with non-empty content, returned as `ChatTurn` objects |
| `ensure_conversation(conversation_id, kb_id)` | Reuse if found in this KB and not deleted, else create |
| `maybe_set_title_from_first_message(conversation_id, user_text)` | See §5.3 |

Soft-deleted conversations keep their messages; they are invisible to list/get/messages but their `id` can still be passed to `/chat` — `ensure_conversation` then silently creates a **new** conversation (it does not resurrect the deleted one).

### 5.3 Title generation

Titles are **not** LLM-generated (`llm_service.generate_title` exists but is used for notes, not chats). `maybe_set_title_from_first_message` runs on every chat turn and is a no-op unless the current title is exactly `"New Chat"`, `""` or `NULL`. It collapses whitespace in the user text; if longer than 72 characters it keeps the first 69 characters (right-stripped) plus `"..."`; sets `title` and `updated_at`. Because the check is title-based rather than "first message"-based, a conversation created with an explicit title via `POST /conversations {title}` is never retitled, and a conversation whose title was reset to "New Chat" would be retitled by the next message.

### 5.4 Follow-up context: how history is built and trimmed

History flows through two independent consumers, each with its own truncation:

1. **Follow-up query rewrite** (`LLMService.rewrite_follow_up_query(history, latest_query)`, called synchronously from `ChatWorkflow._retrieve_context` before any stage is emitted):
   - Returns `latest` unchanged when the query is blank/whitespace or history is empty (no LLM call).
   - Takes `history[-CHAT_HISTORY_MAX_MESSAGES:]`, keeps `user`/`assistant` turns with content, truncates each turn to 600 chars (`[:597] + "..."`), renders `User: ...` / `Assistant: ...` lines.
   - Prompt: "You rewrite follow-up questions into standalone search queries for a document collection. CONVERSATION: ... LATEST USER MESSAGE: ... Return ONE standalone search query ... Reply with only the rewritten query." System prompt: "You are a precise query rewriter. Output only the rewritten query."
   - Uses `_reason_step_sync` (OpenAI-compatible `chat.completions.create` on the chat client — **it has no Gemini/Anthropic branch**, so on those providers `self.chat_client.chat.completions` will fail and the fallback below applies).
   - Accepts the result only if non-empty and ≤ 300 chars after stripping quotes; otherwise (or on any exception, logged as a warning) returns the original query. `backend/tests/unit/test_chat_context.py` pins these behaviours (no history → unchanged; LLM failure → unchanged; invalid roles/empty content ignored; 800-char turn truncated with `...`).
   - The rewritten query is what the **entire** retrieval loop sees as `ORIGINAL QUESTION`; the raw user text is only stored and echoed back as `query`.

2. **Loop conversation context** (`retrieve_with_iterative_loop`): the same history (as `[{role, content}]`) is re-sliced to the last `CHAT_HISTORY_MAX_MESSAGES`, each turn truncated to 500 chars (`[:497] + "..."`), and rendered as a `CONVERSATION SO FAR:` block that is prepended to **every** `iterative_step` prompt. This lets the reasoning step answer "what did you just say?" style follow-ups directly from history even if retrieval finds nothing new.

There is no token counting for history; `CHAT_HISTORY_MAX_MESSAGES` (24 messages ≈ 12 turns) × 500–600 chars is the only bound. With long assistant answers (they include the References block), 24 × 600 chars ≈ 14 KB can be prepended to the rewrite prompt.

### 5.5 Per-KB LLM override (uncommitted working-tree behaviour)

The working tree adds a per-KB chat/ingestion model override that changes how chat picks its LLM:

- `knowledge_bases` gains `llm_provider`, `llm_model`, `llm_ingestion_model` columns (`models/kb.py`, and the raw-SQL registry in `kb_registry.py` adds them via `_ensure_optional_columns`). `GET/PATCH /api/v1/kb/{kb_id}/llm` read/update them; `GET /api/v1/kb` rows include `effective_llm {provider, model, ingestion_model, inherited}`. Allowed providers: `local, openai, gemini, anthropic, huggingface` (`LLM_PROVIDERS`); `ollama`/`lm_studio` are mapped to `local`. Local model ids must be catalog chat models already downloaded (`model_catalog.chat_model_downloaded`), cloud providers must have their key in `.env`; the PATCH constructs the service eagerly and rolls the override back with a 400 if construction fails.
- `KBContext.llm` returns the global `llm_service` when nothing is pinned, otherwise a cached `LLMService(provider, chat_model=..., ingestion_model=..., ingestion_provider=provider)` rebuilt whenever the override tuple changes. `KBContext._ensure_lazy()` passes that instance as `llm=` to `RetrievalService`, `IngestionWorkflow` and `ChatWorkflow`; `apply_llm_override` drops all three cached services so the next request rebuilds them.
- `RetrievalService._llm` (property) returns the override or lazily imports the global `llm_service`; `hybrid_search` and `retrieve_with_iterative_loop` call `self._llm.analyze_query` / `self._llm.iterative_step`. `ChatWorkflow._llm` is used for `rewrite_follow_up_query`. **Embedding and reranking remain system-wide** (embedding dims are shared across every KB's Qdrant collections — a deliberate constraint stated in `kb_registry.py`).
- `LLMService.get_chat_model()` precedence is now: instance `_chat_model_override` → `settings.CHAT_MODEL` → provider-specific key (`LLM_MODEL` for local, `OPENAI_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`, `HUGGINGFACE_MODEL`).
- For local per-KB models, `LocalLlamaRuntime.create_chat_completion(model=...)` calls `resolve_chat_gguf(model)` (catalog id or `.gguf` path → on-disk file; unknown id → Setup selection with a warning; known-but-not-downloaded → `RuntimeError`) and `ensure_chat_loaded(path)` **swaps the resident chat GGUF** when a different KB's model is requested. Chatting alternately in two KBs pinned to different local models therefore costs a full model reload per switch.
- `require_ai(kb)`: a KB with a pinned provider is checked against that provider alone (see §4.1).

## 6. The research loop, step by step (`retrieve_with_iterative_loop`)

`ChatWorkflow._retrieve_context` calls `retrieval.retrieve_with_self_correction(rewritten_query, top_k=50, progress_callback, conversation_history)`, which is a pure alias for `retrieve_with_iterative_loop(...)` (kept for the older name used in the Looping-approach reports). Return type: `tuple[str | None, list[dict], str | None]` = `(final_answer, all_docs, thinking)`. The `top_k` parameter is **unused** (pylint-suppressed) — `hybrid_search` is called with its default and never slices by `top_k`.

### 6.1 Setup

1. Build `conversation_context` from history (§5.4 item 2).
2. `_progress("Analyzing question", "Gemma4")`; `_loop_qa = llm.analyze_query(query)`; keep only `question_attribute` (`_loop_question_attr`) for reranking graph-expansion docs later. `hybrid_search` re-analyses each sub-query itself; the `analyze_query` memo (`_query_analysis_cache`, 64 entries, key `(query, today)`) dedupes identical strings within a day, which matters because a cache miss on local mode forces a chat-GGUF load.
3. State: `accumulated_steps: list[{query, full_answer, reasoning}]`, `all_docs: list[dict]`, `surfaced_names: set[str]`, `current_query = None`, `tried_queries: list[str]`.

### 6.2 Each iteration (`for iteration in range(settings.MAX_LOOP_ITERATIONS)`, default 3)

1. `_progress(f"Retrieval loop {i}/{N}")`.
2. **Retrieval, skipped on iteration 1** because `current_query is None` — the first LLM call only *plans* the first query. Consequently with `MAX_LOOP_ITERATIONS = 3` there are at most **two** retrievals per chat turn (iterations 2 and 3), and the third iteration's LLM step is the last chance to say `ANSWER`.
   - `selected_docs = await self.hybrid_search(current_query)` (§7–§8); add each doc's `original_obj.name` to `surfaced_names`.
   - `expanded = await self._expand_relevant_neighbors(selected_docs, current_query, surfaced_names)` (§8.4) inside a try/except (failure → warning, `expanded = []`). If non-empty: `_progress("Reranking graph neighbors", MODEL_RERANKER_LOCAL)` and `expanded = _apply_reranker_logging(current_query, expanded, top_n=RERANKER_TOP_K, question_attribute=_loop_question_attr)` (no score threshold on this pass). Names of expansion docs' `original_obj` (the *origin* node, not the neighbours) are added to `surfaced_names`.
   - `docs = selected_docs + expanded`; docs are merged into `all_docs` **deduplicated by `original_obj.name`** (fallback `d["name"]`). Because a `graph_expansion` doc's `original_obj` is the origin node that was just added from `selected_docs`, **expansion docs are effectively never added to `all_docs`** — they are visible to the LLM for this iteration only and never reach the final `context`/References (see Gotchas §16).
3. `_progress(f"Reasoning over retrieved context ({i}/{N})", "Gemma4")`; `result = await llm.iterative_step(original_question=query, accumulated_steps, search_query=current_query, docs, tried_queries or None, conversation_context or None)` (§9.2).
4. If `docs` is non-empty and the step produced `full_answer` or `reasoning`, append `{query: current_query, full_answer, reasoning}` to `accumulated_steps` (iteration 1 never records a step).
5. **Termination A — answer:** if `result["can_answer"]`, return `(result["final_answer"], all_docs, result.get("thinking"))`. Only the *terminating* step's thinking is returned.
6. Otherwise add `current_query` to `tried_queries`; `next_q = result.get("next_query")`.
7. **Termination B — no next query:** if `next_q` is falsy (LLM output neither ANSWER nor NEXT_QUERY, or the LLM call failed and returned the empty dict), log a warning and `break` to exhaustion handling. Nothing detects a *repeated* query; `tried_queries` is only surfaced to the LLM as "do NOT repeat these".
8. `current_query = next_q`; loop.

### 6.3 Termination C — exhaustion

After the loop (either `range` exhausted or `break`): `_progress("Synthesizing final answer", "Gemma4")` — a label only; **no LLM call is made**. The code walks `accumulated_steps` in reverse and returns the first `full_answer` (the `FINDING:` text) whose lowercase value is not in `{"not found", "none", "insufficient", "n/a", "unknown"}`; else `None`. Returned tuple is `(last_answer_or_None, all_docs, None)` — thinking is always `None` on exhaustion. Log line `[IterLoop] EXHAUSTED in Xs ... best_finding=...`.

`ChatWorkflow.chat` then decides the user-facing text: the loop's answer if truthy; else `"I couldn't find any relevant information in the knowledge base to answer that."` when `unique_docs` is empty, or `"I couldn't find enough information to answer that."` when there were docs but no finding. References are appended in all three cases if any doc has `linked_notes`.

### 6.4 What "potential questions" / MAX_POTENTIAL_QUESTIONS mean today

`settings.MAX_POTENTIAL_QUESTIONS` (default 10) is defined in `core/config.py` and listed in `backend/.env.example`, but **no code reads it**. It is a leftover of the Sub-Questions approach (decompose into 2–4 sub-questions) that preceded the loop (see §18). Likewise the docstring of `retrieve_with_self_correction` ("primary structured sub-question pipeline") and the exhaustion comment mentioning `final_synthesis_from_sub_results` refer to functions that no longer exist.

## 7. Per-iteration hybrid channels (`hybrid_search`)

`hybrid_search(query, top_k=50) -> list[dict]` is called once per retrieval iteration with the loop's `current_query`. It returns **already reranked and thresholded** candidate docs. `top_k` is only logged.

### 7.1 Query analysis (LLM, structured)

`self._llm.analyze_query(query)` → `LLMService.analyze_query` builds a `QueryAnalysis` Pydantic schema and calls `extract_structured(prompt, QueryAnalysis, temperature=0)` (the same structured-output machinery ingestion uses, see [LLM providers](13-llm-providers-and-prompting.md)). Fields and how retrieval uses them:

| Field | Type | Used for |
|---|---|---|
| `intent` | `search\|summarize\|compare\|explain\|list\|recent\|verify` | logged only |
| `entities` | `list[str]` | **sole source of entity names** for the Kuzu entity branch (no regex/TitleCase fallback — the code that once filtered stopwords/short tokens is now empty lists) |
| `concepts` | `list[str]` | appended to the embedding query; extra Meili terms |
| `keywords` | `list[str]` | extra Meili terms |
| `expected_entity_types` | `list[str]` (lowercased) | appended to the reranker query as `type: a, b` |
| `question_attribute` | `str|null` | appended to the embedding query and the reranker query |
| `date_filter` | `YYYY-MM-DD|null` | Qdrant day filter (§7.4) |
| `period_filter` | `YYYY-MM|null` | Qdrant month filter (§7.4); mutually exclusive with `date_filter` |

The prompt embeds `Today's date: {ISO today}` so the model resolves relative dates ("yesterday", "last month") itself — **there is no `dateparser` in retrieval** (`dateparser` is only used by `api/notes.py` for note date fields). The prompt's examples use the key `expected_entity_types` while its bullet list says `entity_types`; the schema field is `expected_entity_types`. On any exception (`extract_structured` returned `None` → `ValueError("Empty extraction result")`, provider error, JSON failure) the method logs `Query analysis failed` and returns safe defaults: `intent="search"`, `entities=[]`, `keywords=query.split()`, everything else empty/None — i.e. **entity lookup is skipped and BM25 runs on each whitespace token** of the query. Only successful analyses are cached (`_query_analysis_cache`, FIFO 64, key `(query, today)`).

The **enriched query** used for embedding is `f"{query} {question_attribute} {concepts...}"` (only non-empty parts). The reranker query is built separately (§8.3).

### 7.2 Embedding

`embedding_service.embed_query(enriched_query)` (`services/embedding.py`) → `LocalLlamaEmbeddings.embed_query` → `local_llama_runtime.embed(text)`. For Qwen3 embedding models (`is_qwen3` = `"qwen3"` in the basename of `EMBEDDING_MODEL`) the text is prefixed with `"Instruct: Given a question, retrieve relevant context.\nQuery: "`. This call forces the **embed GGUF** to be resident (unloading chat/reranker; §10). Timing is logged as `[⏱️ Timing] Total query embedding: Xs`.

### 7.3 The three branches run concurrently

```python
_entity_res, _meili_res, _vector_res = await asyncio.gather(
    asyncio.to_thread(_entity_branch),   # Kuzu (sync, thread)
    _meili_branch(),                     # Meilisearch (async gather of per-term to_thread calls)
    _vector_branch(),                    # Qdrant (sync client inside async wrapper)
    return_exceptions=True,
)
```

Each branch's exception is caught and replaced with `[]` plus a warning (`[Entity] Name matching failed`, `[Keyword] Meili BM25 search failed`, `[Vector] Vector search failed`). Timing: `[⏱️ Timing] Parallel entity/BM25/vector search: Xs`. Introduced by commit `8de5cda` (previously sequential).

#### Entity branch (`_entity_branch`, Kuzu)

1. Skip entirely if `query_entities` is empty.
2. Lowercase/strip each entity; `graph.find_nodes_by_name(names, fuzzy=True)`. The fuzzy Cypher is `UNWIND $names AS q MATCH (n:Node) WHERE n.kind IN ['indexable','note'] AND toLower(n.name) CONTAINS q RETURN DISTINCT n.id AS node_id, n.name, [n.kind] AS labels, n.type AS entity_type, q AS matched_query LIMIT 50`. **`CONTAINS` is substring match**, so `"wood"` matches `ed wood`, `hollywood`, `woodstock`; and the same node can appear once per matching term (different `matched_query`), which is why the branch later dedupes by `node_id`. The exact-match variant (`fuzzy=False`, `toLower(n.name) IN $names`) exists but retrieval never uses it. Note nodes (`kind='note'`) are matched too — a note titled like the entity becomes a candidate.
3. Mark each hit `_source="entity_match"`.
4. **Name-variant expansion (Person only):** for found nodes whose `entity_type` is `person` (case-insensitive) and whose name has ≥ 2 words, one batched `graph.find_name_variants_batch(names)` (UNWIND; `toLower(n.name) STARTS WITH base + ' '` OR (`CONTAINS base` AND longer by > 2 chars); excludes Sr./Jr./roman-numeral suffix mismatches; ≤ 5 per base). Variants not already present get `_source="name_variant"`, `_variant_of=<base>`.
5. Dedupe by `node_id` (fallback: lowercase name when `node_id` missing).
6. **Enrich from Qdrant:** `qdrant.get_nodes_content_by_ids(ids)` — one `retrieve` on `node_cores` (point id = `uuid5(NAMESPACE_OID, node_id)`) plus one filtered `scroll` on `node_isolated_contexts` (`parent_node_id IN ids`), returning `{node_id: {name, type, description, community_level, isolated_contexts: ["<content> - <note_created_at>", ...]}}`. The branch sets `description` and `summary` to the core `description` and `isolated_contexts` to the list. Kuzu holds **no text** (only `id, kind, name, type, pos_*`), so a node with no Qdrant core and no contexts stays text-less and is dropped later by `_get_node_text`.

#### Keyword branch (`_meili_branch`, Meilisearch)

- Terms: `[query] + [t for t in keywords + concepts if t.lower() not in query.lower()]`.
- Each term → `_search_meili_by_keyword(term)` → `asyncio.to_thread(meili.search_nodes, term, 100)`. `search_nodes` returns hits with `score = total_hits - rank_index` (a **rank-derived pseudo-score**, not a BM25 score; Meili's own ranking score is not requested) and `payload = the indexed document` (`node_id, name, type, description, ...`).
- Per term, dedupe by lowercase name keeping the higher pseudo-score; across terms, first-seen wins. Result rows: `{id: node_id, name, entity_type: type, summary: description, score, _source: "meili"}` — note the key is `id`, **not `node_id`**, which matters for later `node_id`-keyed lookups (they fall back to `id`).

#### Vector branch (`_vector_branch` → `_search_qdrant_multi_collection`)

See §7.4 — searches `node_cores`, `node_relationships`, `node_isolated_contexts` concurrently (one thread per collection inside `QdrantService.search_all_collections`) with `limit=500` per collection and `score_threshold` = `VECTOR_PRE_RERANK_THRESHOLD` (0.45) when `RERANKER_ENABLED` else `VECTOR_SIMILARITY_THRESHOLD` (0.50). The comment in code is explicit: the 500 ceiling exists only because the Qdrant API requires a limit; "the score_threshold is the only real filter".

### 7.4 Vector search details and temporal handling

**Filters (from `analyze_query`):**

| Case | `node_isolated_contexts` filter | `node_cores` filter | Collections searched |
|---|---|---|---|
| `date_filter=YYYY-MM-DD` | `note_created_at == date` | – | **only** `node_isolated_contexts` (`day_only=True`) — cores and relationships are skipped so month-level summaries don't leak into a single-day question |
| `period_filter=YYYY-MM` | `note_created_at IN [every date of that month]` (`calendar.monthrange`) | `period_key == YYYY-MM` | all three |
| neither | none | none | all three |

Consequence of the month case: the `node_cores` filter `period_key == YYYY-MM` matches **only** community/temporal-digest cores that carry a `period_key`; ordinary entity cores (no `period_key`) are excluded from the cores search that iteration, so month questions rely on isolated contexts + relationships + digests/communities. An invalid `period_filter` string (not `YYYY-MM`) logs a warning and applies no filter.

**Hit normalisation and per-name merge** (`merged: dict[lower_name, node]`):

- `node_id` = payload `node_id` / `parent_node_id` / `source_node_id` / `target_node_id` (first present). `name` = payload `name`; when absent, resolved in one batched `get_nodes_content_by_ids` call; hits that still lack a name are skipped.
- **Isolated-context hits** (`"isolated_context" in collection`): text = `payload.content` + `" - " + payload.note_created_at` when present. They are *accumulated*: appended to the existing merged entry's `summary` if not already contained, best score kept. New entries: `{id, node_id, name, entity_type: payload.type or "", summary: ctx_text, score, _source: "qdrant"}`. Rationale in code: nodes with an empty core description would otherwise end up text-less and be silently dropped by `_get_node_text`.
- **Relationship hits** (`"relationships" in collection`): summary = `payload.natural_language`; `entity_type` forced to `""`.
- **Core hits**: summary = `description` / `content` / `natural_language`.
- For non-context hits, a lower-scored hit for an already-merged name only back-fills a missing `summary`; a higher-scored one replaces the entry but inherits the previous summary if it has none itself.
- Final back-fill: entries lacking `entity_type` get it from `node_cores.type` via one more batched fetch (isolated-context payloads only carry `parent_node_id`/`content`/`note_created_at`).

Log lines: `[Qdrant] Raw hits from search_all_collections: N (threshold=…, reranker=on|off)` and `[Qdrant] N unique nodes after dedup (from M raw hits)`.

**Bi-temporal filtering** (`valid_from`/`valid_to`/`is_active` on relationships, commit `033589d`) **no longer exists**: the current Kuzu `SEMANTIC_REL` schema has `ingested_at`, `last_updated`, `created_at`, `is_similarity` but no validity interval, and retrieval applies no temporal predicate to graph edges. Temporal awareness today = the two Qdrant payload filters above + the `" - <date>"` suffix on isolated-context text that the LLM can read.

**Temporal digests and communities** are ordinary `node_cores` points (type `community` or digest types, with `period_key`) and surface through the vector branch like any node; there is no separate community lookup ("STEP 3" comment in code). `COMMUNITY_DETECTION_ENABLED` / `TEMPORAL_DIGESTS_ENABLED` gate only their *creation* during ingestion.

### 7.5 Merge precedence, variants, and note grounding

1. **Entity → BM25 → vector.** `entity_nodes = entity branch results`; names recorded in `node_names_found` (lowercase). `_merge_search_results(entity_nodes, meili_res, node_names_found)` appends Meili rows whose name is unseen (comparison uses the raw name against a lowercase set — a Meili name with capitals never collides and is appended even if the entity branch already has it; the Meili index stores lowercase names, so in practice this is benign). **Meili rows end up in `entity_nodes`** and therefore get candidate type `entity_match` (§8.1), not a keyword-specific type.
2. Vector rows whose lowercase name is unseen become `vector_nodes` with `_source="vector"`.
3. **Vector name variants:** `find_name_variants_batch` over **all** vector node names (the comment says "person entities" but there is no type filter here) — new names get `_source="vector_variant"`, `_variant_of`. These variant rows come from Kuzu and have **no Qdrant enrichment**, so they usually have no text and are dropped at candidate build unless the loop later surfaces them another way.
4. `all_found_nodes = entity_nodes + vector_nodes`.
5. **Note grounding (citations):** build `{node_id: lower_name}` for nodes that have an id (`node_id` or `id`), call `graph.get_linked_evidence_by_node_ids(ids, limit_per_node=2, node_id_to_name=map)`. Cypher: `UNWIND $node_ids AS node_id MATCH (n:Node {id: node_id})<-[r:REFERENCES]-(note:Node) WHERE note.kind = 'note' WITH node_id, collect(DISTINCT {id: note.id, title: note.name}) AS evidence RETURN node_id, evidence`, sliced to 2 per node. Result → `node_to_notes[lower_name] = [{id, title}, ...]`. If no node has an id, the name-based `get_linked_evidence(names, 2)` is used instead (it resolves names via `qdrant.find_node_ids_by_names`). Log: `[Retrieval] Found N grounding notes (for source links)`. **A note is cited only if a `REFERENCES` edge from the note node to the entity node exists in Kuzu**; `title` is the graph's `note.name`, later overridden by the SQLite title in `_extract_references`.

Timing: `[⏱️ Timing] Retrieval phase: Xs (E entity + V vector nodes)`.

## 8. Candidate build, fusion, reranking, graph expansion

### 8.1 Candidate docs (the unit of evidence)

`hybrid_search` turns nodes into **candidate docs** — plain dicts that flow unchanged through reranking, the loop, `ChatWorkflow`, the API result, and the frontend:

```python
{
  "text": "<formatted block sent to the LLM>",        # from _build_node_text(node, [])
  "_rerank_text": "<same block>",                      # what the cross-encoder scores
  "type": "entity_match" | "vector_match" | "community_summary" | "graph_expansion",
  "original_obj": { "id"/"node_id", "name", "entity_type", "summary", "description"?, "isolated_contexts"?, "score"?, "_source", ... },
  "linked_notes": [ {"id": "<note uuid>", "title": "<note.name in Kuzu>"}, ... ],
  # added by _apply_reranker_logging:
  "rerank_score": float,        # 0.0 when the reranker is off/unavailable
  "reranker_rank": int,         # 1-based position in the *input* order, not the output order
  # graph_expansion only:
  "_origin_name": "<origin node name>",
}
```

Build order and types:

- **A. Entity nodes** (`entity_nodes`, incl. Meili rows and Person name variants): skipped if `_get_node_text(node)` is empty; `type="entity_match"`; `linked_notes = node_to_notes[lower_name]`.
- **B. Vector nodes** (incl. vector variants): same text check; `type="community_summary"` when `entity_type.lower() == "community"` else `"vector_match"`.
- No candidate type `neighbor_node` is produced any more, although the summary log line still counts it.

`_get_node_text(node)` = `summary` or `description` or `" ".join(isolated_contexts)`. `_build_node_text(node, [])` renders the root line: `"{name} is a {entity_type}. {text}"` when a type exists; else the text itself if it already starts with the name; else `"{name}. {text}"`. There is **no scoring fusion** across channels: vector scores, Meili pseudo-scores and entity matches are never combined numerically. Channel precedence only decides *which entry survives name-dedup*; the reranker alone decides the final order. (The `boosts`/`symbolic_immune` keys printed by `_log_retrieval_details` are relics of the 2026-02 "symbolic ranking" pipeline and are never set.)

If there are zero candidates, `hybrid_search` logs `[Retrieval] No candidates found` and returns `[]`.

### 8.2 The reranker stack

Three layers:

1. `RetrievalService._apply_reranker_logging(query, candidates, top_n, question_attribute, expected_entity_types, score_threshold)` — builds query + texts, applies scores, sorts, slices, thresholds.
2. `RerankerService.rerank(query, documents, top_n=None)` (`services/reranker.py`) — async façade: returns `[]` if no documents, if `reranker_gguf_path()` is `None` (logs `No GGUF selected — download/select a reranker in Setup`), or on any exception (logged at ERROR). Otherwise `await asyncio.to_thread(local_gguf_reranker.rerank, ...)` and `_normalize_results` (keeps dicts with `index` and a numeric `relevance_score`/`score`, mirrors both keys as floats).
3. `LocalGgufReranker.rerank` (`services/local_models.py`) — Qwen3-Reranker GGUF via llama-cpp-python, `n_ctx` from `ORB_RERANK_N_CTX` (default 8192), `logits_all=True`. Each document is scored **one at a time, sequentially** with a 1-token completion (`max_tokens=1, temperature=0, logprobs=5`) of the prompt
   ```
   <|im_start|>system\n{_RERANK_SYSTEM}\n<|im_end|>
   <|im_start|>user\n<Instruct>: Given a question, retrieve relevant passages that answer the question\n<Query>: {query}\n\n<Document>: {document}\n<|im_end|>
   <|im_start|>assistant\n<think>\n\n</think>\n
   ```
   Score = `P(yes) / (P(yes) + P(no))` from the top-5 logprobs when both tokens appear; else `0.9` if the sampled text starts with "yes", `0.1` if "no", else `0.0`. Returns `[{index, relevance_score, score, document}]` sorted desc, optionally sliced to `top_n`. Cost is therefore **one forward pass per candidate** — 30 candidates ≈ 30 prompt evaluations of up to 8 K tokens.

### 8.3 `_apply_reranker_logging` semantics

- **Query hints:** `rerank_query = f"{query} [{question_attribute}; type: t1, t2]"` (each part only when present). In `hybrid_search` both hints come from that sub-query's analysis; in the loop's expansion pass only `question_attribute` (from the original question) is passed.
- **Texts:** `candidate["_rerank_text"]` → fallback `_build_node_text(original_obj, [])` → fallback `candidate["text"]`.
- **Scores:** if `RERANKER_ENABLED`, call `reranker_service.rerank(rerank_query, texts)`; map `index → relevance_score`. If the reranker returns nothing, log `Model returned no scores, skipping candidate scoring`. Every candidate gets `rerank_score = model_scores.get(idx, 0.0)` and `reranker_rank = idx + 1`. **There is no keyword-overlap fallback** despite the docstring claiming one — disabled or failed reranking yields all-zero scores.
- **Order:** stable sort by `rerank_score` desc (ties keep channel order entity → BM25 → vector).
- **Cut:** `candidates[:top_n]` when `top_n` is not `None`; then drop `rerank_score < score_threshold` when `score_threshold` is not `None`.
- **Call sites:**

| Call site | `top_n` | `score_threshold` | hints |
|---|---|---|---|
| `hybrid_search` (initial candidates) | `RERANKER_TOP_K` (10) | `RERANKER_SCORE_THRESHOLD` (0.05) | sub-query attribute + expected types |
| loop, expansion docs | `RERANKER_TOP_K` (10) | none | original-question attribute |
| `_expand_relevant_neighbors` per-pair (§8.4) | manual: `GRAPH_EXPAND_TOP_NEIGHBORS` (10) | `GRAPH_EXPAND_SCORE_THRESHOLD` (0, i.e. off) if > 0 | none (raw sub-query) |

**Critical consequence:** with `RERANKER_ENABLED=false`, or when the reranker GGUF is missing/fails, every candidate scores `0.0 < RERANKER_SCORE_THRESHOLD (0.05)` and `hybrid_search` returns **an empty list every iteration**. The loop then feeds `docs=[]` to `iterative_step`, which (because `search_query and docs` is false) re-issues the "output the first search query" instruction, and the turn ends in exhaustion with "I couldn't find any relevant information…". Set `RERANKER_SCORE_THRESHOLD=0` if you must run without a reranker (§14).

Per-candidate DEBUG lines: `[Reranker] [i] name rerank=0.1234 | text: '...'`; INFO summary: `[Reranker] qwen3-reranker-0.6b scored N candidates (top score: …)`, `Ranked N candidates → keeping top K`, `Score threshold T dropped D candidate(s) → R remaining`. Note `settings.MODEL_RERANKER_LOCAL` is only a **label** in these logs and progress stages; the actual GGUF is `reranker_gguf_path()` (manifest selection / `ORB_RERANK_GGUF`).

### 8.4 Graph expansion (`_expand_relevant_neighbors(relevant_docs, question, surfaced_names)`)

Runs once per retrieval iteration on the reranked `selected_docs` (≤ `RERANKER_TOP_K`). Purpose: bring in 1-hop neighbours that carry the *bridge* fact for multi-hop questions, phrased as natural-language relationship sentences.

1. **Collect 1-hop edges.** For each doc's `original_obj` (needs `name`; uses `node_id`/`id` to skip Qdrant name→id resolution), `graph.get_related_nodes(node_name, max_depth=1, node_id=...)`. The 1-hop fast path runs two directed Cypher queries over `SEMANTIC_REL|REFERENCES` (outgoing then incoming; a neighbour seen in both keeps `outgoing`), returning `node_id, name, label(kind), depth=1, relationship_path=[rel_type or 'REFERENCES'], confidence_path, context_path=[NULL], natural_language_path=[NULL], edge_direction`. Incoming `REFERENCES` edges mean **note nodes** (`kind='note'`) that reference the entity are among the neighbours. Neighbours whose name is already in `surfaced_names` are skipped. Each kept edge becomes a `relationship_entry {source, src_node_id, rel_type, nl_sentence (None), neighbor(row), context (None), edge_direction}`.
2. **NL sentence enrichment from Qdrant.** Kuzu stores no relationship text, so one `qdrant.get_relationships_for_node_ids(all ids)` (two filtered scrolls over `node_relationships`, by `source_node_id` and `target_node_id`, deduped by `natural_language`) builds a `(src_id, tgt_id) → natural_language` map. For each entry: forward match → `nl_sentence = _extract_predicate(nl, src_name, tgt_name)`, `_nl_is_reverse=False`; else reverse match → predicate extracted with names swapped, `_nl_is_reverse=True`. `_extract_predicate` strips a leading/trailing occurrence of either entity name (case-insensitive) so `"scott derrickson directed doctor strange"` becomes `"directed"` and `_build_node_text` can re-add the names without doubling them.
3. **Neighbour content enrichment.** One `get_nodes_content_by_ids` for all neighbour ids → sets `description`, `summary` (= core description), `isolated_contexts`, and `entity_type` (from core `type` when Kuzu had none).
4. **Per-pair rerank (only when `RERANKER_ENABLED` and `len(entries) > GRAPH_EXPAND_TOP_NEIGHBORS`).** For each entry, render a one-relationship block with `_build_node_text(origin_node, [rel_entry])` where `rel_entry = {nl_sentence (or rel_type with underscores → spaces), neighbour_name, neighbour_type, neighbour_context (= _get_node_text(neighbor) or name), is_incoming}`; score all blocks against the raw sub-query; keep the top `GRAPH_EXPAND_TOP_NEIGHBORS`; if `GRAPH_EXPAND_SCORE_THRESHOLD > 0`, also drop entries below it. Otherwise ("at or below top-N limit") all entries are kept unranked. Log: `[GraphExpand] Ranked N neighbours → kept top K (scores: [...])`.
5. **Group by origin → one doc per source.** Entries are bucketed by `source`; neighbours are globally deduped by name (`seen_neighbors`) so a neighbour shared by two origins is attached to the first origin only. Text = `_build_node_text(origin_node, rel_entries)`:
   ```
   {origin} is a {type}. {origin context}
   {origin} {predicate} {neighbour}.            ← or "{neighbour} {predicate} {origin}." when is_incoming
   {neighbour} is a {ntype}. {neighbour context}
   ...
   ```
   Doc: `{"text", "_rerank_text", "type": "graph_expansion", "_origin_name", "original_obj": origin_node, "linked_notes": [], "_neighbor_names": [...]}`.
6. **Provenance.** `graph.get_linked_evidence(selected_neighbor_names, limit_per_node=2)` (name-based → Qdrant `find_node_ids_by_names` → the same `REFERENCES` Cypher) attaches the neighbours' notes as the expansion doc's `linked_notes`; `_neighbor_names` is then removed.
7. Returns the list (log `[GraphExpand] Added N neighbour node(s) via relationship expansion`) or `[]` when there were no unseen neighbours (`No new 1-hop neighbors to evaluate.`).

Edge attributes such as `edge_weight` (= strength·0.5 + confidence·0.3 + relevance·0.2, computed at ingestion), `confidence`, `mention_count`, `is_similarity` are **not used for ranking or filtering** during expansion; `confidence_path` is returned by Cypher but ignored. Ranking is purely the cross-encoder over NL text.

Back in the loop, the expansion docs are reranked a second time as whole documents (top 10, no threshold) and appended to `docs` for the LLM — but, as noted in §6.2, they are dropped from `all_docs` by the name-dedup because their `original_obj` is the origin node.

### 8.5 The older `_get_node_relationships` helper

`_get_node_relationships(node, max_relationships=5)` (1-hop neighbours with `nl_sentence` built from `natural_language_path`/`context_path`/`rel_path`, plus neighbour context from Qdrant) is still defined but **has no callers** in the current tree; it predates the Qdrant NL-lookup approach. Safe to ignore; do not "fix" callers to use it.

## 9. Context assembly, the reasoning/synthesis prompt, and thinking

### 9.1 What the LLM sees per iteration (no token budgeting)

`iterative_step` concatenates `doc["text"]` of **every** doc passed for that iteration (≤ 10 reranked candidates + ≤ 10 reranked expansion docs), separated by `\n\n---\n\n`. There is **no token budget, no per-doc truncation and no character cap** anywhere in retrieval or the workflow: the only limiters are `RERANKER_TOP_K` (docs per pass) and the natural length of node summaries/isolated contexts. On the local runtime the safety net is `LocalLlamaRuntime._remaining_output_budget` (working tree): the prompt is token-estimated against `n_ctx` (`ORB_LLAMA_N_CTX`, default 16384) and if fewer than 256 tokens would remain it raises `PromptTooLongError` — which `iterative_step` catches as a generic exception and turns into the empty result (`can_answer=False, next_query=None`) → loop `break` → exhaustion. So an oversize context manifests as "I couldn't find enough information", with the real cause only in `llm.log` (`iterative_step failed: Prompt is ~N tokens; context window is …`).

Doc text ordering inside the prompt = reranked order (candidates first, then expansion docs). Each candidate's text is the `_build_node_text` block; there is no per-source header, no score, no citation marker — the LLM never sees note ids, and **citations are not produced by the LLM** (see §9.4).

`_doc_passage(doc)` in `workflows/chat.py` (= `original_obj.summary` or `.description` or `doc.text`) is used only for the DEBUG "Context (N docs)" log line in `ChatWorkflow.chat`; it is not part of any prompt.

### 9.2 `iterative_step` prompt skeleton

```
You are a research assistant solving a multi-hop question step by step.

CONVERSATION SO FAR:                      ← only when history exists (§5.4)
User: ...
Assistant: ...

ORIGINAL QUESTION: {rewritten query}

PRIOR FINDINGS:                           ← only after ≥1 step recorded
Step 1: Searched for '{q}'
  Reasoning: {reasoning}
  Finding: {full_answer or 'Not found'}

QUERIES ALREADY TRIED (do NOT repeat these — choose a different angle):
  - {q}

CURRENT SEARCH: '{search_query}'          ← only when search_query and docs
RETRIEVED DOCUMENTS:
{doc text}

---

{doc text}

{task_instructions}
Reply:
```

`task_instructions` has two shapes:

- **Planning turn** (`search_query` is None **or** `docs` is empty): `Output the first search query needed to start answering this question:\nNEXT_QUERY: <one specific search query>` — note this same instruction is used on *any* iteration whose retrieval returned nothing, so the model is never told "the last search found nothing"; it only sees the query in the tried list.
- **Assessment turn** (docs present), general-KB mode (`BENCHMARK_MODE=False`, the default):
  ```
  Assess the current results:
  REASONING: <how these documents relate to the question and what you have found so far>
  FINDING: <a summary of what is relevant in these documents — key facts, entities, relationships. Always write something; use 'Not found' only if truly nothing here is relevant>

  Then decide:
    If you have enough information to answer the ORIGINAL QUESTION:
    ANSWER: <a complete, natural-language answer covering everything relevant you found — see output rules below>

    If you need more information:
    NEXT_QUERY: <one specific search query, different from all prior ones>

  {_REASONING_RULES_GENERAL}   ← trace step by step, cover all aspects, synthesise across searches
  {_OUTPUT_RULES_GENERAL}      ← complete natural-language answer, thorough, organised, stick to documents, acknowledge gaps, no filler, "If you cannot answer yet → output NEXT_QUERY, not ANSWER"
  ```
  With `BENCHMARK_MODE=True` the HotPotQA-style `_REASONING_RULES`/`_OUTPUT_RULES` are used instead (bare answer phrase, YES/NO discipline, exact extraction). `BENCHMARK_MODE` is still a live setting in `core/config.py` and `iterative_step` despite commit `8eba91d` "Remove benchmark mode" — that commit removed it from ingestion and docs only.

The system prompt for this call (via `_reason_step`) is `"You are a deep reasoning engine. Analyze the input carefully. Detect conflicts, subtleties, or hidden connections."` on local/OpenAI; Gemini and Anthropic receive the prompt as a single user message with no system prompt.

### 9.3 Response parsing (`iterative_step`)

- A regex extracts labelled sections `REASONING | FINDING | FULL_ANSWER | ANSWER | NEXT_QUERY`, tolerant of `**bold**` labels and multi-line values; first occurrence of each label wins.
- `reasoning = REASONING`; `full_answer = FINDING or FULL_ANSWER`.
- If `ANSWER` is present and not in `{INSUFFICIENT, NONE, N/A, UNKNOWN, NOT FOUND}` → `can_answer=True, final_answer=ANSWER`.
- Else if `NEXT_QUERY` present → `_clean_next_query`: first non-empty line, leading bullets/asterisks and surrounding quotes stripped.
- **Unlabelled fallback (planning turn only, `docs` empty):** if neither label matched, the whole reply (minus a leading `Reply:`/`Query:`) is accepted as the next query when it is a single line ≤ 200 chars and not a non-answer token (added in `4408a72` for small local models that ignore the label).
- **FINDING fallback:** if there is neither ANSWER nor NEXT_QUERY but a meaningful FINDING (not in the not-found set), the FINDING is promoted to the final answer (`can_answer=True`) — the model "forgot to switch to the terminating format".
- LLM exceptions → `{reasoning:"", full_answer:"", can_answer:False, final_answer:None, next_query:None, thinking:None}` after a warning.

Return dict: `{reasoning, full_answer, can_answer, final_answer, next_query, thinking}`. The raw model reply is logged at INFO in `llm.log` (`[LLM] iterative_step raw response:`), which is the fastest way to see why a turn stopped.

### 9.4 Answer assembly and citations (`ChatWorkflow`)

1. `all_docs` → `_dedupe_docs` (key: `original_obj.name` or `note_id` or `text`; first wins) → `_truncate_context(docs, max_docs)`: if more than `max_docs` (6 for `chat`, 12 for `retrieve_for_query`), keep the top by `rerank_score` (docs lacking the key sort as 0.0); then **any doc without a `rerank_score` key gets `linked_notes = []`** ("clear unverified linked_notes"). In practice every doc from `hybrid_search` has the key, so this only bites docs injected by other means. Because `rerank_score`s come from *different* reranker passes (one per sub-query) they are not strictly comparable, but they are treated as such here.
2. `answer = final_answer or fallback message` (§6.3).
3. `_extract_references(unique_docs)`: collect `linked_notes[*].id → title` across docs in order; one SQLite query `SELECT id, title FROM notes WHERE id IN (...)` overrides titles with the current note title (falls back to the graph title, then `"Untitled Note"`); dedupe by note id; render `- [{title}](/notes/{id})`. Log `[Chat] Found N references for response`.
4. If any references: `answer += "\n\n### References\n" + "\n".join(references)`. The heading text and `/notes/<id>` link format are a **contract with the frontend regex** (§13.3). Citations are per-note, not per-claim, and are derived purely from `REFERENCES` edges of the surviving top-6 docs — an answer can quote a fact whose source node was truncated away (no reference) or list a note whose content the LLM never saw (the note text itself is never retrieved; only node summaries are).

The `context` list in the result is `unique_docs` (full candidate dicts). The frontend ignores it; it is stored nowhere except in `_chat_status` for async jobs (and only `context_count` goes into message metadata).

### 9.5 Thinking extraction

`LLMService._reason_step(prompt) -> (content, thinking)`:

- Gemini / Anthropic branches: `thinking=None` always.
- OpenAI-compatible (incl. the in-process local shim): `thinking = message.reasoning_content` if the server exposed it; else, if `content` contains `<think>…</think>`, the first block is extracted as thinking and all `<think>` blocks are removed from `content`.
- The local runtime's streaming path concatenates `delta.content or delta.reasoning_content` into one string, so for local GGUFs thinking arrives only via literal `<think>` tags in the text (Gemma 4 emits them when it "thinks"; Qwen-style models likewise). `_openaiish_chat_response` builds a `SimpleNamespace` message without `reasoning_content`.
- `iterative_step` returns the step's thinking; the loop surfaces **only the terminating step's** thinking (`result.get("thinking")`), `None` on exhaustion. `ChatWorkflow` passes it through as `thinking`; the API stores it in `chat_messages.thinking` and the status result; the frontend renders a collapsible "Model thinking" `<pre>` block above the answer.

## 10. Model residency during a chat turn, and what it costs

Local mode keeps **one heavy GGUF resident at a time** (`LocalLlamaRuntime` + `LocalGgufReranker`, both guarded by locks; see [Local models](12-local-models-and-inference.md)). Every call site below implicitly triggers a swap when the needed model is not the resident one:

| Step | Needs | Triggered by | Unloads |
|---|---|---|---|
| follow-up rewrite | chat GGUF | `_reason_step_sync` → `create_chat_completion` → `ensure_chat_loaded` | embed, reranker, multimodal |
| `analyze_query` (loop, then each `hybrid_search`) | chat GGUF | `extract_structured` (local branch) | — if chat already resident |
| `embed_query` | embed GGUF | `local_llama_runtime.embed` → `ensure_embed_loaded` | **chat**, reranker, multimodal |
| candidate rerank; per-pair expansion rerank; expansion-doc rerank | reranker GGUF | `LocalGgufReranker.ensure_loaded` (calls `local_llama_runtime.unload()` first) | **chat + embed**, multimodal |
| `iterative_step` | chat GGUF | `create_chat_completion` | reranker, embed |

So one retrieval iteration in local mode is at minimum **chat → embed → rerank → chat** (three loads), and a full 3-iteration turn that answers on iteration 3 is roughly: rewrite (chat) → analyze (chat) → plan step (chat) → [analyze sub-query (chat) → embed → rerank → chat] × 2. The `analyze_query` cache only helps when the exact same query string recurs (e.g. the same question asked twice in a day, or a sub-query identical to the original). Model load time dominates wall time on machines with slow disks; the benchmark reports in `Results/` measured 2–4 min per HotPotQA question with Gemma 4 E4B and up to 10 iterations before the desktop build reduced `MAX_LOOP_ITERATIONS` to 3.

**Idle unload:** `ORB_MODEL_IDLE_SECONDS` (default 300; `0` = never) unloads whichever GGUF is resident after inactivity, so the first turn after a pause pays a cold chat load before the rewrite call.

**Timing instrumentation (working tree):** `local_models.model_load_clock` (`ModelLoadClock`) accumulates seconds per kind (`chat`, `embed`, `rerank`) on every load. `ChatWorkflow.chat` / `retrieve_for_query` snapshot it before and after and emit one line to `chat.log`:

```
[Timing] chat total=84.3s model_load=41.2s inference=43.1s loads=chat×3,embed×2,rerank×2 docs=6
```

`model_load` is the summed load time in the window, `inference` is `total − model_load` (everything else, including Kuzu/Qdrant/Meili I/O), `loads` is `ModelLoadClock.describe(delta)` (`none` when nothing loaded). This is the number to look at when deciding whether a slow turn is disk-bound or compute-bound.

**Cross-KB and cross-request contention.** The runtime locks are process-wide; the async job lock serialises async chats but the sync `/chat` endpoint and ingestion do not take it. A chat running while ingestion embeds documents will thrash chat ↔ embed loads. Per-KB pinned local chat models (§5.5) add another swap axis: switching KBs with different pinned GGUFs reloads the chat model even when "chat" is already resident.

**Cloud providers** (`LLM_PROVIDER=openai|gemini|anthropic|huggingface`, or a KB override) remove the chat-model swaps but **embedding and reranking stay local** — the embed ↔ rerank swap per iteration remains, and the reranker's sequential per-document scoring is unchanged.

**Batching/parallel I/O already in place** (commits `8de5cda`, `b84ca73`): entity/BM25/vector branches concurrent; per-term Meili queries concurrent; Qdrant collections searched in a thread pool; all Kuzu variant lookups batched (`find_name_variants_batch`); Qdrant content/relationship enrichment batched by id list; note grounding one Cypher `UNWIND`; SQLite title lookup one `IN` query. Remaining serial hot spots: the reranker's per-document loop, `get_related_nodes` once per relevant doc (two Cypher queries each), and the LLM calls themselves.

## 11. Configuration keys that influence retrieval and chat

All `settings.*` keys come from `backend/app/core/config.py` (pydantic-settings; env vars / `backend/.env`; some are overridden at runtime by `DATA_DIR/runtime_config.json` — see [Configuration reference](21-configuration-reference.md)). `ORB_*` keys are read directly from the environment in `local_models.py` (`LIVEOS_*` aliases still accepted).

| Key | Default | Where read | Effect |
|---|---|---|---|
| `MAX_LOOP_ITERATIONS` | `3` | `retrieve_with_iterative_loop` | LLM steps per turn; retrievals = iterations − 1. Comment in config: 3 is enough for personal KBs; HotPotQA gains little past ~3 before KB-miss exhaustion |
| `RERANKER_ENABLED` | `True` | `_apply_reranker_logging`, `_expand_relevant_neighbors`, `_search_qdrant_multi_collection` | Off ⇒ all `rerank_score = 0.0`, vector threshold switches to `VECTOR_SIMILARITY_THRESHOLD`, per-pair expansion rerank skipped. **Off with default `RERANKER_SCORE_THRESHOLD` ⇒ empty retrieval** (§8.3) |
| `RERANKER_TOP_K` | `10` | `hybrid_search`, loop expansion rerank | Docs kept per rerank pass (both passes) |
| `RERANKER_SCORE_THRESHOLD` | `0.05` | `hybrid_search` only | Drop candidates below this yes-probability after the top-K cut |
| `VECTOR_PRE_RERANK_THRESHOLD` | `0.45` | `_search_qdrant_multi_collection` | Qdrant `score_threshold` when the reranker is on |
| `VECTOR_SIMILARITY_THRESHOLD` | `0.50` | same | Qdrant `score_threshold` when the reranker is off |
| `GRAPH_EXPAND_TOP_NEIGHBORS` | `10` | `_expand_relevant_neighbors` | Max (origin, neighbour) pairs kept per iteration; also the trigger — per-pair rerank runs only when more pairs than this exist |
| `GRAPH_EXPAND_SCORE_THRESHOLD` | `0` | same | If > 0, drop pairs scoring below it after the top-N cut |
| `CHAT_HISTORY_MAX_MESSAGES` | `24` | `chat_store.get_recent_history`, `rewrite_follow_up_query`, loop context | Messages (not turns) of history fetched and prompted |
| `MAX_POTENTIAL_QUESTIONS` | `10` | **nothing** | Dead setting from the Sub-Questions approach |
| `BENCHMARK_MODE` | `False` | `LLMService.iterative_step` | Switches to bare-answer HotPotQA rules; still functional despite "removal" commit |
| `CHAT_MODEL` | `None` | `get_chat_model` | Global chat model override (set by `PATCH /api/v1/settings` / runtime_config); beaten only by a per-KB `llm_model` |
| `LLM_PROVIDER` / `LLM_MODEL` | `local` / `local-chat` | `LLMService.__init__`, `get_chat_model` | Provider for analysis/reasoning/rewrite; `ollama`/`lm_studio` map to `local` |
| `LLM_FALLBACK_PROVIDER` | `None` | `LLMService` | Secondary provider used by `extract_structured` fallbacks (see doc 13) — affects `analyze_query` robustness |
| `OPENAI_MODEL` / `GEMINI_MODEL` / `ANTHROPIC_MODEL` / `HUGGINGFACE_MODEL` (+ `*_API_KEY`) | `None` | `get_chat_model`, `ai_gate` | Provider-specific chat model and gate keys |
| `EMBEDDING_MODEL` | `local-embed` | `EmbeddingService` | Only its basename matters: contains `qwen3` ⇒ query instruction prefix |
| `MODEL_RERANKER_LOCAL` | `qwen3-reranker-0.6b` | logs, progress stage | **Label only**; the file used is `reranker_gguf_path()` |
| `QDRANT_COLLECTION_NODE_CORES` / `_RELATIONSHIPS` / `_ISOLATED_CONTEXTS` | `node_cores` / `node_relationships` / `node_isolated_contexts` | `QdrantService` | Base names; per-KB contexts use per-KB collection names — retrieval detects hit kind by substring `"relationships"` / `"isolated_context"` in the collection name, so renaming collections without those substrings breaks merge logic |
| `MEILI_INDEX_NAME` | `orb_nodes` | `MeilisearchService` | BM25 index (per-KB variants) |
| — | — | `ai_gate` | Chat endpoints 503 only when nothing is reachable: no GGUFs, no cloud key, no `LLM_BASE_URL`. `AI_SETUP_MODE` no longer gates. |
| `LOG_LEVEL` | `INFO` | `core/log.py` | `DEBUG` enables `_log_retrieval_details` (full texts sent to LLM) and per-candidate reranker lines |
| `ORB_MODEL_IDLE_SECONDS` | `300` | `local_models.model_idle_seconds` | Idle unload of resident GGUF (0 = keep) |
| `ORB_RERANK_N_CTX` | `8192` | `LocalGgufReranker.ensure_loaded` | Reranker context; long candidate texts beyond it are truncated by llama.cpp |
| `ORB_RERANK_GGUF` / `ORB_CHAT_GGUF` / `ORB_EMBED_GGUF` | pinned HF paths | `local_models` | Default model ids (overridden by Setup selection in the models manifest) |
| `ORB_LLAMA_N_CTX` | `16384` | `LocalLlamaRuntime` | Chat context window; bounds the whole `iterative_step` prompt (docs + history + rules) |
| `ORB_LLAMA_MAX_TOKENS` | unset (working tree; was `10240`) | `create_chat_completion` | Optional hard output cap; unset ⇒ `n_ctx − prompt − 32` |
| `ORB_LLAMA_PROMPT_RESERVE` | `4096` | `_chat_kwargs` | Raises `n_ctx` when a fixed `max_tokens` is set |
| `COMMUNITY_DETECTION_ENABLED` / `TEMPORAL_DIGESTS_ENABLED` / `TEMPORAL_DIGEST_PERIOD` | `False` / `False` / `month` | ingestion only | Whether community/digest nodes exist to be retrieved; retrieval itself has no switch |
| KB row `llm_provider` / `llm_model` / `llm_ingestion_model` | `NULL` | `KBContext.llm` | Per-KB override of provider + chat model (working tree) |

Hard-coded constants worth knowing: Kuzu `find_nodes_by_name` `LIMIT 50`; name variants `limit_per_name=5`; Meili `limit=100` per term; Qdrant `limit=500` per collection; note grounding `limit_per_node=2`; `_dedupe_docs`/`_truncate_context` `max_docs` 6 (chat) / 12 (finance retrieval); history truncation 600/500 chars; rewrite acceptance ≤ 300 chars; title 72/69 chars; `_query_analysis_cache` 64 entries; unlabelled NEXT_QUERY fallback ≤ 200 chars; frontend poll 1000 ms × 600 attempts, 8 consecutive errors.

## 12. Function reference

### 12.1 `backend/app/services/retrieval.py`

| Function | Purpose |
|---|---|
| `RetrievalService.__init__(graph, qdrant, meili, llm=None)` | Bind per-KB stores (defaults to module singletons) and optional per-KB LLM |
| `RetrievalService._llm` (property) | Per-KB LLM override or lazily-imported global `llm_service` |
| `_log_retrieval_details(query, results, query_entities, query_concepts)` | DEBUG dump of every final candidate: type, score, full summary, isolated context, linked notes, exact text sent to the LLM |
| `_get_node_relationships(node, max_relationships=5)` | Legacy 1-hop relationship sentences for a node (Kuzu + Qdrant neighbour contexts). **Unused** |
| `_expand_relevant_neighbors(relevant_docs, question, surfaced_names)` | 1-hop graph expansion: collect unseen neighbours, fill NL predicates from Qdrant, enrich, per-pair rerank to top-N, emit one `graph_expansion` doc per origin with neighbour note provenance |
| `_extract_predicate(nl, src_name, tgt_name)` (static) | Strip leading/trailing entity names from a stored NL sentence to get the bare predicate |
| `_build_node_text(node, relationships, brief_root=False)` | Render `"{name} is a {type}. {ctx}"` plus one `"{a} {predicate} {b}."` + neighbour descriptor line per relationship (`brief_root` = header only; currently never passed as True) |
| `_get_node_text(node)` | `summary` → `description` → joined `isolated_contexts` |
| `_search_qdrant_multi_collection(query_vector, date_filter, period_filter)` | Vector search over the three collections with temporal filters; normalise and merge hits per node name; back-fill names/types from cores |
| `_search_meili_by_keyword(query)` | One Meili query (limit 100) → node rows keyed by name with rank pseudo-scores |
| `_merge_search_results(primary, secondary, seen_names)` | Append secondary rows whose name is unseen (entity → BM25 merge) |
| `hybrid_search(query, top_k=50)` | One retrieval pass: analyse → embed → parallel entity/BM25/vector → merge → note grounding → candidates → rerank (top-K + threshold) → logs |
| `retrieve_with_self_correction(query, top_k, progress_callback, conversation_history)` | Alias of `retrieve_with_iterative_loop` (legacy name) |
| `_apply_reranker_logging(query, candidates, top_n, question_attribute, expected_entity_types, score_threshold)` | Score candidates with the GGUF reranker (query + hints), sort, slice, threshold; sets `rerank_score`/`reranker_rank` |
| `retrieve_with_iterative_loop(query, top_k, progress_callback, conversation_history)` | The research loop: plan → (search → expand → rerank → reason) × N → answer or best finding |
| `retrieval_service` (module) | Default-KB singleton built on import (module-level `graph_service`/`qdrant_service`/`meilisearch_service`); per-KB instances come from `KBContext._ensure_lazy` |

### 12.2 `backend/app/workflows/chat.py`

| Function | Purpose |
|---|---|
| `_doc_passage(doc)` | Cleanest text for a doc (`summary`/`description`/`text`) — debug logging only |
| `_dedupe_docs(docs)` | Dedupe by `original_obj.name` / `note_id` / `text`, first wins |
| `_load_snapshot()` | `model_load_clock.snapshot()` or `{}` (working tree) |
| `_log_timing(kind, total, load_before, **extra)` | Emit `[Timing] kind total=… model_load=… inference=… loads=… docs=…` to `chat.log` (working tree) |
| `_describe_loads(delta)` | `ModelLoadClock.describe` wrapper (working tree) |
| `_truncate_context(docs, max_docs)` | Keep top `max_docs` by `rerank_score`; clear `linked_notes` on docs without a score |
| `ChatWorkflow.__init__(retrieval=None, llm=None)` | Bind per-KB retrieval service and LLM (defaults: singletons) |
| `ChatWorkflow._retrieve_context(user_query, history, progress_callback, max_context_docs)` | Rewrite follow-up → `retrieve_with_self_correction(top_k=50)` → dedupe/truncate; returns `(rewritten_query, final_answer or "", unique_docs, thinking)` |
| `ChatWorkflow.chat(user_query, history, progress_callback)` | Full turn: retrieve (max 6 docs) → choose answer/fallback → references → `### References` block → timing → result dict |
| `ChatWorkflow.retrieve_for_query(user_query, history, progress_callback)` | Evidence only (max 12 docs), no answer; used by the finance path |
| `ChatWorkflow._extract_references(docs)` | Unique `- [title](/notes/id)` lines from `linked_notes`, titles refreshed from SQLite `notes` |

### 12.3 `backend/app/api/chat.py` and `backend/app/services/chat_store.py`

| Function | Purpose |
|---|---|
| `_answer_chat_query(query, kb, history_turns, progress_callback)` | Route: `firefly_service.looks_like_finance_query(query)` (keyword list: balance(s), transaction(s), spending, spent, income, expense(s), budget, cash, account(s), finance, financial, report, net worth, savings) → `retrieve_for_query` + `answer_finance_question`; else `ChatWorkflow.chat` |
| `list_chat_conversations` / `create_chat_conversation` / `get_chat_messages` / `delete_chat_conversation` | Thin wrappers over `chat_store` with 404s |
| `chat(body, kb)` | Sync endpoint (§4.3) |
| `_run_chat_job(request_id, query, kb, conversation_id, history)` | Async job body: progress → answer → persist assistant → terminal status |
| `_run_chat_job_serialized(...)` | Wraps `_run_chat_job` in `_chat_job_lock`, publishing the waiting stage |
| `start_chat(body, kb)` | Async endpoint (§4.4) |
| `get_chat_status(request_id)` | Status read |
| `ChatStore.*` | See §5.2 |
| `RerankerService.rerank(query, documents, top_n=None)` | Async façade over `local_gguf_reranker.rerank`; `[]` on missing GGUF/failure |
| `_normalize_results(results)` | Coerce reranker rows to `{index, relevance_score: float, score: float, ...}` |

## 13. Wire shapes and the frontend contract

### 13.1 Source / citation payload

Sources reach the client in **two** forms:

1. **In the answer text** — the only form the UI renders: a trailing block
   ```
   ### References
   - [Note title](/notes/<note_id>)
   - [Another note](/notes/<note_id>)
   ```
2. **In `result.context`** (async status result and sync response) — the candidate dicts of §8.1 with `linked_notes: [{id, title}]`. The frontend does not read `context`; it exists for API consumers and debugging. The persisted message keeps only `metadata.context_count`.

Conversation and message records (`GET .../conversations`, `.../messages`, `ChatConversation`/`ChatMessageRecord` in `frontend/src/lib/types.ts`):

```json
{"id": "...", "kb_id": "...", "title": "New Chat", "created_at": "2026-09-02T10:00:00+00:00", "updated_at": "..."}
{"id": "...", "conversation_id": "...", "role": "assistant", "content": "…\n\n### References\n- […](/notes/…)", "thinking": "…|null", "created_at": "..."}
```

`ChatStatus` (`GET /chat/status/{id}`):

```ts
{ request_id: string; conversation_id?: string; stage: string; model?: string | null; done?: boolean;
  result?: { answer?: string; thinking?: string | null; conversation_id?: string; assistant_message_id?: string }; error?: string }
```

### 13.2 Client state machine (`frontend/src/lib/chat-context.tsx`)

- `ChatProvider` holds `messages`, `conversations`, `activeConversationId`, `isLoading`, `isLoadingConversations`, `loadingStage`, `loadingModel`. `initializeForKb(kb)` (run when the KB changes) lists conversations and opens the most recent one; `selectConversation`, `startNewConversation`, `deleteActiveConversation` (DELETE then select the next remaining conversation or start fresh).
- `sendMessage(text, kb)`:
  1. Ignored if blank or `isLoading` (the input and Send button are also disabled while loading — there is **one in-flight turn per tab**).
  2. Cancels any previous poller (`pollCleanupRef`) and bumps a `sendGenerationRef` "generation" so late responses from an older send are ignored (`isStale()`).
  3. Optimistically appends `{id: "local-user-<ts>", role: "user", content}`; sets `loadingStage = "Starting chat request"`.
  4. Generates `requestId` (`crypto.randomUUID()`), calls `api.startChat(text, kb, requestId, activeConversationId)` → `POST /chat/async?kb=…`; on resolve records `conversation_id`, starts `setInterval(poll, POLL_INTERVAL_MS = 1000)` and polls once immediately. If the POST rejects (503 AI gate, 404 KB, network) → `fail()`.
  5. `poll()`: gives up after `POLL_MAX_ATTEMPTS = 600` (~10 min) or `POLL_MAX_CONSECUTIVE_ERRORS = 8` consecutive rejected status calls; otherwise updates `loadingStage`/`loadingModel`/`activeConversationId` from the status; when `done`: `error` → `fail()`, else `complete(result.answer, result.thinking, result.conversation_id || conversation_id)`.
  6. `fail()`: appends a **local, unpersisted** assistant message `"Sorry, I encountered an error. Please try again."` and clears loading state. The backend job (if any) continues.
  7. `complete()`: appends an optimistic assistant message (`"I couldn't generate a response."` if `answer` is empty), clears loading state, `loadConversations(kb)` (titles/order), then `getChatMessages(conversationId)` and **replaces** the message list with server records (real ids, server timestamps) — if that refetch fails the optimistic messages stay.
- Unmounting the provider stops polling. Navigating away and back re-runs `initializeForKb`, which shows the completed answer once persisted.

### 13.3 Rendering (`frontend/src/app/chat/page.tsx`)

- **Stage bubble:** while `isLoading`, a spinner bubble shows `loadingStage || "Thinking..."` and, when `loadingModel` is set, a second line `Using {loadingModel}`. Stages are shown verbatim (§4.5); there is no stage → icon/percentage mapping.
- **Assistant body** (`AssistantMessageBody`): splits `message.content` with `/###?\s*References[:\s]*\n([\s\S]+?)$/i`. Text before the match is rendered with `react-markdown` + `remark-gfm`; each line of the captured block is matched with `/\[([^\]]+)\]\(\/notes\/([^)]+)\)/` and rendered as a pill button (FileText icon + title). Clicking calls `api.getNote(noteId, kb)` and opens the in-page note preview modal (`SegmentedNoteContent`) via a `window.__chatSetPreview` hook; the note preview supports file (`📎`/`🎤`) links and entity clicks. Lines that do not match the link regex are silently dropped, so the backend must keep the exact `- [title](/notes/<id>)` format.
- **Thinking:** when `message.thinking` is non-empty, a "Model thinking" toggle (chevron) reveals a monospace `<pre>` block above the body; expansion state is per message id.
- **Entity highlighting:** `useScannedEntities(content, kb, {enabled, cacheKey: message.id})` POSTs the answer text to `/api/v1/graph/entities/scan-text` (regex-extracted capitalised candidates → Meili `search_nodes(candidate, 2)`, skipping `note`/`community` types) and `injectEntityLinks` rewrites matched names into `entity://<node_id>` links rendered as dashed-underline buttons that open `EntityDetailPanel`. Only the **5 most recent assistant messages** (`ENTITY_SCAN_RECENT_LIMIT`) are scanned (commit `b84ca73`: older conversations used to fire one scan per message on load); results are cached in a bounded module-level map keyed by `kb:messageId`, and in-flight scans are aborted via `AbortSignal` when content/KB changes.
- **Markdown links:** `urlTransform`/`MarkdownAnchor` from `lib/markdown-entities` handle `entity://`, `/notes/...`, file links; other anchors render normally.
- **User messages** render as plain `whitespace-pre-wrap` text.
- Header: New Chat, Delete Chat (confirm dialog → soft delete), Export (broken, §4.6), conversation `<select>` (titles), KB badge, store badges. Footer hard-codes "Gemma3 4B · Qwen3 Embedding · Qwen3 Reranker · Florence 2 · Whisper V3".

## 14. How to tune retrieval

Work from the logs (§15.1) before touching knobs: `retrieval.log` tells you which channel found (or missed) the node, what the reranker scored it, and what was cut; `llm.log` tells you what the reasoning step did with it.

| Symptom | Likely cause | Knob / change |
|---|---|---|
| Correct node is retrieved (visible in `[Retrieval] Prepared N candidates`) but not in the top list | Reranker cut | Raise `RERANKER_TOP_K` (more docs per pass, more prompt tokens, +1 reranker pass per extra doc); lower `RERANKER_SCORE_THRESHOLD` |
| Node never appears in vector hits | Similarity below threshold or node has no merged core vector | Lower `VECTOR_PRE_RERANK_THRESHOLD` (0.45 → 0.35 is a common first move; the reranker will discard noise); confirm the node has a `node_cores` point (see doc 15) |
| Entity named in the question is not found by name | LLM `entities` empty/mis-split, or node name differs | Check `[LLM Analysis] ... Entities: [...]` in `retrieval.log`; improve ingestion naming/aliases; the fuzzy `CONTAINS` match is already lenient |
| Multi-hop questions stop after one hop | Not enough iterations | `MAX_LOOP_ITERATIONS` 3 → 4 or 5 (each extra iteration ≈ one full embed/rerank/chat swap cycle in local mode); check `[IterLoop] EXHAUSTED` vs `No NEXT_QUERY` in logs to see which termination fired |
| Bridge entity is only reachable through a neighbour | Expansion budget | Raise `GRAPH_EXPAND_TOP_NEIGHBORS`; keep `GRAPH_EXPAND_SCORE_THRESHOLD` at 0 unless expansions are visibly noisy |
| Answers ignore facts that were retrieved | Prompt too long / weak model | Lower `RERANKER_TOP_K`, or move `LLM_PROVIDER`/KB override to a stronger model; check `llm.log` for `PromptTooLongError` or `hit max_tokens` |
| "I couldn't find any relevant information" on every question | Reranker missing/disabled with default threshold | Select/download the reranker in Setup, or set `RERANKER_SCORE_THRESHOLD=0`; look for `[Reranker] No GGUF selected` / `Model returned no scores` |
| Month/day questions miss ordinary entities | Month mode restricts `node_cores` to `period_key` matches; day mode searches contexts only | Enable temporal digests/communities at ingestion, or rephrase without the date; there is no config knob for this behaviour |
| Slow turns | Model swaps | Read the `[Timing] chat … model_load=… loads=…` line; raise `ORB_MODEL_IDLE_SECONDS`/set 0 to avoid cold loads, use a cloud chat provider to remove chat-model swaps, reduce `RERANKER_TOP_K` to shorten reranker passes |
| Follow-ups lose context | History window | `CHAT_HISTORY_MAX_MESSAGES` (also increases rewrite prompt size) |
| Answers in the wrong style (too terse) | `BENCHMARK_MODE` accidentally on | Ensure `BENCHMARK_MODE=false` |

Changing thresholds requires a backend restart (settings are read at import; `runtime_config.json` covers provider/model keys only). Benchmarking harness: `backend/tests/benchmark/` (see [Testing & benchmarks](24-testing-and-benchmarks.md)) — `BENCHMARK_MODE=true` plus higher `MAX_LOOP_ITERATIONS` reproduces the `Results/` configuration.

## 15. Timing, logging, and failure modes

### 15.1 Where to look

All files are rotating (10 MB × 5) under `DATA_DIR/logs/` (`core/log.py` `COMPONENT_LOG_FILES`; format `time | logger | LEVEL | message`):

| File | Loggers | What you find |
|---|---|---|
| `retrieval.log` | `RetrievalService`, `QdrantService`, `MeilisearchService`, `RerankerService` | `[HybridSearch]` banner per sub-query, `[LLM Analysis]` fields, `[Entity]`/`[Keyword]`/`[Vector]`/`[Qdrant]`/`[Meili]` hit counts, `[GraphExpand]` relationship list + scores, `[Reranker]` summaries, final ranked list `1. [type] name (rerank=…)`, `[⏱️ Timing]` lines, `[IterLoop]` START/iteration/`iterative_step result`/COMPLETE/EXHAUSTED banners; at DEBUG the full `_log_retrieval_details` dump |
| `chat.log` | `ChatWorkflow`, `ChatStore` | `[Chat] Started processing query`, rewritten query, unique doc count, chosen answer, reference count, `Total pipeline duration`, `[Timing] chat …` |
| `llm.log` | `LLMService`, `LocalModels`, `InferenceDevice` | `rewrite_follow_up_query: 'a' -> 'b'`, `Query analysis cache hit`/`failed`, raw `iterative_step` replies, parse fallbacks, GGUF load/unload/switch lines, `[ModelLoad] kind loaded in Xs`, repetition-loop retries, truncation warnings |
| `graph.log` | `GraphService` | Kuzu query failures with the Cypher and params |
| `api.log` | `API`, uvicorn | Request lines, `[Chat] Async chat job failed` tracebacks |
| `errors.log` | root ≥ ERROR | Everything at ERROR from all components |

Historical benchmark runs under `Results/**/…Logs/` also contain a `reranker.log`; in the current code the reranker logs into `retrieval.log`.

### 15.2 Failure modes

| Situation | Behaviour | Surface |
|---|---|---|
| AI not configured (GGUFs missing, no cloud key, no endpoint) and no KB-pinned provider | `require_ai(kb)` → 503 before anything is persisted | Frontend `startChat` rejects → `fail()` → generic error bubble |
| Unknown `?kb=` | 404 from `get_kb` | same |
| Empty `query` | 422 (pydantic `min_length=1`) | same |
| Kuzu unavailable / KB has no Kuzu path | `KBContext.graph` raises `RuntimeError` when the retrieval service is first built (`_ensure_lazy`) → job `Failed` | Error bubble; `api.log` traceback |
| Qdrant down | `search_all_collections`/`get_nodes_content_by_ids` return `[]`/`{}` (availability check); entity nodes stay text-less and are dropped; vector channel empty | Usually ends in exhaustion → "couldn't find…" |
| Meilisearch down | `search_nodes` returns `[]` | Keyword channel silently empty |
| Reranker GGUF not selected / load failure | `RerankerService.rerank` → `[]` → all scores 0 → `hybrid_search` returns `[]` under the default threshold | "I couldn't find any relevant information…"; `[Reranker] No GGUF selected` |
| Chat GGUF not downloaded (local) | `ensure_chat_loaded` raises `RuntimeError("Local GGUF models are not downloaded…")` in the rewrite call → job `Failed` | Error bubble |
| Per-KB pinned local model not downloaded | `resolve_chat_gguf` raises → `Failed` (PATCH normally prevents this) | Error bubble |
| `analyze_query` JSON/provider failure | Safe defaults: no entities, keywords = whitespace tokens; entity channel skipped | Weaker recall; `Query analysis failed` in `llm.log` |
| `iterative_step` LLM failure or `PromptTooLongError` | Empty result → `No NEXT_QUERY … stopping` → exhaustion | "couldn't find enough information"; cause only in `llm.log` |
| LLM ignores the protocol (no labels) | Planning turn: single-line fallback accepted as query; assessment turn: FINDING promoted to answer, else stop | Possibly premature/odd answer |
| LLM repeats a query | Not detected; the loop searches again (cache makes analysis cheap, but embed/rerank/reason repeat) | Wasted iteration |
| Local repetition loop (Gemma ordinal cascade) | `create_chat_completion` retries up to 3 fresh samples, then `RuntimeError` → exhaustion or `Failed` | `llm.log` `Chat repetition loop on attempt n/3` |
| Empty graph / fresh KB | All channels empty → exhaustion | "I couldn't find any relevant information in the knowledge base to answer that." |
| Note deleted after ingestion but `REFERENCES` edge remains | Reference rendered with the graph title (`Untitled Note` if none); clicking it 404s in `getNote` (logged to console) | Dead reference pill |
| Exception anywhere in an async job | `_chat_status[...] = {stage: "Failed", done: true, error}`; user message already persisted; no assistant row | Error bubble; dangling user turn in history |
| Exception in sync `/chat` | 500, stage `"Failed"` (no `done` key) | Caller sees HTTP error |
| Browser closed mid-job | Job continues under the lock and persists the answer | Appears on next load |
| Frontend polls > 10 min | `fail()` client-side; backend unaffected | Error bubble, later the real answer appears after reload |
| Two async chats at once (same or different KB) | Second waits on `_chat_job_lock` showing "Waiting for current chat to finish" | Stage text |
| Sync `/chat` concurrent with an async job or ingestion | No lock — model swap thrash, longer wall time; `LocalLlamaRuntime._lock` prevents corruption | Slow turns |
| Finance query (`looks_like_finance_query`) but Firefly not ready | `answer_finance_question` returns "I can't answer finance questions yet because …" with the note context attached | Answer text (no References block appended on this path) |

## 16. Invariants, locked decisions, and gotchas

**Invariants / contracts other code relies on**

- The `### References` heading + `- [title](/notes/<id>)` line format is parsed by the frontend regex; changing either breaks citation pills.
- Candidate docs must carry `original_obj.name` — every dedup (loop, `_dedupe_docs`, `surfaced_names`) is name-keyed. Two distinct nodes with the same name collapse to one.
- Kuzu is name/type/edges only; **all text comes from Qdrant** (`node_cores.description`, `node_isolated_contexts.content`, `node_relationships.natural_language`). A node without a Qdrant core and contexts can never become a candidate.
- Retrieval detects collection kind by substring (`"relationships"`, `"isolated_context"`) in Qdrant collection names.
- Embedding and reranking are system-wide even with per-KB LLM overrides (shared embedding dims across KBs' Qdrant collections).
- One heavy local model resident at a time; every embed/rerank/chat call may swap. Never call `embedding_service` and `llm_service` "in parallel" expecting concurrency — they serialise on the runtime lock and thrash loads.
- `_chat_job_lock` is process-global: one async chat at a time across all KBs.
- `get_recent_history` must be called before `add_message(user, …)` (it is); the history never contains the current question.
- Stage strings are UI text; `progress_callback` must be cheap and non-blocking (it just writes a dict).

**Gotchas an assistant would get wrong**

- `MAX_LOOP_ITERATIONS = 3` means **two** searches, not three; iteration 1 only plans.
- `graph_expansion` docs are shown to the LLM but never enter `all_docs`/`context`/References (name-dedup against their origin). If you want expansion provenance in citations, key expansion docs by a synthetic name (e.g. `f"{origin}→neighbours"`) or merge their `linked_notes` into the origin doc.
- Meili results are typed `entity_match`, not a keyword type; the source can be told apart via `original_obj._source == "meili"`.
- Vector name variants come from Kuzu without Qdrant enrichment and are usually dropped for lack of text.
- `RERANKER_ENABLED=false` (or a missing reranker) empties retrieval because of `RERANKER_SCORE_THRESHOLD`; the "keyword-overlap heuristic" in the docstring does not exist.
- `reranker_rank` is the pre-sort index, not the final rank.
- `hybrid_search(top_k)` and `retrieve_with_iterative_loop(top_k)` ignore `top_k`.
- `MAX_POTENTIAL_QUESTIONS` is dead; `BENCHMARK_MODE` is alive (in `iterative_step` only).
- `MODEL_RERANKER_LOCAL` is a display string; the reranker file is chosen by the models manifest.
- The `"Gemma4"` model label in stages and the "Gemma3 4B" footer are hard-coded.
- `_chat_status` grows unbounded (each entry keeps the full `result.context`).
- Sync `/chat` bypasses the job lock and never sets `done`.
- `ensure_conversation` silently forks a new conversation for unknown/foreign/deleted ids.
- `rewrite_follow_up_query` uses the OpenAI-style client only; on `gemini`/`anthropic` providers it fails and falls back to the raw query (logged as a warning, not an error).
- Month filter (`period_filter`) excludes ordinary entity cores; day filter searches contexts only.
- `_extract_predicate` assumes stored NL starts/ends with the entity names; other phrasings pass through unchanged and `_build_node_text` may double names.
- `find_nodes_by_name` fuzzy `CONTAINS` also matches note nodes (`kind='note'`), so note titles compete as entities.
- The title auto-set is keyed on the *title* being "New Chat", not on message count.
- Deleting a conversation is soft; `hard_delete_conversation` has no endpoint.
- The export endpoint is broken and the UI swallows the error.
- There is no streaming — "progress" is polling of a dict.

## 17. Extension points / how to modify safely

- **Add a retrieval channel** (e.g. a note-chunk index): add a branch coroutine in `hybrid_search`, include it in the `asyncio.gather`, merge into `entity_nodes`/`vector_nodes` with a new `_source` and a distinct candidate `type`, make sure rows have `node_id`/`id`, `name`, `entity_type`, `summary` so `_get_node_text`, note grounding and `_build_node_text` work; update the type-count log line.
- **Change ranking:** everything funnels through `_apply_reranker_logging`; a fusion score (e.g. RRF over channel scores) belongs there, computed from `original_obj.score`/`_source` before the reranker call, and must still populate `rerank_score`.
- **Change the reranker model/backend:** implement `rerank(query, documents, top_n) -> [{index, relevance_score}]` and swap it in `RerankerService`; keep index-based results.
- **Change the loop policy** (early stop on repeated query, confidence gating, per-iteration budget): `retrieve_with_iterative_loop` between the `iterative_step` call and `current_query = next_q`; keep the `(final_answer, all_docs, thinking)` return.
- **Change the answer protocol:** prompt and parser both live in `LLMService.iterative_step`; the loop only reads `can_answer`, `final_answer`, `next_query`, `full_answer`, `reasoning`, `thinking`.
- **Add per-claim citations:** the LLM currently never sees note ids; you would add `[n]` markers to doc texts in `iterative_step` and map them back in `ChatWorkflow.chat` — and update the frontend regex.
- **Add streaming:** replace the status dict with an SSE/WebSocket endpoint fed by the same `progress_callback`; keep `_chat_status` for the existing poller.
- **Add cancellation:** keep the `asyncio.Task` from `_chat_tasks` addressable by `request_id` and `task.cancel()`; note the LLM/embedding calls run in threads and will not stop mid-call.
- **New stage strings:** free-form; the UI shows them verbatim. Keep them short.
- **Evict job status:** add a TTL sweep over `_chat_status` keyed by completion time; keep entries at least until the client's next poll.
- **New config knob:** add to `Settings` in `core/config.py`, document in `backend/.env.example` and [Configuration reference](21-configuration-reference.md).

## 18. History and rationale

| When / commit | Change | Why (from commit messages and `Results/`) |
|---|---|---|
| 2026-01-23 `6852528` | "Graph-first hybrid retrieval": 4-phase pipeline (temporal anchors → graph consensus → grounding → semantic fallback), unified vector index of distilled nodes | First move away from chunk RAG to node-summary evidence |
| 2026-02-01 `033589d` | Bi-temporal relationship fields + **symbolic ranking** replacing neural rerank; Community nodes | Hand-tuned boosts (`boosts`, `symbolic_immune` — still visible in `_log_retrieval_details`) |
| 2026-02 → 03 | **Sub-Questions approach**: decompose into 2–4 sub-questions, per-sub-query retrieval, verification, synthesis (Neo4j era) | HotPotQA gemma3:4b ≈ 80 s/question; `MAX_POTENTIAL_QUESTIONS` dates from here |
| 2026-03-15 `24459f8` | **Looping approach**: `retrieve_with_self_correction` agentic loop replaces decomposition; neighbour expansion removed | 62 % faster (30 s vs 80 s), recall up with richer graph; report recommends re-adding *targeted* 1-hop expansion on later iterations |
| 2026-04-18 `559e988` | **Joint approach**: iterative refinement, graph-expansion retrieval, reranker service, Qdrant service, feedback (later removed) | Re-introduces expansion, now reranked |
| 2026-04-19 `097c822` | Reranker runs early, cut to top-20; handles v2/v3 response formats | Reduce LLM input size |
| 2026-05-07 `68494b7` | **Final Implementation**: Kuzu + Typesense migration; loop ≤ 10 iterations | Gemma 4 E4B: EM 58 %, Fuzzy 81 %, 212 s/question (`Results (Final Implementation)`) |
| 2026-05-17 | "After Optimizations" run | EM 62 %, 231 s/question, wall clock −26 % (`Results (After Optimizations)`) |
| 2026-05-18 `8eba91d` | Benchmark mode removed from ingestion/docs; chat fallback message updated | (`BENCHMARK_MODE` survives in `iterative_step`) |
| 2026-05-18 `d37abd6` | Multi-KB: per-KB `RetrievalService`/`ChatWorkflow` via `KBContext` | KB isolation |
| 2026-06-12 `4408a72` | Async chat + polling (`/chat/async`, `/chat/status`), NEXT_QUERY cleaning, unlabeled-query fallback, Qdrant per-collection filters + `day_only` | Long local turns exceeded browser/proxy timeouts |
| 2026-07-02 `ac7f4e4` | Persistent conversations (then Postgres, now SQLite) + follow-up rewrite using recent history | Multi-turn chat |
| 2026-08-02/03 `3f21e08`, `fbcafe7` | Docker-free desktop: in-process GGUFs, Meilisearch replaces Typesense, `_chat_job_lock` serialises jobs on the event loop, `MAX_LOOP_ITERATIONS` lowered to 3 | Desktop UX; one resident model; SQLite sessions bound to one loop |
| 2026-08-06/07 `b84ca73`, `8de5cda` | Concurrent entity/BM25/vector search, batched Kuzu/Qdrant lookups, `analyze_query` day-cache; frontend scans only the 5 most recent messages with id-keyed cache and abortable requests | Fewer round trips and model swaps |
| working tree (2026-09) | Per-KB LLM override (provider + chat/ingestion model), `model_load_clock` timing line, dynamic output budget / `PromptTooLongError`, per-KB chat GGUF switching, `require_ai(kb)` | Attribute wall time to loads vs inference; let a KB pin a stronger/cloud model |

Open discrepancies noted while writing (also listed in the report): dead `MAX_POTENTIAL_QUESTIONS`; live `BENCHMARK_MODE`; docstring-only keyword fallback in `_apply_reranker_logging`; unused `_get_node_relationships`; `neighbor_node` type in logs; "person entities" comment on the untyped vector-variant expansion; `retrieve_with_self_correction` docstring referencing removed sub-question functions; broken chat export; hard-coded "Gemma4"/"Gemma3 4B" labels; `rewrite_follow_up_query` lacking Gemini/Anthropic branches.
