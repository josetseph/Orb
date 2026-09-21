# Testing branch notes

The living record for `orb-testing`: what is dangerous, what is true about the pipeline
that an experiment depends on, how to measure honestly, and what has been learned.
Update it in the same commit as the change or finding it describes. Newest log entry first.
If something here stops being true, fix or delete it; do not leave it to rot.

## 0. The rule

Set by the owner on 2026-09-21. It governs every pipeline change made on this branch.

**No regex or patchwork implementations. No normalisation. Output from the model is not "fixed".
Everything must work with minimal effort to correct it.**

What that means in practice: a variant is judged on what the model actually produced. If a reply does not parse,
names an entity that was not listed, or uses a predicate outside the vocabulary, that is a failure of the
model-and-prompt pair and is counted as one. It is not repaired, coerced, fuzzy-matched or silently dropped.
The way to get valid output is to ask for it better (prompt, schema, output format, model), not to clean it up after.

How the rule is enforced (branch `orb-testing-strict`, 2026-09-21):

- Every structured reply goes through `services/model_output.parse`: `json` + the schema, as written. A reply that
  does not parse or fit raises `ModelOutputError`, is recorded in the request trace (`invalid_output`) and appended to
  `DATA_DIR/logs/invalid_model_output.jsonl` with stage, model, reason and the raw text. The count is a result.
- Replies are constrained to valid JSON while they are generated (`json_mode`: llama.cpp JSON grammar, OpenAI
  `response_format`, Gemini `response_mime_type`). Constraining to a full schema is not used: a code comment in
  `local_models.py` records that it emptied nested arrays on small models. Untested since; a candidate lever.
- Schemas have required fields and no `before` validators. Predicates are a `Literal` of the closed vocabulary.
  A relationship or description must name an entity exactly as listed. Chunk extractions merge on the exact name.
- `EXTRACTION_ATTEMPTS` (default 1) may ask again; it never repairs. A truncated reply on an unsplittable chunk fails.
- A research step must set exactly one of `answer` / `next_query`; an answer such as "INSUFFICIENT" is returned as the
  answer, not reinterpreted. An unusable step reply is a counted step that produced nothing.

Removed: `_clean_json` and the `json-repair` dependency, all tolerant validators and shape unwrapping, predicate rewriting
to `related_to`, `match_entity_name`, the regex sentence fallback that invented an entity description when the model gave
none (`sentences_about`), the fall-back from task-split to chunking when the entity pass returned nothing, the swallowed
task-split passes, community-name fit check with its two retries and invented fallback name, the graph layer's predicate
sanitiser, every regex in the pipeline packages, and (as attachment code) the extraction-block markup and vault migration sweep.

Still to decide, not LLM output but in the same spirit: the graph stores and looks up entities by lower-cased name, so
"Ama" and "ama" from two notes become one node. That is entity resolution across notes and is a lever to test, not a repair.

The harness scorer normalises answers (`normalize_answer`, first-line extraction, fuzzy match). That is the published
HotpotQA metric and the owner chose to keep it.

## 1. Hazards

| Hazard | What happens | Guard |
|---|---|---|
| Desktop `paths.json` (`~/Library/Application Support/Orb/paths.json`) points `data_dir` at the real app data and `default_vault_path` at the real notes vault. The backend binds both at import, and the file beats the `ORB_DEFAULT_VAULT` env var. | Setting only `ORB_DATA_DIR` is not enough: a fresh data dir still creates its default KB on the real vault. A note created there lands in the real journal. This happened once (2026-09-20, one 49-byte smoke note, removed). | `backend/run.py` pins `ORB_PATHS_FILE` and `ORB_DATA_DIR` to the repo and refuses to start if any KB vault is outside the data dir. `backend/conftest.py` pins test runs to a temp dir. Never run bare `uvicorn app.main:app` or `import app.main` here without both env vars. |
| `MODELS_DIR/manifest.json` holds the model selection, and the desktop app reads the same file. | Downloading or selecting a model through a backend pointed at the shared models dir switches the desktop app's model. | `run.py` uses `<repo>/models/` with its own manifest; `gguf/` is a symlink to the shared weights. Switch models per run with a KB pin (`experiment.py --chat-model`), which is stored in the data dir. |
| A downloaded embedding model that differs from the snapshot's. | `select-chat-model` resizes Qdrant collections to the embed model's dimensions. Vectors in a snapshot are only valid for the embed model that built them. | Every result file records `config.selection` (chat, embed, reranker ids). Keep embed and reranker fixed across runs you intend to compare. |
| Snapshots restored anywhere but `<repo>/data`. | The KB registry stores absolute vault paths; a snapshot elsewhere points back at the original location. | `experiment.py` only ever restores into `<repo>/data`. |
| A backgrounded process inherits `SIGINT = ignore`. | `kill -INT` on a runner that has not installed handlers does nothing; it keeps the Kuzu lock and the ports. | `run.py` installs handlers first thing. If a run will not start with "Could not set lock on file", look for a stray `run.py`. |
| `POST /api/v1/setup/select-chat-model` on its own. | It rewrites the selection but keeps a file path only for a model whose id did not change, so switching to an already-downloaded model leaves no chat path and every AI route returns 503. | Use `/api/v1/setup/download-models`: it records paths and only downloads what is missing. `experiment.py` does this and checks `configured` before doing any work. |
| Kuzu is single-writer. | Two backends on one data dir: the second fails at import. | One experiment at a time. |

## 2. How this branch is built

`backend/` is `main`'s backend with product-only code cut out. It is not a fork that merges.
To resync after a small gap, apply main's changes as a three-way patch:
`git diff <last-sync> origin/main -- backend > p.patch && git apply --3way --exclude=<files cut here> p.patch`.
Conflicts only appear where `main` touched code this branch removed; keep the branch side, then cut whatever
attachment, finance or desktop code the patch added cleanly elsewhere (lint for unreferenced functions, drop tests
for cut routers). After a large gap, take `main`'s `backend/` wholesale and re-cut. Last synced to `main` at `3bd480f`.

- **Branch-only files:** `backend/run.py`, `backend/conftest.py`, `backend/tests/benchmark/`,
  `backend/app/api/benchmark.py`, `backend/app/services/trace.py`, `backend/.env.example`, `Results/`, this doc.
- **Branch-only edits inside shared files:** `BENCHMARK_MODE` in `core/config.py` and `services/llm.py`
  (rules swap inside `iterative_step`), trace hooks in `services/retrieval.py`, `trace` flag on `ChatInput`,
  one-shot `/api/v1/chat` with no history.
- **Cut:** desktop runtime, Firefly finance, all multimedia (ASR, vision, Marlin, attachment extraction,
  the multimodal ingestion stage), chat history and async chat, 3D graph, settings, files and vault routers,
  vault watcher, static UI mount, their tests and docs.
- **Kept on purpose:** the credentials router and service (only way to add a cloud key; the app has no env
  fallback, so `run.py` seeds keys from `*_API_KEY`), `utils/graph_layout.py` (ingestion calls it),
  `wrap_legacy_enrichment_blocks` (vault sync calls it), the unit suite (safety net for tweaks).
- Settings do not read `.env`. `run.py` loads `backend/.env` into the environment itself.

## 3. Pipeline facts an experiment depends on

Retrieval and answering (`services/retrieval.py`, `workflows/chat.py`, `services/llm.py`):

- The loop is `retrieve_with_iterative_loop`. Iteration 1 only plans the first query, so
  `MAX_LOOP_ITERATIONS = N` means at most `N - 1` searches. The default is 3: two searches.
  The archived benchmark runs allowed up to 10. This is the first knob to test on multi-hop sets.
- Every candidate passes through one choke point, `_apply_reranker_logging`: all candidates are scored,
  sorted, cut to `RERANKER_TOP_K` (10), then cut by `RERANKER_SCORE_THRESHOLD` (0.05).
  Graph-expansion candidates go through it too but are not thresholded.
- `hybrid_search` is entity-first: entity-name match is the entry point, vector and keyword search are the
  fallback. Other knobs: `VECTOR_PRE_RERANK_THRESHOLD` 0.45, `GRAPH_EXPAND_TOP_NEIGHBORS` 10,
  `GRAPH_EXPAND_SCORE_THRESHOLD` 0.
- The evidence returned with an answer is deduped by node name and cut to the 6 best by rerank score.
  That cap is a literal in `ChatWorkflow.chat`, not a setting. It shapes citations and the retrieval
  metric, not what the model read at each step.
- Each step is one model call returning JSON (`reasoning`, `finding`, `answer`, `next_query`).
  `BENCHMARK_MODE=true` swaps in the HotPotQA/MuSiQue reasoning and output rules and asks for the bare fact.
  Without it answers are full sentences and exact match collapses.
- Local runtime knobs are Settings fields now, so `--set` reaches them: `LLAMA_N_CTX` (16384), `LLAMA_FLASH_ATTN`,
  `LLAMA_SWA_FULL`, `LLAMA_REPEAT_PENALTY`, `LLAMA_PROMPT_RESERVE`, `EMBED_N_CTX`, `RERANK_N_CTX`,
  `MODEL_IDLE_SECONDS` (300; 0 keeps models loaded), `EXTRACTION_CHUNK_TOKENS` (learned per model when unset).
- Local inference loads one heavy model at a time (chat, embed, reranker swap in and out). A single
  retrieval iteration is at minimum chat, embed, rerank, chat. Model swaps dominate latency on small RAM.
- Chat response shape: `answer`, `sources` (`{id, title}` of cited notes), `context` (docs; `linked_notes`
  are bare ids), `thinking`, and `trace` when requested.

Ingestion (`workflows/ingestion.py`, `workflows/agents/ingestion_agent.py`):

- Three stages in order: extraction, storage, summarisation. No LangGraph; `run_ingestion_agent` is a plain loop.
- Community detection (Leiden) and temporal digests no longer run after ingestion. Since `main` `52041d8` they run
  only on request (`POST /api/v1/admin/rebuild-communities`, `.../build-temporal-digests`). `experiment.py --communities`
  triggers the rebuild after ingest and times it; without the flag an index has no community summaries.
  Evaluate or snapshot only after `GET /api/v1/benchmark/idle` reports idle; `experiment.py` waits for it.
- Ingestion runs on the chat model unless the KB carries its own ingestion pin (`main` `ab5419a`);
  `experiment.py --ingestion-model` sets that pin. An index built by one ingestion model can be queried by
  any chat model, which is what makes snapshots worth keeping.
- A node found by both keyword and vector search used to lose its matched text and drop out of the candidates
  (`main` `5b260ee`, `fold_vector_hits`). Any result recorded before that fix under-reports recall.

## 4. The harness

| Tool | Use |
|---|---|
| `tests/benchmark/experiment.py` | One run end to end: restore snapshot, boot, pin models, ingest, wait idle, evaluate, snapshot, stop. |
| `tests/benchmark/compare.py` | Pair runs per question. First file is the baseline. |
| `tests/benchmark/replay.py` | Offline: where questions are lost, and a sweep of top-k, threshold, context cap. |
| `tests/benchmark/synthesis.py` | Re-answer from a run's frozen evidence with another model. |
| `tests/benchmark/plan.sh` | The ordered list of experiments. Skips finished runs, so it is safe to restart. `QUESTIONS`, `DATASET`, `PYTHON` env vars. |
| `evaluate.py`, `prepare_dataset.py`, `fetch_notes.py` | The manual loop; `--questions N` ingests only the notes the first N questions use. |

Switches, all defaulting to the app's behaviour. Flags of `experiment.py`: `--provider`, `--chat-model`,
`--ingestion-model` (KB pins), `--communities` (rebuild summaries; works on a restored snapshot), `--download`
(fetch missing GGUFs). Settings via `--set`: `MAX_LOOP_ITERATIONS`, `RERANKER_TOP_K`, `RERANKER_SCORE_THRESHOLD`,
`CHAT_MAX_CONTEXT_DOCS` (branch-only, was a literal 6), `EMBED_MODEL_ID` and `RERANK_MODEL_ID` (branch-only;
catalogue ids overriding the RAM-tier pick), `VECTOR_PRE_RERANK_THRESHOLD`, `GRAPH_EXPAND_*`, the `LLAMA_*` runtime knobs.
A reranker swap works on any snapshot. An embed swap changes vector dimensions and needs its own `--fresh --ingest`;
`experiment.py` refuses to restore a snapshot built with a different embed model (`snapshot.json`).
`experiment.py` re-asserts the model selection at the start of every run, because the branch manifest outlives a run.
An interrupted `--fresh --ingest` resumes instead of wiping (`data/.experiment` marker) when re-run under the same name.
Use `backend/.venv` (Python 3.13, gitignored); the desktop repo's venv cannot install these requirements.

Result file (`Results/<run>/<dataset>.json`): `config` (chat and ingestion model, `selection`, knobs),
`note_titles` (id to title), and per question the scores, `context` (cited evidence text) and `trace`.
Trace events: `rerank` (`stage` search or expansion, `query`, `top_n`, `score_threshold`, and every
candidate with `name`, `type`, `score`, `notes`, sorted, before any cut) and `step` (`iteration`, `query`,
`docs`, `llm_seconds`, `can_answer`, `answer`, `next_query`, `finding`).

Datasets: HotpotQA, 100 questions, 990 notes, 2 gold and 8 distractor notes per question, all level hard,
79 bridge and 21 comparison. MuSiQue from LongBench, 50 questions, 526 notes, 2 to 4 hops.

## 4b. Covering many routes on one machine

One Mac, one heavy model in memory at a time, one writer on the graph: running pipelines side by side would only make
the models thrash. Breadth comes from not repeating work, which is the useful idea in Dream-RSI: record an expensive
stage once, then judge many variants against the record.

| Stage | Levers (`tests/benchmark/levers.py`) | Cost of one variant | How it is kept cheap |
|---|---|---|---|
| extract | ingestion model, attempts, chunk size, context window | hours | unchanged extraction calls replay from the model-call cache |
| index | embedding model, community summaries | minutes once extractions are cached | one snapshot per distinct set of extract + index levers, built once |
| retrieve | reranker, top-k, thresholds, graph expansion, evidence cap | seconds per query | `--evaluator retrieval`: no answering model, scored on the gold notes; `replay.py` then sweeps the filters offline |
| loop | iterations, the model that plans and answers | about three minutes a question | successive halving: small rungs first, survivors go on |
| answer | the answering model over fixed evidence | one call a question | `synthesis.py` |

- **Model-call cache** (`LLM_CALL_CACHE_DIR`, on by default under `experiment.py`, stored in `<repo>/llm-cache`). Keyed on provider,
  endpoint, model, the exact messages and the generation parameters. A changed prompt, model or note misses on its own; there
  are no versions to bump. It survives `--fresh`, so rebuilding an index with a different embedding model re-runs no extraction.
  Consequence: a repeated run is a replay, not a second sample. To measure sampling variance, pass `--no-cache`.
- **`sweep.py SPEC.json`** expands a spec (baseline, levers to vary, one-at-a-time or grid) into variants, builds each distinct
  index once, runs the rungs, keeps the better part of the field after each, scores the held-out slice once at the end for
  the baseline and the winners, and writes `Results/<sweep>/report.md`: scores, paired statistics against the baseline, time per
  question and unusable replies by stage. `--plan` prints what would run. Finished runs are skipped, so a sweep can be stopped and resumed.
- A full grid is for cheap stages only. Eight levers at three values is 6,561 hour-long runs; screen one lever at a time, then
  combine the winners in a small grid.
- Prompts are not levers yet. They are the largest lever, and they live inline in `llm.py` and `ingestion_agent.py`. Varying
  them from a spec needs them moved into named files first. Until then a prompt change is a code change, which the cache
  handles correctly on its own.

Specs for the first round are in `backend/sweeps/`: `round1-retrieval.json` (nine retrieval variants over one index, no answering
model) and `round1-loop.json` (loop limit and four smaller answering models, three rungs, held-out slice).

## 5. Measuring honestly

- At N=100 the standard error on exact match is about five points. A gap that size between two runs is noise.
  Use `compare.py`: McNemar's exact test on flipped questions and a bootstrap interval on per-question F1.
- HotpotQA retrieval recall is quantised (0, 0.5, 1). MuSiQue "expected notes" include distractors.
  Compare precision and recall only within one dataset.
- Title matching is substring-based and over-counts slightly. The scorer takes the first line of the answer.
  Cross-run comparisons are only valid with the same scorer, so do not change scoring mid-series.
- `replay.py` holds the issued queries fixed. It is exact for the first search and an estimate after it,
  because a different filter changes what the model reads and asks next. Confirm a winner with a real run.
- `synthesis.py` isolates the answering model. It says nothing about that model's ability to plan queries.
- Change one thing per run. Record why in the log below.

## 6. Baselines and inventory

| Run (archived in `Results/`) | Model | HotpotQA N=100 |
|---|---|---|
| After Optimizations | Gemma4 E4B, local | EM 62 %, F1 0.736, contains 74 %, retrieval recall 0.610, 231 s per question |
| Final Implementation | Gemma4 E4B, local | EM 58 %, F1 0.737, retrieval recall 0.715, 212 s per question |
| Final Implementation | Gemma3 4B and Gemini 3.1 Flash Lite | see the model comparison report |

Those runs used an older stack (Ollama, Typesense, labelled-section prompts, up to 10 iterations).
They are a target, not a like-for-like baseline. The first job on the current pipeline is a fresh baseline.

Shared weights on this machine (`/Users/josetseph/Projects/models/gguf`): Gemma 4 12B Q4, Gemma 4 E4B Q4,
Qwen3 Embedding 4B Q4 (2560 dimensions), Qwen3 Reranker 4B Q4. Desktop selection: 12B chat, those two for
embed and rerank. 26 GB RAM.

### Cost of a run on this machine (from `smoke-e4b`, Gemma 4 E4B Q4, 26 GB RAM, desktop app also running)

| Stage | Measured | What it means |
|---|---|---|
| Ingest one note | 84 to 321 s, mean 171 s; extraction is 95 % of it, storage and indexing about 10 s | 990 HotpotQA notes is about 47 hours of extraction. A full-set ingest per ingestion variant is not practical locally. |
| Community rebuild after 10 notes (then automatic, now `--communities`) | about 750 s: 70 second-level clusters (mean size 1.5, 48 of them single-entity orphans), each summarised by a model call | Roughly 40 % on top of ingestion, and it grows with the graph. |
| Answer one question | 178 s: three model steps of 10, 24 and 52 s, the rest is search, rerank and model swaps | 100 questions is about 5 hours per chat-model or retrieval variant. |

Planning consequences: build snapshots on question subsets (`--questions 20` is 200 notes, about 10 hours with E4B),
reuse one snapshot for every chat-model and retrieval experiment, and lean on `synthesis.py` and `replay.py`,
which cost one model call per question and nothing at all. Ingestion variants are the expensive axis; a cloud
ingestion model or a much smaller local one is the way to make them affordable.

## 7. Open questions

1. Fresh baseline on the current pipeline: E4B ingest and chat, `BENCHMARK_MODE`, default knobs.
2. Does raising `MAX_LOOP_ITERATIONS` from 3 recover the archived scores on multi-hop questions?
3. Which smaller local models (Qwen in the catalogue has never been run) hold the baseline as chat model,
   and separately as ingestion model? Ingestion is the expensive half, so a small model that extracts
   well is the bigger win for system load.
4. Where are questions actually lost: never surfaced, cut by filters, or answered wrong? `replay.py`
   on the baseline decides whether to work on ingestion, retrieval filters, or the answering prompt.
6. The one validation question needed all three loop iterations and answered on the last allowed step.
   With `MAX_LOOP_ITERATIONS=3` a 2-hop question has no slack; 3 and 4-hop MuSiQue questions cannot finish.
7. Per-note extraction (mean 171 s for a paragraph) is one model call, not several: a note that fits the
   context budget is extracted in a single pass, and only longer notes are split by task. The time is one long
   generation (a context sentence per entity plus every relationship), so the levers are model size and how
   much each extraction is asked to write. Ingestion-model runs in `plan.sh` measure the first.
8. Are community summaries worth their cost at all for these question sets, and at this granularity? Run one
   snapshot with `--communities` and one without. 70 clusters from 10 notes, mean size 1.5.
   A minimum cluster size would cut most of those model calls. Does retrieval quality move if it does?
9. Settled, low value: in the first extraction nearly every relationship was typed `related_to` although the
   closed vocabulary has `created`, `authored`, `produces`. It costs retrieval little, because graph expansion
   words an edge with its stored natural-language sentence and falls back to the type label only when that is missing.
5. Would smaller embedding and reranker models (0.6B) cost accuracy? They are loaded on every search.

## 8. Log

- **2026-09-21, baseline `base-e4b` (pipeline as it was, repairs included).** Gemma 4 E4B ingest and chat, 20 HotpotQA questions,
  199 notes, default knobs, no community summaries. Ingest 7.7 h (137 s a note, no failures). Exact match 45 %, F1 0.704,
  contains 60 %, retrieval recall 0.825, 227 s a question. Where the 8 misses went (`replay.py`): 6 retrieved but answered
  wrong, 2 gold note never surfaced, none cut by the filters. No filter setting beats the recall ceiling of 0.825, so the
  rerank filters are not the problem; tightening the evidence cap only raises precision. Three questions ran out of loop
  iterations and returned their last finding, a full sentence, as the answer, which cannot match. The other wrong answers are
  yes/no and answer-form errors ("between 1986 and 2013" for "from 1986 to 2013"). Aim next at the answering step and the loop limit, not retrieval filters.
- **2026-09-21, strict pipeline, cache and sweeps (branch `orb-testing-strict`).** Built in a second worktree so the baseline plan
  running from `orb-testing` keeps the code it started with. The rule is enforced (section 0), the model-call cache and the
  retrieval-only evaluator are in, and `sweep.py` with the lever registry replaces hand-written plans (section 4b).
  Verified by 471 unit tests, dry-run plans and synthetic replay files. **Not yet run against a live pipeline**: the machine is
  busy with the baseline. First live step after merging: a one-question run, then `round1-retrieval`.
  Expect the strict pipeline to fail notes the old one silently patched; that failure rate per model is the first new result.
- **2026-09-21, plan launched.** `plan.sh` started detached at 09:45 (`Results/plan.log`): 20 HotpotQA questions,
  199 notes, first notes at 155 to 180 s each, so the shared E4B index is due after roughly nine hours.
  The first launch attempt failed within seconds and exposed three faults, all fixed in `6daf616`:
  selecting a model without the download route drops its recorded file path and the server then reports no model
  (the download route is the only one that records paths; it is a no-op for files already present);
  an interrupted run let the plan cascade through every dependent run; and a blocked download thread kept the
  server alive after SIGTERM. Stop the plan with `pkill -INT -f tests/benchmark/experiment.py`; re-running
  `plan.sh` resumes, skipping finished runs and continuing an interrupted ingest.
- **2026-09-21, variations and plan.** Added `CHAT_MAX_CONTEXT_DOCS`, `EMBED_MODEL_ID`, `RERANK_MODEL_ID`, `--download`,
  standalone `--communities`, snapshot embed guard, ingest resume marker, and `plan.sh`: one E4B index on 20 HotpotQA
  questions, then baseline, loop limits, small reranker, five answering models (synthesis replay then full loop),
  community summaries, three ingestion models, small embed. Two hypotheses dropped after reading the code (open questions 7 and 9).
- **2026-09-21, sync to `main` `3bd480f`.** Fifteen commits since `63c602e`, applied as a three-way patch; two
  conflicts, both in attachment code, kept the branch side. Taken: the keyword-plus-vector candidate fix,
  on-demand communities and digests, ingestion on the selected model, llama.cpp knobs as Settings, the learned
  extraction budget changes. Cut again: the large-attachment prompt (route, `resolve_large_attachment`,
  `summarize_document`, its tests) and the settings-router test. `/benchmark/idle` and `experiment.py` adapted;
  `--communities` added. 412 unit and 2 integration tests pass. `smoke-e4b` and its snapshot predate this sync.
- **2026-09-21, validation run `smoke-e4b`.** First live end-to-end run: 1 HotpotQA question, its 10 notes,
  Gemma 4 E4B for ingestion and answering, default knobs, `BENCHMARK_MODE`. Passed: answer `YES` (exact match),
  recall 1.0, precision 0.222, 178 s for the question; traces, config, snapshot all written.
  `replay.py` on the real trace reproduces the live precision and recall exactly, so the offline simulation
  mirrors the pipeline. It also caught a bug the unit suite could not: the cut attachment stage was what
  seeded `content` and `logs` in the agent state (fixed in `0cf13ca`, pinned by `test_ingestion_entrypoint.py`).
  N=1 proves the plumbing, not the pipeline. Timings from this run are in section 6.
- **2026-09-21.** Harness built: traces, `experiment.py`, `compare.py`, `replay.py`, `synthesis.py`.
  Offline tools verified on a synthetic results file. Found and fixed the `paths.json` hazard and the shared
  manifest hazard (section 1). - **2026-09-20.** Resynced to `main` at `63c602e`. `main` had dropped LangGraph, moved the step protocol to
  JSON and removed `BENCHMARK_MODE`; re-added it on the new protocol. `evaluate.py` adapted to bare
  `linked_notes` ids and the new `sources` list. Docker replaced by `run.py`.
- **2026-09-18.** Branch created from `main`; product-only code cut; harness and `Results/` moved here
  and removed from `main`.
