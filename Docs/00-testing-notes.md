# Testing branch notes

The living record for `orb-testing`: what is dangerous, what is true about the pipeline
that an experiment depends on, how to measure honestly, and what has been learned.
Update it in the same commit as the change or finding it describes. Newest log entry first.
If something here stops being true, fix or delete it; do not leave it to rot.

## 1. Hazards

| Hazard | What happens | Guard |
|---|---|---|
| Desktop `paths.json` (`~/Library/Application Support/Orb/paths.json`) points `data_dir` at the real app data and `default_vault_path` at the real notes vault. The backend binds both at import, and the file beats the `ORB_DEFAULT_VAULT` env var. | Setting only `ORB_DATA_DIR` is not enough: a fresh data dir still creates its default KB on the real vault. A note created there lands in the real journal. This happened once (2026-09-20, one 49-byte smoke note, removed). | `backend/run.py` pins `ORB_PATHS_FILE` and `ORB_DATA_DIR` to the repo and refuses to start if any KB vault is outside the data dir. `backend/conftest.py` pins test runs to a temp dir. Never run bare `uvicorn app.main:app` or `import app.main` here without both env vars. |
| `MODELS_DIR/manifest.json` holds the model selection, and the desktop app reads the same file. | Downloading or selecting a model through a backend pointed at the shared models dir switches the desktop app's model. | `run.py` uses `<repo>/models/` with its own manifest; `gguf/` is a symlink to the shared weights. Switch models per run with a KB pin (`experiment.py --chat-model`), which is stored in the data dir. |
| A downloaded embedding model that differs from the snapshot's. | `select-chat-model` resizes Qdrant collections to the embed model's dimensions. Vectors in a snapshot are only valid for the embed model that built them. | Every result file records `config.selection` (chat, embed, reranker ids). Keep embed and reranker fixed across runs you intend to compare. |
| Snapshots restored anywhere but `<repo>/data`. | The KB registry stores absolute vault paths; a snapshot elsewhere points back at the original location. | `experiment.py` only ever restores into `<repo>/data`. |
| A backgrounded process inherits `SIGINT = ignore`. | `kill -INT` on a runner that has not installed handlers does nothing; it keeps the Kuzu lock and the ports. | `run.py` installs handlers first thing. If a run will not start with "Could not set lock on file", look for a stray `run.py`. |
| Kuzu is single-writer. | Two backends on one data dir: the second fails at import. | One experiment at a time. |

## 2. How this branch is built

`backend/` is `main`'s backend with product-only code cut out. It is not a fork that merges.
To resync: take `main`'s `backend/` wholesale, re-cut, restore the branch-only files.

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
- Local inference loads one heavy model at a time (chat, embed, reranker swap in and out). A single
  retrieval iteration is at minimum chat, embed, rerank, chat. Model swaps dominate latency on small RAM.
- Chat response shape: `answer`, `sources` (`{id, title}` of cited notes), `context` (docs; `linked_notes`
  are bare ids), `thinking`, and `trace` when requested.

Ingestion (`workflows/ingestion.py`, `workflows/agents/ingestion_agent.py`):

- Three stages in order: extraction, storage, summarisation. No LangGraph; `run_ingestion_agent` is a plain loop.
- Community detection (Leiden) and temporal digests run in the background after ingestion goes idle.
  Evaluate or snapshot only after `GET /api/v1/benchmark/idle` reports idle; `experiment.py` waits for it.
- Ingestion model and chat model are independent. An index built by one ingestion model can be
  queried by any chat model, which is what makes snapshots worth keeping.

## 4. The harness

| Tool | Use |
|---|---|
| `tests/benchmark/experiment.py` | One run end to end: restore snapshot, boot, pin models, ingest, wait idle, evaluate, snapshot, stop. |
| `tests/benchmark/compare.py` | Pair runs per question. First file is the baseline. |
| `tests/benchmark/replay.py` | Offline: where questions are lost, and a sweep of top-k, threshold, context cap. |
| `tests/benchmark/synthesis.py` | Re-answer from a run's frozen evidence with another model. |
| `evaluate.py`, `prepare_dataset.py`, `fetch_notes.py` | The manual loop; `--questions N` ingests only the notes the first N questions use. |

Result file (`Results/<run>/<dataset>.json`): `config` (chat and ingestion model, `selection`, knobs),
`note_titles` (id to title), and per question the scores, `context` (cited evidence text) and `trace`.
Trace events: `rerank` (`stage` search or expansion, `query`, `top_n`, `score_threshold`, and every
candidate with `name`, `type`, `score`, `notes`, sorted, before any cut) and `step` (`iteration`, `query`,
`docs`, `llm_seconds`, `can_answer`, `answer`, `next_query`, `finding`).

Datasets: HotpotQA, 100 questions, 990 notes, 2 gold and 8 distractor notes per question, all level hard,
79 bridge and 21 comparison. MuSiQue from LongBench, 50 questions, 526 notes, 2 to 4 hops.

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
| Background communities after 10 notes | about 750 s: 70 second-level clusters (mean size 1.5, 48 of them single-entity orphans), each summarised by a model call | Roughly 40 % on top of ingestion, and it grows with the graph. |
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
7. Is per-note extraction (mean 171 s for a paragraph) dominated by the number of passes? It runs entity,
   relationship and context passes separately. Fewer or merged passes is the obvious ingestion speed lever.
8. Are second-level communities worth their cost at this granularity? 70 clusters from 10 notes, mean size 1.5.
   A minimum cluster size would cut most of those model calls. Does retrieval quality move if it does?
9. In the first extraction nearly every relationship was typed `related_to` even where the text gave a
   specific predicate ("is the director of"). Check across notes; typed edges are what graph expansion can use.
5. Would smaller embedding and reranker models (0.6B) cost accuracy? They are loaded on every search.

## 8. Log

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
