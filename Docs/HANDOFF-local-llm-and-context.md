# Handoff: how Orb uses the local LLM and its context window

For a coding agent picking up work on Orb. State as of 2026-09-21, main at `3bd480f`.
Every claim below names the file it lives in; verify before changing behaviour.
Deeper references: `Docs/12-local-models-and-inference.md`, `Docs/13-llm-providers-and-prompting.md`,
`Docs/10-ingestion-pipeline.md`, `Docs/16-retrieval-and-chat.md`.

## 1. The shape of it

- All local inference is **in-process llama.cpp** (`llama-cpp-python`) over GGUF files. No Ollama, no
  LM Studio, no HTTP server. `backend/app/services/local_models.py` owns it: `LocalLlamaRuntime`
  (chat + embed), `LocalGgufReranker`, and `LocalOpenAICompat`, a shim that makes the runtime look
  like an OpenAI client so `LLMService` has one call path (`LLMService._chat` in `services/llm.py`).
- **One heavy model is resident at a time.** Loading chat unloads embed/reranker/multimodal and vice
  versa. A single chat turn therefore swaps embedder → reranker → chat model. Never call embedding
  and chat "in parallel": they serialise on the runtime lock and thrash loads.
- **One model for chat and ingestion.** `LLMService.get_ingestion_model()` is
  `self._ingestion_model_override or self.get_chat_model()`. The model picked on the Models page
  drives both. The only exception is a workspace that ticked "use a different model for note
  ingestion" (`knowledge_bases.llm_ingestion_model`). The old `INGESTION_*` settings are deleted; do
  not reintroduce them.
- Cloud providers (OpenAI, Gemini, Anthropic, Hugging Face, any OpenAI-compatible URL) are per
  workspace and go through the same `_chat`. Keys live in the OS keychain (`services/credentials.py`).

## 2. Configuration: no environment variables, no `.env`

Every tuning knob is a `Settings` field (`core/config.py`), edited in the app under
**Models → Local runtime**, saved to `DATA_DIR/runtime_config.json`
(`core/runtime_config.py`, `LOCAL_RUNTIME_KEYS`) and served by
`GET`/`PUT /api/v1/settings/local-runtime` (`api/settings.py`, range-checked). Saving unloads the
models so the next request reloads with the new values. `null` means automatic.

| Setting | Default | Meaning |
|---|---|---|
| `LLAMA_N_CTX` | 16384 | chat context window (KV cache size) |
| `LLAMA_SWA_FULL` | true | full-size cache on sliding-window layers |
| `LLAMA_FLASH_ATTN` | false | flash attention |
| `LLAMA_MAX_TOKENS` | null | hard output cap; null = size per call (see §3) |
| `LLAMA_PROMPT_RESERVE` | 4096 | only used with an output cap, as a floor for `n_ctx` |
| `LLAMA_BACKEND` / `LLAMA_N_GPU_LAYERS` / `LLAMA_N_THREADS` | auto / null / null | acceleration |
| `LLAMA_REPEAT_PENALTY` | 1.12 | sampling |
| `EMBED_N_CTX` / `RERANK_N_CTX` | 8192 / 8192 | embedder and reranker context |
| `MODEL_IDLE_SECONDS` | 300 | unload after idle; 0 = never |
| `EXTRACTION_CHUNK_TOKENS` | null | pin the extraction chunk size; null = learned (§4) |
| `LARGE_ATTACHMENT_TOKENS` | 20000 | a document over this is summarised for the graph and indexed in full for search (§5) |

The `ORB_*` environment variables that remain (`ORB_DATA_DIR`, `ORB_MODELS_DIR`, `ORB_PATHS_FILE`,
download staging, build pins) are process plumbing between the Tauri shell, the Python runtime and
the build script. They are not user settings. Do not add new env-var knobs: add a `Settings` field,
a `LOCAL_RUNTIME_KEYS` entry, a field on `LocalRuntimeSettings`, and a row in
`frontend/src/app/models/_components/LocalRuntimeCard.tsx`.

The owner's machine (24 GB M3) currently runs **32768 context, `LLAMA_SWA_FULL` off, flash attention
on**, with Gemma 4 E4B. The defaults above are unchanged.

## 3. The context window: how it is used

- **`n_ctx` is a global setting, capped per model at load.** `_clamp_ctx_to_model` reads the GGUF's
  trained context and lowers `n_ctx` for that load if the setting exceeds it. It never raises it and
  never rewrites the setting. Both Gemma 4 GGUFs in use are trained for 131072.
- **Output is sized per call, not capped.** `LocalLlamaRuntime.create_chat_completion` computes
  `_remaining_output_budget(messages)` = `n_ctx − estimated prompt tokens − 32`, and uses that as
  `max_tokens` (or `min(cap, budget)` if `LLAMA_MAX_TOKENS` is set). A fixed cap used to truncate
  long extractions mid-JSON; do not bring one back. An image part is charged 1200 tokens.
- **A prompt that leaves fewer than 256 tokens raises `PromptTooLongError`** with the numbers in the
  message. This is deliberate and must stay loud: `LLMService.iterative_step` re-raises runtime
  errors, the chat job stores them in its `error` field, and the UI shows them. Only a JSON parse
  failure is swallowed into an empty research step. The same applies to the reranker: it is
  mandatory, and a missing GGUF, a load failure or a run with no scores raises.
- **Sliding-window cache (`swa_full`).** Gemma interleaves global-attention layers with
  sliding-window layers. With `swa_full` on, every layer keeps the full context in the KV cache,
  which is the largest memory cost at load; with it off, sliding-window layers keep only their
  window. Attention results are the same either way; what is lost is the ability to rewind past the
  window (prompt-cache reuse, context shift), which Orb barely uses because it sends a fresh prompt
  per request and swaps models between steps. **Caveat:** the docstring of
  `_llama_metal_safe_kwargs` records that an older build saw "ordinal / 'or the' repetition
  collapse" on Gemma 4 with the compact cache. On the current build, a twelve-note batch including
  a 75k-token note showed no repetition in 1,178 extracted descriptions. If output ever loops,
  turn `swa_full` back on and drop to 16k before looking elsewhere.
  `create_chat_completion` also retries up to three times when it detects that cascade.
- **Flash attention** computes the same attention in blocks without materialising the score matrix:
  less compute-buffer memory, faster prompt processing, identical results. `_construct_llama`
  retries without `swa_full`/`flash_attn` if the library rejects them.
- The load log line reports the real values:
  `Loading chat GGUF in-process (metal, n_gpu_layers=-1, n_ctx=…, swa_full=…, flash_attn=…)` in
  `DATA_DIR/logs/llm.log`.

## 4. How ingestion spends the window

`backend/app/workflows/agents/ingestion_agent.py`, `workflows/extraction_chunking.py`,
`services/extraction_budget.py`.

- `LLMService.ingestion_context_tokens()` returns `LLAMA_N_CTX` for local models (128000 for cloud).
- **Chunk budget** = `min(learned ceiling, (context − prompt overhead − 64) / 3.5)`, floored at 400.
  Extraction emits about 2.5× its input as JSON, so what binds is the model's *output* stamina, which
  no API reports. `extraction_budget` learns it per model: starts at 4000, shrinks to 0.6× the size
  that truncated, grows 1.3× after three clean full chunks, hard ceiling 32000. Stored in
  `DATA_DIR/extraction_budgets.json`. `EXTRACTION_CHUNK_TOKENS` pins it and disables learning.
- **A note under the budget** is extracted in one call.
- **A note over the budget but inside the context** is extracted **by task, not by text**
  (`_extract_task_split`): three whole-document passes (entities, then relationships against the
  entity list, then a description per entity), so every pass sees the entire note and cross-note
  references survive. This is why a larger `n_ctx` helps ingestion more than chat.
- **A note too large for the context** falls back to paragraph-bounded chunks
  (`_extract_by_chunks`) run one after another and merged. A 75k-token note took 24 chunks and over
  three hours on the E4B. That is what §5 exists to prevent.
- All extraction calls use `json_mode=True`: llama.cpp's JSON grammar locally,
  `response_format=json_object` on OpenAI-shaped providers, `application/json` on Gemini, prompt-only
  on Anthropic. **Never pass a JSON schema to a local model**: schema-constrained sampling empties
  nested arrays on small GGUFs. `_clean_json` is fence-unwrap + curly quotes + `json_repair`, nothing
  more. Relationship types are a closed vocabulary (`RELATIONSHIP_TYPES`, default `related_to`).
- Model calls inside an ingest are checkpointed per note (`services/ingestion_checkpoint.py`), so a
  failed ingest resumes from the call that broke instead of starting over.
- Re-ingesting a note is idempotent: `GraphService.clear_note_contribution` takes back what the note
  asserted before the new extraction is written, and orphaned entities are swept afterwards.

## 5. Recordings and long documents: notes, not a full graph

An attachment's extracted text goes into the note as a delimited block:
`<!-- orb:extract src="…" -->` … `<!-- /orb:extract -->`, graphed in full. The one variant is
`mode="notes"`: the usual header line, a model-written summary, then the full text under
`## Transcript` (recordings) or `## Full text` (documents). Nothing is asked — an earlier version
parked big attachments and showed a graph / summarize / index prompt; that prompt, its
`pending` / `index` / `summary` modes, `notes.attachment_modes` and
`PUT /notes/{id}/attachments/mode` are gone (`core/database._sqlite_repairs` drops the column).

- `finish_attachment(kind, section, filename, llm, set_status)` (in `ingestion_agent.py`) is the one
  place the rule lives. A full ingest and the editor's per-attachment action
  (`api/notes._run_attachment_job`) both call it.
- **Recordings** (`audio`, `video_audio`) always become notes, whatever their length:
  `LLMService.summarize_transcript` reads `asr_engine.untimed(text)` — the transcript without its
  `[MM:SS]` stamps, which cost about half as many tokens again — and writes a title, a flow summary,
  three to five topic sections, next steps by speaker and decisions. Stage `Writing notes for <file>`.
- **Any other non-image attachment** becomes notes only when its text is over
  `LARGE_ATTACHMENT_TOKENS` (ingestion-model tokens): `LLMService.summarize_document`, plain prose.
  Stage `Summarising <file>`. Smaller ones are graphed in full with no summary call. Images never.
- Both summarisers share `_pieces`: paragraph-bounded pieces of about half the ingestion context,
  summarised one by one and then merged, so a text larger than the window still fits.
- `graph_text(content)` reduces every notes block to its summary part (`split_notes`) before
  anything reaches entity extraction, `_write_ontology` or `_update_neighborhoods`.
- The full text goes to `IngestionWorkflow._index_documents`, which creates ONE node
  (`type='document'`), splits the text into ~1200-character passages, embeds them and stores them in
  the *contexts* collection under that node. No LLM call. Retrieval needs no special case: vector
  hits on contexts accumulate only the matching passages into the candidate's text. The node's
  description is a fixed one-liner on purpose, so a match by name does not pull every passage into
  the prompt.
- Blocks written before this (plain, no mode) stay graphed in full until the attachment is redone
  from the editor; nothing migrates them.

## 6. How chat spends the window

`workflows/chat.py`, `services/retrieval.py`, `services/chat_store.py`, `schemas/chat.py`.

- History is a **fixed window plus a rolling summary**. The last `CHAT_HISTORY_MAX_MESSAGES` (24)
  messages are prompted verbatim, each cut at 500–600 characters. After every answer,
  `chat_store.refresh_summary` folds the messages that just left the window into
  `chat_conversations.summary` with one call to `LLMService.summarize_conversation` (under 200
  words). `schemas/chat.render_history` is the single renderer for both consumers: the follow-up
  rewrite and the `CONVERSATION SO FAR:` block of the reasoning loop.
- Retrieval is a bounded loop (`MAX_LOOP_ITERATIONS=3`): analyse → hybrid search (entity names,
  Meilisearch BM25, Qdrant vectors) → mandatory rerank → reasoning step, which either answers or
  asks for another query. Evidence docs, not history, are what could overflow the window; when they
  do, the user sees the `PromptTooLongError` text.
- `retrieval.fold_vector_hits`: when a node is found by keyword **and** by vector search, the
  matched sentences from the vector hit are folded into the node already found. A keyword hit has no
  text of its own; before this, such a node dropped out of the candidates entirely.
- Community detection and temporal digests are **never** automatic. Only the Setup page button
  (`POST /admin/rebuild-communities`, then `/admin/build-temporal-digests`) runs them.

## 7. Rules of the road

- Fix causes, not symptoms: no regexes or normalisers that paper over bad model output or bad stored
  data. Fix the prompt or the writer, and add a one-time migration for existing data
  (`vault_sync.migrate_vault_files`, `core/database._sqlite_repairs`, `main._migrate_stores`).
- No testing-only switches, no silent fallbacks. If a required model is missing, raise.
- The owner wants to be consulted at major decision points (product behaviour, data migrations,
  anything destructive). Routine implementation choices are yours.
- Verify with: `pytest tests/unit` (554) and `pytest tests/integration` (7, separate process) from
  `backend/` with `DATA_DIR` pointed at a scratch folder; `npx tsc --noEmit`, `npm run lint`,
  `npm test`, `npm run build` in `frontend/`; `cargo check` in `desktop/src-tauri/`. CI runs all of
  it on every push.
- Local app loop: `python3 desktop/build.py prepare && python3 desktop/build.py dist`, then replace
  `/Applications/Orb.app` with `desktop/src-tauri/target/release/bundle/macos/Orb.app`. Before
  replacing it, check `GET /api/v1/admin/maintenance-status` for running ingests, and make sure no
  old backend still holds port 17401: the shell attaches to any healthy API it finds there.
- Releases: `python3 desktop/build.py bump X.Y.Z`, commit, tag `desktop-vX.Y.Z`, push. CI builds
  four platforms and drafts the GitHub release.
