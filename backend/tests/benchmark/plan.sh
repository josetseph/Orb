#!/bin/bash
# The experiment plan, in order of value. Each line is one run; a run whose result already
# exists is skipped, so this is safe to re-run after a stop, a crash or a reboot.
#
#   cd backend && nohup tests/benchmark/plan.sh > ../Results/plan.log 2>&1 &     # start
#   pkill -INT -f tests/benchmark/plan.sh; pkill -INT -f tests/benchmark/experiment.py   # stop
#
# PYTHON must be the interpreter of a venv with requirements.txt installed.
set -u
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python}"
N="${QUESTIONS:-20}"
DS="${DATASET:-hotpotqa}"
R=../Results
BASE=hp$N-e4b                      # the shared index: Gemma 4 E4B ingestion, RAM-tier embed + reranker

run() {  # run <name> <file that proves it finished> <experiment.py args...>
  local name=$1 proof=$2; shift 2
  if [ -e "$proof" ]; then echo "== skip $name (done)"; return; fi
  echo "== $(date '+%F %T') start $name"
  "$PY" tests/benchmark/experiment.py "$name" "$@" || echo "== FAILED $name (continuing)"
  echo "== $(date '+%F %T') end $name"
}
full()  { run "$1" "$R/$1/$DS.json" --dataset $DS --questions $N "${@:2}"; }
synth() { run "$1" "$R/$1/synthesis.json" --synthesis-from "$R/base-e4b/$DS.json" "${@:2}"; }

# 1. One index, built once (the slow part). No community summaries: the app's default since main 52041d8.
run ingest-e4b ../snapshots/$BASE --dataset $DS --questions $N --fresh --ingest --no-eval --snapshot $BASE \
    --provider local --chat-model gemma4-e4b-q4 --ingestion-model gemma4-e4b-q4

# 2. Baseline on the current pipeline.
full base-e4b --restore $BASE --provider local --chat-model gemma4-e4b-q4

# 3. Retrieval: does the loop need more room, and does a small reranker hold up?
full loops5      --restore $BASE --provider local --chat-model gemma4-e4b-q4 --set MAX_LOOP_ITERATIONS=5
full loops8      --restore $BASE --provider local --chat-model gemma4-e4b-q4 --set MAX_LOOP_ITERATIONS=8
full rerank-0.6b --restore $BASE --provider local --chat-model gemma4-e4b-q4 --set RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 --download

# 4. Answering models, cheapest test first: same evidence, only the model differs.
for m in qwen35-2b-q4 qwen35-4b-q4 qwen35-9b-q4 gemma4-e2b-q4 gemma4-12b-q4; do
  synth synth-$m --restore $BASE --provider local --chat-model $m --download
done
#    ...then the same models driving the whole loop (planning queries as well as answering).
for m in qwen35-4b-q4 qwen35-9b-q4 qwen35-2b-q4 gemma4-e2b-q4 gemma4-12b-q4; do
  full chat-$m --restore $BASE --provider local --chat-model $m --download
done

# 5. Community summaries: same index, rebuilt with them, then the baseline again.
run communities ../snapshots/$BASE-comm --dataset $DS --restore $BASE --communities --no-eval --snapshot $BASE-comm \
    --provider local --chat-model gemma4-e4b-q4
full base-e4b-comm --restore $BASE-comm --provider local --chat-model gemma4-e4b-q4

# 6. Ingestion models: each builds its own index, then E4B answers over it so only extraction differs.
for m in qwen35-4b-q4 gemma4-e2b-q4 qwen35-2b-q4; do
  run ingest-$m ../snapshots/hp$N-$m --dataset $DS --questions $N --fresh --ingest --no-eval --snapshot hp$N-$m \
      --provider local --chat-model $m --ingestion-model $m --download
  full over-$m --restore hp$N-$m --provider local --chat-model gemma4-e4b-q4
done

# 7. Small embedding + reranker: new vectors, so its own index.
run ingest-small-embed ../snapshots/hp$N-small-embed --dataset $DS --questions $N --fresh --ingest --no-eval \
    --snapshot hp$N-small-embed --provider local --chat-model gemma4-e4b-q4 --ingestion-model gemma4-e4b-q4 \
    --set EMBED_MODEL_ID=qwen3-embed-0.6b-q8 --set RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 --download
full small-embed --restore hp$N-small-embed --provider local --chat-model gemma4-e4b-q4 \
    --set EMBED_MODEL_ID=qwen3-embed-0.6b-q8 --set RERANK_MODEL_ID=qwen3-rerank-0.6b-q4

echo "== $(date '+%F %T') plan complete. Compare with:"
echo "   $PY tests/benchmark/compare.py $R/base-e4b/$DS.json $R/*/$DS.json"
