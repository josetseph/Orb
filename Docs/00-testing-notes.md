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
| `tests/benchmark/sweep.py` + `sweeps/*.json` | A whole round from a spec: variants, shared indexes, rungs, held-out slice, one report. See 4b. |
| `tests/benchmark/retrieval_eval.py` | Search alone against the gold notes, no answering model. |
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

## 4c. Running on a rented GPU (RunPod)

The Mac runs one model at a time and is needed for other work; the AWS and GCP servers on hand are micro instances
(2 vCPU, 1 GB RAM, no GPU) and cannot hold even the smallest model. Long runs go to a RunPod GPU instead, on the
second RunPod account content-machine also uses (keys in the gitignored `<repo>/.env`, copied from content-machine's `ALT_` set).

```bash
cd backend
.venv/bin/python tests/benchmark/pod.py up          # rent (SECURE cloud, 16-48 GB card by availability), upload, install: ~20 min
.venv/bin/python tests/benchmark/pod.py run live-check sweeps/round1-retrieval.json sweeps/round1-loop.json
.venv/bin/python tests/benchmark/pod.py status      # state, $/hr, spend so far, queue tail
.venv/bin/python tests/benchmark/pod.py pull        # results -> Results/runpod/ (restarts a stopped pod to reach its volume)
.venv/bin/python tests/benchmark/pod.py down        # pull, then delete pod and volume
```

- Everything on the pod lives on its `/workspace` volume, so stopping it keeps models, indexes, cache and results.
- **Running out of credit.** `up` reads the account balance and the pod's rate and caps the pod at (credit - $1.50) / rate hours:
  the watchdog then stops it with $1.50 left, enough to restart it and copy its volume home. `follow` (run on the Mac) copies results
  and the model-call cache to `pod-state/` every 15 minutes, watches the real balance (another project may spend from the same account),
  stops the pod gracefully before the reserve is reached, and writes `pod-state/STOPPED.txt` with the reason. To continue elsewhere:
  `pod.py --account main up --restore` (main-account keys go in `.env` as `MAIN_RUNPOD_*`), then `run` the same queue: finished runs
  are skipped and the cache replays every model call already made, so rebuilding an interrupted index costs minutes.
- The AWS and GCP micro servers were considered as an always-on store for progress; on 2026-09-28 AWS refused SSH and the GCP VM was stopped.
- A watchdog on the pod, holding no API key, ends the container after `--max-hours` (default 48) or `--idle-hours`
  with no queue running (default 3). That stops the GPU charge; only the volume is billed until `down`.
- `pull` copies only what the pod produced (the upload's file list is kept on the pod as `.uploaded`).
- Indexes are built on the pod with the strict pipeline. The Mac snapshot `hp20-e4b` cannot move: its vault paths are absolute.
- llama-cpp-python is built for CUDA on the pod at the same version as the Mac (0.3.35), so local-model results compare.

## 4d. Lanes: several experiments on one GPU

One process runs one model at a time and leaves most of a big GPU idle, so a 48 GB card runs several experiment processes
side by side: `sweep.py` bundles (`{"lanes": 3, "specs": [...]}`, e.g. `sweeps/round2.json`) or `--lanes N`. Each lane has its own
data dir (`data-laneN`), ports (API 8000+10N, Qdrant 6333/6334+10N, Meilisearch 7700+10N) and model-selection manifest
(`models-laneN/`, its `gguf/` a symlink to the one shared copy of the weights). The model-call cache is shared.

- Variants that share an index run one after another on one lane; different indexes run on different lanes.
- Index builds live in `Results/_indexes/` and `snapshots/`, shared by every spec; a lock per index means two lanes never build
  the same one. A snapshot restored into another lane has its registry paths moved there (`experiment.relocate`).
- Every model the bundle needs is downloaded once before any lane starts (two lanes writing one file corrupt it).
- An ingest whose first 20 notes all fail stops (`--give-up-after`): round 1 spent 9 h on a model that could not do the task.
- Pin `EMBED_MODEL_ID` and `RERANK_MODEL_ID` in every spec. Unpinned they follow the machine's RAM: round 1's pods picked the 8B
  pair, the Mac uses the 4B pair. Round 2 pins 8B to stay comparable with round 1 and tests the 4B and 0.6B as variants.

**Branching (the Dream-RSI idea).** The model-call cache makes every change a branch from the last point it touches: calls before
it replay, calls after it generate. Changing the answering model replays extraction; raising `MAX_LOOP_ITERATIONS` replays the steps
already taken and generates only the new ones; a retrieval lever replays every model call and re-runs only search. What is not built:
an automatic search that proposes new strategies (Dream-RSI's inner loop) and branching mid-question from a saved loop state.

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

- **2026-10-01, Qwen 3.5 ignores `/no_think`; thinking is switched off through the chat template instead.** Round 2's first
  Qwen extraction note still opened with an untagged "Thinking Process:" despite the `/no_think` suffix. The Qwen 3.5 GGUF templates
  read `enable_thinking` (4B and 9B think unless it is false; 2B only when it is true); Gemma 4's template defaults it to false.
  New per-stage settings `EXTRACTION_ENABLE_THINKING` and `ENABLE_THINKING` send `chat_template_kwargs` (the OpenAI-compatible field
  llama-server and vLLM honour); the in-process runtime passes it to the template. Checked on the pod with Qwen 3.5 4B: no switch gives
  "Thinking Process: ...", `enable_thinking=false` gives `{"capital": "Paris"}`. The switch only joins the cache key when set, so
  earlier replies keep their keys. The Qwen specs use it in place of the suffix; the "thinking on" check is dropped (round 1 already
  showed it fails strict parsing). Round 2 was restarted with this; the first launch also lost 3 minutes to a Hugging Face download
  that stopped short, so the prefetch now retries up to three times.
- **2026-10-01, round 2 launched on the main account (A40, 3 lanes, bundle `sweeps/round2.json`).** Seven specs: confirmation at scale
  (HotpotQA 20-70, MuSiQue 0-50; 12B against E4B extraction), answering models and loop limits over the 12B index, Qwen 3.5 as answering
  model and as extractor with `/no_think`, retrieval levers (no answering model), and index levers (4B / 0.6B embedder, communities).
  New switches for it: `PROMPT_SUFFIX` / `EXTRACTION_PROMPT_SUFFIX` (per stage, so a chat test never changes the index).
- **2026-09-30, round1-extraction complete: extract with Gemma 4 12B.** Full report: `Results/runpod/round1-extraction/report.md`.
  E4B answers every question; only the extractor changes. 20 HotpotQA questions, 199 notes.

  | Extractor | Notes ingested | EM | F1 | Recall | Build |
  |---|---|---|---|---|---|
  | **Gemma 4 12B** | **195** | **55 %** | **0.767** | **0.875** | 4.2 h |
  | Gemma 4 E4B (baseline) | 120 | 30 % | 0.429 | 0.550 | 2.3 h |
  | E4B, two-pass extraction | 96 | 30 % | 0.340 | 0.525 | 1.9 h |
  | Gemma 4 E2B | 100 | 15 % | 0.322 | 0.500 | 1.7 h |
  | E4B, JSON-constrained extraction | 72 | 15 % | 0.211 | 0.425 | 8.5 h |
  | Qwen 3.5 4B | 0 | n/a | n/a | n/a | 8.9 h |

  - The 12B extractor beats the strict baseline on 6 questions and loses 1; F1 +0.34, 95 % CI +0.12 to +0.54; McNemar p = 0.125, so
    confirm on more questions. It also beats the old patched pipeline (F1 0.704), with no repair of any kind.
  - Answer quality follows the share of notes a model extracts usably. The small models' failure is relating things they did not list
    as entities; stating the rule did not fix it, and two-pass extraction made it worse.
  - The JSON grammar is a loss on every count for extraction: slowest by far and the most rejections. Keep extraction unconstrained
    with the bare-JSON prompt; keep chat-side replies constrained (short, and unconstrained they come back fenced).
  - Suggested product setting: 12B for ingestion (runs once per note), a small model for chat (runs every question). Test next.
  - Cost of the round on the alt account: about $9.50 of $13.66 (including the 9 h spent learning Qwen 3.5 does not work as-is).
    $3.58 left; pod deleted.
- **2026-09-29, round1-extraction, first results (RTX 4000 Ada).**
  - E4B, unconstrained extraction: 120 of 199 notes ingested, 79 rejected (relationships to unlisted entities), 2.3 h.
    Evaluated on 20 questions: exact match 30 %, F1 0.429, contains 65 %, retrieval recall 0.550, 95 s a question, no unusable
    chat replies. Against the old patched pipeline (`base-e4b`: EM 45 %, F1 0.704, recall 0.825) the F1 drop is outside the
    bootstrap interval but McNemar is not significant (+2/-5). Most of the gap is the 40 % of notes the strict pipeline refuses:
    their facts are missing from the graph. Two of the lost questions are answer form ("'Animorphs' is a science fantasy series…").
  - Qwen 3.5 4B: 0 of 199. It writes about 25,000 characters of plain-text reasoning ("Thinking Process: …") before its JSON,
    outside `<think>` tags, taking about 160 s a note; 9 h of the budget went to learning this. Qwen 3.5 is dropped from the round
    until a thinking-off lever exists (llama.cpp chat-template option, or constrained decoding, which forces `{` first).
  - The Mac lost its network around 11:00; the follower died on the DNS error and has been fixed to wait and retry.
    The pod ran on regardless under its budget cap. GitHub pushes are queued until the network settles.
- **2026-09-28, correction to round0-speed, and the fence fix.** The 87 s unconstrained builds were not a clean measurement: most of
  their replies were replayed, and unconstrained E4B fenced its extraction replies in a markdown code block almost every time (53 of
  53 in the second round1 attempt, which was stopped). The extraction prompts never said not to. They now say: reply with the JSON object
  alone, first character `{`, no fence. Re-measured on one question (10 notes, unconstrained): no fenced replies, 6 ingested,
  4 rejected (all relationships to an unlisted entity), question correct, 441 s = about 44 s a note. The real speedup over constrained
  extraction (172 s a note) is about 4x, not 20x.
- **2026-09-28, round1-extraction first attempt: discarded.** Every index build stopped after its first three notes: `prepare_dataset.py`
  had a circuit breaker that tripped on three failures in a row, meant for a dead server. Under the strict pipeline a rejected reply is a
  failure too, so every index was empty and every evaluation meaningless; caught after about an hour and stopped. The breaker now trips only
  when requests to the server fail. A note the pipeline rejects is counted and passed over. `experiment.py` records ingested / rejected
  / total notes per build and refuses to evaluate an index with nothing in it.
- **2026-09-28, round0-speed (RTX 4000 Ada, 1 question, 10 notes): the JSON grammar is the bottleneck.** Index build: baseline
  1,717 s; flash attention 1,654 s; JSON constraint off 87 s, about 20x faster, with the same 3 of 10 extractions rejected. GPU
  utilisation sat near 13 % with the constraint on: llama.cpp applies the grammar on the CPU, token by token, and extraction replies
  are long. Without the constraint the model wrapped its short research-step reply in a markdown code fence, which is rightly
  rejected, so the question died. Split into two switches: `JSON_CONSTRAINED_DECODING` for chat-side replies (short, cheap to
  constrain) and `EXTRACTION_JSON_CONSTRAINED` for ingestion replies. Rounds now build indexes unconstrained and keep chat constrained.
  Also found: the model-call cache key left out the settings the probe varied, so its first pass replayed the baseline's replies for
  every variant. Every reply-shaping setting is now in the key (`_REPLY_SHAPING_SETTINGS` in `llm.py`), each pinned by a test.
  Next: `round1-extraction` (five other extraction models, two-pass extraction, and the constraint back on as a check at 199 notes).
- **2026-09-28, second RunPod live check (RTX 4090, about $0.75): ready.** With the three prompt fixes, all four steps pass and the
  question is answered correctly in each (exact match, recall 1.0; retrieval-only found every gold note; synthesis replay correct).
  The cache replayed all 4 model calls of the repeat run; its remaining 68 s are embedding, reranking and loading models one at a
  time, which are not model calls. Extraction still rejects 4 of 10 notes with E4B for relating to entities it did not list (two years,
  "ConradBrooks" for "Conrad Brooks", "surgeon Strange") even with the rule stated: a real property of this model, and
  `EXTRACTION_MODE=task_split` is the lever against it. Speed is the open problem: about 150 s a note on a 4090, no faster than the Mac,
  for a 4B model that should run several times faster. Total RunPod spend so far about $1.30.
- **2026-09-28, first run on RunPod (A40, $0.56 for setup plus the live check).** All four live-check steps finished and came
  home with `pod.py pull`: strict ingest, retrieval-only evaluator, synthesis replay, and a repeat run replayed from the cache in
  262 ms against 26 s. It also exposed three contract gaps in our own prompts, each rejecting replies that were right by the prompt:
  (1) the query-analysis field list said `entity_types` while its examples and schema said `expected_entity_types`, so query hints
  were lost on every question; (2) the first research step, before any search, was told to write `"finding": ""` and rejected for
  writing `null`, which ended every question before it searched; (3) the extraction prompt never said relationship ends must be listed
  entities, yet 5 of 10 notes were rejected for relating to an unlisted date or concept ("2012", "actual accounts"). Fixed by making
  prompt and schema say the same thing: the field list uses `expected_entity_types`, the first step asks for and accepts `null`, and
  the extraction prompt states the endpoint rule (add the thing as a node first). New lever `EXTRACTION_MODE`: `task_split` sends every
  note through entities-then-relationships. Speed: the model ran fully on the GPU (CUDA, all layers), yet extraction averaged about
  110 s a note against 137 s on the Mac. Not yet explained; JSON-constrained sampling runs on the CPU in llama.cpp and is the first suspect.
- **2026-09-21, first strict result: the predicate vocabulary does not fit encyclopedic notes.** A live check of the strict
  pipeline (E4B, the 10 notes of HotpotQA question 1) was stopped by the owner after 7 notes: 4 ingested, 3 rejected. All three
  rejections were the same thing. The reply was valid JSON with every field present (the JSON constraint works), but it used
  predicates outside the closed vocabulary: `produced` 6, `stars` 5, `directed` 2, `authored_by` 2, `stars_in`, `concerns`,
  `based_on`, `released_on`. 19 of 45 relationships in those replies. The old pipeline rewrote every one of them to
  `related_to` without a trace, which is why the first extraction looked almost entirely `related_to`. The vocabulary was
  written for personal notes (`works_at`, `friend_of`, `attends`); film and biography notes need `directed`, `produced`,
  `acted_in`, `based_on`. This is a design question for the owner, not something to patch: widen the vocabulary, allow
  free predicates (retrieval words an edge with its stored sentence anyway), or constrain the predicate to the list while it
  is generated. Until it is settled, strict ingestion of these datasets will reject a large share of notes with E4B.
  Raw replies: `Results/check/strict/invalid_model_output.jsonl`. **Still unverified live:** the retrieval evaluator,
  synthesis replay and cache replay. `tests/benchmark/live_check.sh` runs all four steps; run it before the first sweep.
- **2026-09-21, plan stopped, strict branch merged, ready state.** The owner stopped testing after the baseline; the rest of the old
  plan (model comparisons on the patched pipeline) was not run and `plan.sh` is gone. `orb-testing-strict` is merged into
  `orb-testing`. `experiment.py` and `sweep.py` now turn SIGINT and SIGTERM into a clean stop: a backgrounded process inherits
  SIGINT=ignore, so the documented stop had done nothing and the run had to be killed. A spec can name an existing snapshot
  as its baseline index (`"index": "hp20-e4b"`), so the 7.7-hour E4B ingest is reused by `round1-retrieval` and `round1-loop`.
  That index was built by the pipeline as it was, repairs included: fair for comparing retrieval and loop levers against each
  other, not a strict-pipeline extraction result. `round1-ingestion` builds its own indexes with the strict pipeline.
  To start: `cd backend && .venv/bin/python tests/benchmark/sweep.py sweeps/round1-retrieval.json` (add `--plan` to look first).
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
