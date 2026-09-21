#!/bin/bash
# One question through every path once, against real models (about 35 minutes). Run this before the first sweep.
#   strict ingest + full evaluation -> retrieval-only evaluator -> synthesis replay -> a repeat run that should replay from the cache
cd "$(dirname "$0")/../.." || exit 9
PY="${PYTHON:-.venv/bin/python}"; M="--provider local --chat-model ${MODEL:-gemma4-e4b-q4} --ingestion-model ${MODEL:-gemma4-e4b-q4}"
$PY tests/benchmark/experiment.py check/strict    --dataset hotpotqa --questions 1 --fresh --ingest --snapshot check-strict $M || exit 1
$PY tests/benchmark/experiment.py check/retrieval --dataset hotpotqa --questions 1 --restore check-strict --evaluator retrieval --queries-from ../Results/check/strict/hotpotqa.json $M || exit 2
$PY tests/benchmark/experiment.py check/synth     --restore check-strict --synthesis-from ../Results/check/strict/hotpotqa.json $M || exit 3
$PY tests/benchmark/experiment.py check/replay    --dataset hotpotqa --questions 1 --restore check-strict $M || exit 4
echo "ALL FOUR STEPS FINISHED"
