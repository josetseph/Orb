#!/usr/bin/env bash
# Run from backend/: tests/benchmark/pod/chain.sh NAME SPEC...
# Wait for NAME's current queue to finish, then queue SPEC... on it at once (within the watchdog's 15-minute
# grace, so the pod is never stopped and never has to find capacity again), then follow it.
name=$1; shift
P=".venv/bin/python -u tests/benchmark/pod.py --name $name"
until .venv/bin/python - "$name" <<'PY'
import sys; name = sys.argv[1]; sys.argv = ["pod.py"]; sys.path.insert(0, "tests/benchmark")
import pod as P
P.STATE, P.STATE_DIR, P.NAME = P.REPO / f"pod-{name}.json", P.REPO / f"pod-state-{name}", name
P.saved()
out = P.ssh(P.running_endpoint(), "cat /workspace/orb/Results/queue.done 2>/dev/null", capture=True, check=False).strip()
sys.exit(0 if out else 1)
PY
do sleep 120; done
echo "$(date +%H:%M) $name: queue finished; queueing $*"
$P run "$@" && $P follow
echo "$name follow exit $?"
