# Orb — pipeline testing branch

This branch is the research half of Orb: the GraphRAG ingestion + retrieval
pipeline in `backend/`, the HotpotQA / MuSiQue benchmark harness in
`backend/tests/benchmark/`, and the run reports in `Results/`. No desktop
shell, no frontend. Use it to try a pipeline variation, score it, and keep or
drop it. The product lives on `main`.

**Read [Docs/00-testing-notes.md](Docs/00-testing-notes.md) first.** It is this branch's living record: hazards,
the pipeline facts experiments depend on, how to measure honestly, baselines, open questions and a dated log.
Update it in the same commit as any change or finding worth keeping.

## Run the pipeline

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate     # 3.12 or 3.13
pip install -r requirements.txt httpx tqdm
cp .env.example .env                       # pick a provider block; local GGUFs need MODELS_DIR
echo BENCHMARK_MODE=true >> .env           # short factual answers + the benchmark reasoning rules
python run.py                              # downloads Qdrant + Meilisearch once, starts both, then the API on :8000
```

No Docker. `run.py` reads `backend/.env` itself (the app no longer does), seeds cloud keys
from `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `HUGGINGFACE_API_KEY` into the
keychain-backed credential store, and `LLM_BASE_URL` points at any OpenAI-compatible server.
Data (SQLite, Kuzu, Qdrant, Meili, vault, logs) goes to `<repo>/data`.

The pipeline code is `main`'s as of `63c602e`; to pick up newer pipeline work see *Syncing* below.

## Isolation from the desktop app

The desktop app's `paths.json` points `DATA_DIR` at your real Orb data and the default
vault at your real notes, and the app binds both at import. On this branch:

- `run.py` always uses `<repo>/data` and a branch-local paths file, and refuses to start if
  any knowledge base's vault sits outside the data dir.
- `<repo>/models/` holds this branch's own `manifest.json` (the model selection). Its `gguf/`
  is a symlink to your desktop models folder, so downloads are shared and selections are not.
- `backend/conftest.py` pins every test run to a temp dir.

Never start the API here with bare `uvicorn app.main:app`: that resolves to the real data.

## Experiments

One command per run. It restores a snapshot, boots the pipeline with that run's settings,
ingests if asked, evaluates, snapshots if asked, and shuts everything down.

```bash
cd backend
python tests/benchmark/fetch_notes.py                 # once: materialise the note files

# 1. Ingest once per ingestion variant and keep the index (the slow part)
python tests/benchmark/experiment.py ingest-e4b --dataset hotpotqa --questions 20 \
    --fresh --ingest --no-eval --snapshot hotpot20-e4b --ingestion-model <id>

# 2. Try answering models and retrieval knobs against that same index
python tests/benchmark/experiment.py base   --dataset hotpotqa --questions 20 --restore hotpot20-e4b
python tests/benchmark/experiment.py qwen   --dataset hotpotqa --questions 20 --restore hotpot20-e4b --chat-model <id>
python tests/benchmark/experiment.py loops5 --dataset hotpotqa --questions 20 --restore hotpot20-e4b --set MAX_LOOP_ITERATIONS=5

# 3. Cheapest model test: answer from the evidence `base` already retrieved (no retrieval at all)
python tests/benchmark/experiment.py qwen-synth --restore hotpot20-e4b --chat-model <id> \
    --synthesis-from ../Results/base/hotpotqa.json

# 4. Read the results
python tests/benchmark/compare.py ../Results/base/hotpotqa.json ../Results/qwen/hotpotqa.json ../Results/loops5/hotpotqa.json
python tests/benchmark/replay.py  ../Results/base/hotpotqa.json
```

Model ids come from `GET /api/v1/models` (`local.downloadable[].id`, `local.installed[].ref`).
`--provider gemini --chat-model gemini-2.5-flash` runs a cloud model; `--provider openai_compat
--base-url http://127.0.0.1:1234 --chat-model <name>` runs anything LM Studio, Ollama or
llama-server is serving. Models are pinned on the knowledge base, which lives in the data dir.
`--set KEY=VALUE` overrides any setting in `backend/app/core/config.py` for that run.

What each tool tells you:

| Tool | Question it answers | Cost |
|---|---|---|
| `experiment.py` | What does this model / setting score, end to end? | full run |
| `compare.py` | Is the difference between two runs real? Pairs runs per question: McNemar exact test on flipped answers, bootstrap interval on F1. At N=100 a five-point exact-match gap is noise. | none |
| `replay.py` | Where are questions lost: gold note never surfaced, surfaced then cut by the filters, or retrieved and answered wrong? And would other `RERANKER_TOP_K` / `RERANKER_SCORE_THRESHOLD` / context-cap values keep more gold notes? | none, reads recorded traces |
| `synthesis.py` | Given identical evidence, which model answers correctly? | one model call per question |

A snapshot is only valid with the embedding model it was built with (recorded as `selection`
in every result file) and only restored into `<repo>/data`, because vault paths are absolute.
Results go to `Results/<run>/`: the scores, per-question traces, `config.json`, `server.log`.

The manual loop still works against a server started with `python run.py`:

```bash
python tests/benchmark/prepare_dataset.py --dataset hotpotqa [--questions N] [--resume] [--retry-failed]
python tests/benchmark/evaluate.py --dataset hotpotqa [--limit N] --verbose
```

Baseline to beat: `Results/Results (After Optimizations)`: Gemma4 E4B, HotpotQA N=100,
EM 62 %, F1 0.736, retrieval recall 0.610. Those runs allowed up to 10 loop iterations;
`MAX_LOOP_ITERATIONS` now defaults to 3, which is at most two searches per question.

## Where the pipeline is

| Stage | Files |
|---|---|
| Extraction prompts + chunking | `backend/app/workflows/agents/ingestion_agent.py`, `backend/app/workflows/extraction_chunking.py` |
| Ingestion orchestration | `backend/app/workflows/ingestion.py` |
| Retrieval (hybrid + graph expansion) | `backend/app/services/retrieval.py`, `backend/app/services/graph.py`, `backend/app/services/reranker.py` |
| Chat loop / answer synthesis | `backend/app/workflows/chat.py` |
| Providers / model routing | `backend/app/services/llm.py`, `backend/app/services/local_models.py` |

Docs per stage under [Docs/](Docs/): ingestion (10), local models (12), prompting (13), Kuzu (14), Qdrant/Meili (15), retrieval and chat (16), env reference (21).

## Syncing with `main`

This branch is `main`'s `backend/` with the product-only code cut out (desktop shell,
finance, multimedia, chat history, attachments). To resync, take `main`'s `backend/`
wholesale and re-cut, keeping `backend/run.py`, `backend/tests/benchmark/` and `BENCHMARK_MODE`
in `llm.py` / `config.py`. To push a pipeline win to `main`, `git cherry-pick` the commit;
shared pipeline files apply cleanly.
