"""Every 10 minutes, look at each named pod for trouble; exit (with the reason) at the first sign of it.

Usage (from backend/): .venv/bin/python -u tests/benchmark/watch_pods.py <pod name> ...

Trouble: backend crashes (500s, NoneType), storage failures, a dead queue, no progress for 40 minutes,
extraction rejecting >70% of the latest notes, a pod that stopped before its queue finished, or credit
under $8. Network errors are retried, never reported. Pods still being set up (no queue yet) are skipped.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pod as P  # noqa: E402

NAMES = sys.argv[1:]
PROBE = r"""cd /workspace/orb
alive=no; [ -f Results/queue.pid ] && kill -0 $(cat Results/queue.pid) 2>/dev/null && alive=yes
done=no; [ -f Results/queue.done ] && done=yes
[ -f Results/queue.pid ] || [ -f Results/queue.done ] || { echo '{"setup": true}'; exit 0; }
L=$(ls -t Results/_indexes/*-served/server.log Results/round*/*/r*/server.log 2>/dev/null | head -1)
p=$(ls -t data*/prepare_progress.json 2>/dev/null | head -1)
python3 - "$L" "$p" "$alive" "$done" <<'PY'
import json, sys, collections
log, prog, alive, done = sys.argv[1:5]
out = {"alive": alive == "yes", "done": done == "yes", "log": log}
c = collections.Counter(); recent = []
if log:
    for line in open(log, errors="replace"):
        if 'POST /api/v1/chat HTTP/1.1" 200' in line: c["chat_ok"] += 1
        if 'POST /api/v1/chat HTTP/1.1" 500' in line or 'benchmark/retrieve HTTP/1.1" 500' in line: c["http500"] += 1
        if "NoneType" in line and "Store migration" not in line: c["nonetype"] += 1
        if "[Ingestion] FAILURE" in line:
            kind = "storage" if "Storage failed" in line else "model"
            c[kind] += 1; recent.append(0)
        if "[Ingestion] SUCCESS" in line:
            c["ingested"] += 1; recent.append(1)
out["counts"] = dict(c)
out["recent_fail_rate"] = round(1 - sum(recent[-30:]) / len(recent[-30:]), 2) if len(recent) >= 30 else None
if prog:
    d = json.load(open(prog)); out["notes_done"] = sum(1 for v in list(d.values())[0].values() if not str(v).startswith("pending"))
print(json.dumps(out))
PY"""

last: dict[str, tuple] = {}
stalls: dict[str, int] = {}
seen: dict[str, bool] = {}  # last probe's queue.done per pod
baseline: dict[tuple, dict] = {}  # (pod, log) -> counts when first seen: logs append across restarts


def problem(name: str, why: str, info: dict) -> None:
    print(time.strftime("%H:%M"), f"PROBLEM on {name}: {why}", json.dumps(info), flush=True)
    sys.exit(1)


while True:
    for name in NAMES:
        P.STATE, P.STATE_DIR, P.NAME = P.REPO / f"pod-{name}.json", P.REPO / f"pod-state-{name}", name
        if not P.STATE.is_file():
            continue  # deleted: finished and taken down
        try:
            status = P.pod_info(P.saved()["id"]).get("desiredStatus")
            if status != "RUNNING":
                if seen.get(name) is False:  # stopped mid-queue: a spot reclaim or the time cap
                    problem(name, f"pod is {status} but its queue had not finished", {})
                continue  # stopped by its follower or its own watchdog when the queue finished
            info = json.loads(P.ssh(P.running_endpoint(), PROBE, capture=True, check=False).strip().splitlines()[-1])
        except Exception as exc:  # network: try again next round
            print(time.strftime("%H:%M"), name, "unreachable:", str(exc)[:80], flush=True)
            continue
        if info.get("setup"):
            continue  # still being set up: no queue yet
        seen[name] = info["done"]
        c = info.get("counts", {})
        base = baseline.setdefault((name, info["log"]), dict(c))
        new = {k: v - base.get(k, 0) for k, v in c.items()}
        if new.get("http500", 0) > 0 or new.get("nonetype", 0) > 0:
            problem(name, "backend errors since the watch started", info)
        new_notes = new.get("ingested", 0) + new.get("model", 0) + new.get("storage", 0)
        if new.get("storage", 0) >= 3 and new.get("storage", 0) > 0.03 * new_notes:
            problem(name, "storage failures (harness, not the model)", info)
        if (info.get("recent_fail_rate") or 0) > 0.7:
            problem(name, "more than 70% of the latest 30 notes rejected", info)
        if not info["alive"] and not info["done"]:
            problem(name, "queue is not running and did not finish", info)
        progress = (info["log"], info.get("notes_done"), c.get("chat_ok"), c.get("ingested"), c.get("model"))
        stalls[name] = stalls.get(name, 0) + 1 if last.get(name) == progress and info["alive"] else 0
        last[name] = progress
        if stalls[name] >= 4:  # one MuSiQue question can take 20 minutes (the reranker, on long notes)
            problem(name, "no progress for 40 minutes", info)
    try:
        if not seen:
            raise LookupError  # no pod probed yet: P.PROVIDER is not set from a pod record (it defaults to RunPod)
        credit, spend = P.balance()
        if credit < 8:
            print(time.strftime("%H:%M"), f"PROBLEM: credit ${credit:.2f} (spending ${spend:.2f}/h)", flush=True)
            sys.exit(1)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    print(time.strftime("%H:%M"), "all clear", flush=True)
    time.sleep(600)
