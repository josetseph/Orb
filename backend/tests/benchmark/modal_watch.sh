#!/usr/bin/env bash
# Run from backend/: tests/benchmark/modal_watch.sh FIRST_SPEC — every 10 minutes, print new milestones of the Modal queue
# (from the volume, committed every 10 minutes); exit when it is all done or something failed.
FIRST=${1:?first spec of the run, e.g. sweeps/round9-musique.json}
D=$(mktemp -d); seen=0
# Runs started before this watcher are not its business: wait for one more "start FIRST" line than now.
.venv/bin/python -m modal volume get --force orb-bench Results/modal-queue.log "$D/" >/dev/null 2>&1
before=$(grep -c "start $FIRST" "$D/modal-queue.log" 2>/dev/null || echo 0)
while true; do
  rm -f "$D/modal-queue.log"
  .venv/bin/python -m modal volume get --force orb-bench Results/modal-queue.log "$D/" >/dev/null 2>&1
  if [ -f "$D/modal-queue.log" ] && [ "$(grep -c "start $FIRST" "$D/modal-queue.log")" -gt "$before" ]; then
    # Only the latest run: the log on the volume is appended to by every run.
    start=$(grep -n "start $FIRST" "$D/modal-queue.log" | tail -1 | cut -d: -f1)
    [ -n "$start" ] && tail -n +"$start" "$D/modal-queue.log" > "$D/run.log" && mv "$D/run.log" "$D/modal-queue.log"
    lines=$(grep -E '^\[queue|^== (run|skip|report)|notes ingested|did not come up|failed|Traceback|no snapshot' "$D/modal-queue.log")
    n=$(printf '%s\n' "$lines" | grep -c .)
    [ "$n" -gt "$seen" ] && printf '%s\n' "$lines" | tail -n $((n - seen)) | cut -c1-160 && seen=$n
    grep -qE 'all done' "$D/modal-queue.log" && { echo "$(date +%H:%M) queue all done"; exit 0; }
    grep -qE 'Traceback|did not come up|failed 3 times' "$D/modal-queue.log" && { echo "$(date +%H:%M) PROBLEM"; exit 1; }
  fi
  sleep 600
done
