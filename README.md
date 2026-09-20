# Orb — pipeline testing branch

This branch is the research half of Orb: the GraphRAG ingestion + retrieval
pipeline in `backend/`, the HotpotQA / MuSiQue benchmark harness in
`backend/tests/benchmark/`, and the run reports in `Results/`. No desktop
shell, no frontend. Use it to try a pipeline variation, score it, and keep or
drop it. The product lives on `main`.

## Run the pipeline

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate     # 3.12 or 3.13
pip install -r requirements.txt httpx tqdm
cp .env.example .env                       # pick a provider block; local GGUFs need MODELS_DIR
echo BENCHMARK_MODE=true >> .env           # short factual answers + the benchmark reasoning rules
python run.py                              # downloads Qdrant + Meilisearch once, starts both, then the API on :8000
```

No Docker. Data (SQLite, Kuzu, Qdrant, Meili, vault, logs) goes to `../data`, or `ORB_DATA_DIR`.
Use a fresh `ORB_DATA_DIR` per experiment so runs never share an index.

## Benchmark loop

```bash
cd backend
python tests/benchmark/fetch_notes.py                          # once: downloads + writes note .md files
python tests/benchmark/prepare_dataset.py --dataset hotpotqa   # ingest 990 notes (--resume / --retry-failed / --limit N)
python tests/benchmark/evaluate.py --dataset hotpotqa --verbose
python tests/benchmark/prepare_dataset.py --dataset musique    # 526 notes, 2–4 hop
python tests/benchmark/evaluate.py --dataset musique --verbose
```

Scores land in `backend/tests/benchmark/results/<dataset>_<timestamp>.json`
(gitignored). Metrics: EM, token F1, fuzzy, contains; retrieval P/R/F1 against
the manifest's supporting notes. Details in
[backend/tests/benchmark/README.md](backend/tests/benchmark/README.md).

## Recording an experiment

One folder per variation under `Results/`, same shape as the existing ones:

```
Results/<Variation name>/
├── <MODEL>_<DATASET>_REPORT.md    # what changed, config table, headline metrics vs. baseline
├── <model>_<dataset>_results.json # copied from backend/tests/benchmark/results/
└── <model>_logs/                  # DATA_DIR/logs/* from the run (optional)
```

Baseline to beat: `Results/Results (After Optimizations)` — Gemma4 E4B,
HotpotQA N=100, EM 62 %, F1 0.736, retrieval recall 0.610.

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

Pull pipeline changes in: `git merge main`, then drop the shell files it
brings back (`git rm -r -q desktop frontend "Platform Images" .github`) and commit.
Push a winning variation out: `git cherry-pick <commit>` onto `main` — only
`backend/` differs, so it applies cleanly.
