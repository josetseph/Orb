#!/usr/bin/env bash
# Runs on the pod: each argument in turn, "live-check" or a sweep spec. Sequential on purpose (one GPU).
cd /workspace/orb/backend || exit 9
R=/workspace/orb/Results; mkdir -p $R
rm -f $R/queue.done; echo $$ > $R/queue.pid
trap 'echo "[queue $(date "+%F %T")] interrupted"; rm -f $R/queue.pid; exit 130' INT TERM
for item in "$@"; do
  echo "[queue $(date '+%F %T')] start $item"
  if [ "$item" = "live-check" ]; then PYTHON=.venv/bin/python tests/benchmark/live_check.sh
  else .venv/bin/python tests/benchmark/sweep.py "$item"; fi
  rc=$?
  echo "[queue $(date '+%F %T')] end $item (exit $rc)"
done
date '+%F %T' > $R/queue.done; rm -f $R/queue.pid
echo "[queue $(date '+%F %T')] all done"
