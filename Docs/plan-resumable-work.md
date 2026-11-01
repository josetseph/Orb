# Plan: every long task resumes from where it stopped

## The principle (applies to all future work, not only this change)

Any work that takes longer than a moment (ingesting, transcribing, extracting a PDF, answering, re-indexing,
migrating, downloading) must survive a crash, a quit, a power cut or a failed model call, and **continue by
itself from the last finished step** the next time the app runs. Nothing finished is ever done twice, and the user
never has to find and restart broken work by hand.

The benchmark harness already works this way (`backend/tests/benchmark`: content-keyed model-call cache, saved
snapshots, sweeps that skip finished variants). The app should follow the same rules:

1. **Write down the work before starting it.** A durable record of each job exists *before* its first step runs.
   An in-memory queue is not enough.
2. **Split work into steps, and save each step's output as soon as it finishes.** Saved outputs are keyed by
   everything that decides them (model, prompt, settings, input content). If an input changes, the key changes,
   and only the steps it affects run again.
3. **Make every write safe to repeat.** Rerunning a step after a crash must produce the same state as running it
   once: deterministic IDs, plus delete-then-write per note.
4. **Resume on start.** At startup the app finds unfinished jobs and continues them. It does not mark them failed.
5. **Save successes only.** Following the existing no-patchwork rule: a malformed or refused model reply is a
   failure. It is never cached and never repaired, and a resume retries that step from scratch. Usage limits and
   connection errors are waited out and retried. A step that keeps failing marks the job failed and shows the
   reason. It does not loop forever.
6. **Show the resume.** The UI says "Resuming: step 7 of 12" (or similar), not just "Processing".

Please add this principle to the project's contributor docs (a short section in `Docs/` and in an agent
instructions file, e.g. `CLAUDE.md`), so every future feature is built to it.

## What exists today (main, 1.2.0)

- **Per-note model-call memo during ingestion:** `backend/app/services/ingestion_checkpoint.py`. Calls for a note
  are saved under `DATA_DIR/ingestion_cache/<kb>/<note>/`, keyed by model + temperature + prompt. A retry replays
  the calls that succeeded. The folder is deleted when the note completes.
  - Gap: other model calls (answering, multimodal, ASR, summaries) don't go through it.
- **Re-ingest replaces, not appends:** `backend/app/workflows/ingestion.py` around lines 940–1020 drops the note's
  earlier contexts on each node before writing new ones. This is the basis for repeatable writes. Check that every
  store (SQLite graph, Qdrant, Meilisearch, relationships) follows the same pattern.
- **Startup marks interrupted notes as failed:** `backend/app/main.py` around lines 84–116 sets any note left
  mid-pipeline to `failed=True, processing_stage="Ingestion failed (interrupted)"`. The user has to re-ingest by
  hand. This is the main thing to change.
- **The queue lives in memory:** `backend/app/api/notes.py` uses `BackgroundTasks` and `asyncio.create_task`, and
  `ingestion.py:147` uses `create_task`.
  - Notes shown as "Queued for ingestion" are probably never picked up after a restart. Verify this first.
  - `vault_watcher.py` resets "Queued"/"Starting" to "Saved" when a file changes, which also drops them.
  - Attachment jobs (`notes.py:340`) are in-memory tasks too.
- **Downloads already resume:** `.partial` files are kept and later downloads join them (commit 6c139c2). Treat
  this as the reference behaviour.
- **Store migrations are deferred:** "Store migration v1 ... deferred to next start". Check that a migration
  stopped halfway resumes correctly and is never applied twice.

## Design

### 1. A durable job table

A SQLite table (e.g. `jobs`) in the existing app database:

| column | meaning |
|---|---|
| `id` | job id |
| `kind` | `ingest_note`, `transcribe`, `extract_pdf`, `answer`, `reindex`, `migrate`, `download`, ... |
| `kb_id`, `target_id` | what it works on (note id, chat turn id, model id...) |
| `input_hash` | hash of what decides the result (note content, settings that matter). A changed input means a new job; the old one is superseded. |
| `state` | `queued`, `running`, `done`, `failed`, `cancelled` |
| `step`, `steps_total` | progress, for the UI and for resuming |
| `attempts`, `last_error` | for the retry limit and for showing why it failed |
| `created_at`, `updated_at`, `heartbeat_at` | `heartbeat_at` tells a job that is still running from one a crash left behind |

- Enqueue = insert a row (in the same transaction as the note's status change). Then wake the worker.
- **One worker drains the table** in a fixed order. It replaces the scattered `create_task`/`BackgroundTasks`
  calls. It keeps the existing concurrency limits (extraction slots and so on).
- **At startup:** every `queued` job is drained. Every `running` job whose heartbeat is stale resumes from its
  saved step. This replaces the "mark failed (interrupted)" block in `main.py`.
- `cancelled` is final. Cancelling deletes that job's saved steps, the way `ingestion_checkpoint.clear` does now.

Keep it small. One table and one worker loop, no job-framework dependency.

### 2. Save each step's output

- **Model calls:** widen `ingestion_checkpoint` into a single call memo used by *every* model call made inside a
  job: extraction, entity resolution, summaries, multimodal, query analysis, answer synthesis.
  - The key stays content-based (provider, model, messages, the settings that shape the reply), as in the
    harness's `LLMService._chat` key in `orb-testing-2/backend/app/services/llm.py`. Only successful replies are
    stored.
  - Delete the memo when its job is `done` (as now), so the cache never grows without bound.
  - Optional later: keep it on until the next successful re-ingest, so changing one setting re-runs only the
    affected calls.
- **Non-model steps** save their output next to the job, keyed the same way:
  - **Transcription:** finished audio segments are saved as they complete. A resume starts at the first
    unfinished segment.
  - **PDF / attachment extraction:** finished pages are saved as they complete.
  - **Embeddings:** saved vectors are written to the stores as part of the note's write step. A rerun recomputes
    only the missing ones (or reads them from the memo).
- **Writing to the stores** is the note's last step, and it must be repeatable: delete what this note wrote
  before, then write. Use deterministic IDs derived from (note id, entity, context index), never random ones,
  so a half-finished write followed by a rerun leaves no duplicates or orphans.

### 3. Answering (chat)

- Save the user's question and create the answer job before any retrieval starts.
- Save each agent-loop step as it finishes: the search queries, the retrieved and reranked ids, the tool results,
  the reasoning step.
- Save the streamed answer text as it grows, at least every few hundred tokens.
- After a crash, or when the window is closed mid-answer, the chat shows the partial answer with "Resuming…". It
  continues from the last saved loop step, replaying saved model calls from the memo instead of paying for them
  again.
- If the knowledge base changed since the step was saved (a new `input_hash`), restart that answer from the top
  and say so.

### 4. Other long work to bring under the job table

- Re-indexing after an embedder change. This is coming next: the switch to EmbeddingGemma 2 needs every vault
  re-embedded once, so build that re-embed as a resumable job from the start.
- Store migrations.
- Vault sync / bulk import.
- Attachment jobs (`notes.py:340`).
- Model downloads: keep the current behaviour, but list them in the job table so the UI has one progress list.

## How to prove it works

Add one test file (e.g. `backend/tests/test_resume.py`) with a **kill-and-resume test** for each job kind:

1. Run the job once without interruption and record the final state: graph nodes and relations, Qdrant points,
   Meilisearch docs, chat answer.
2. Run it again from empty. Kill the worker at a random step (or raise inside a chosen step). Restart, and let it
   resume.
3. Assert the final state is identical to step 1, and that no model call was made twice. Count calls through the
   memo: the calls made in total over the interrupted run and its resume must equal the calls made in step 1.

Use a fake model client with fixed replies so the test is fast and free. One parametrized test over the job kinds
is enough.

## Order of work

1. Add the job table, the worker and resume-on-start for **note ingestion**. Remove the "mark failed
   (interrupted)" reset, and make the queue durable.
2. Widen the call memo to every model call inside a job. Add saved segments for transcription and pages for PDF
   extraction.
3. Make store writes repeatable (deterministic IDs, delete-then-write), and add the kill-and-resume test.
4. Answering: save each loop step and the partial answer; resume in the chat UI.
5. Re-embed, migration, sync and attachments as jobs. UI: one progress list with "Resuming" labels.
6. Add the principle to the contributor docs and the agent instructions.

## Out of scope / don't

- Don't repair or normalise model output on resume (no-patchwork rule).
- Don't add a job-queue library or a second database. SQLite and one worker loop are enough.
- Don't resume a job whose input changed. Supersede it with a new one.
