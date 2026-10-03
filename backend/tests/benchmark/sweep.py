#!/usr/bin/env python3
"""Run one round of experiments from a spec and write one report.

    python tests/benchmark/sweep.py sweeps/round1.json            # run (safe to re-run: finished runs are skipped)
    python tests/benchmark/sweep.py sweeps/round1.json --plan     # show what would run, and what it would build

A spec names a baseline, the levers to vary and how:

    {"name": "round1", "dataset": "hotpotqa",
     "dev": [0, 20], "holdout": [20, 30],
     "baseline": {"provider": "local", "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-e4b-q4"},
     "vary": {"MAX_LOOP_ITERATIONS": [5, 8], "RERANKER_TOP_K": [5, 20]},
     "design": "one-at-a-time",        // or "grid"
     "evaluator": "full",              // or "retrieval": no answering model, scored on the gold notes
     "rungs": [5, 10, 20], "keep": 0.5, "metric": "answer_f1",
     "index": "hp20-e4b"}              // optional: reuse this snapshot as the baseline's index

Everything runs in sequence: one machine, one model in memory at a time. Breadth comes from not repeating
work. Variants that share their extract and index levers share one index, built once. Unchanged model calls
are replayed from the cache, so the questions a variant already answered in a smaller rung cost nothing
again. Each rung keeps the better part of the field, so full-size runs are spent only on survivors. The
held-out slice is scored once, at the end, for the baseline and the winners: no variant is ever tuned on it.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
import signal
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
from compare import bootstrap_ci, mcnemar_exact  # noqa: E402
from levers import BY_NAME  # noqa: E402

BACKEND = BENCH.parent.parent
REPO = BACKEND.parent
INDEX_STAGES = ("extract", "index")


def variants(spec: dict) -> list[dict]:
    """The baseline first, then each variant as a full lever -> value mapping with a ``name``."""
    for lever in [*spec["baseline"], *spec.get("vary", {}), *(k for combo in spec.get("also", []) for k in combo)]:
        if lever not in BY_NAME:
            raise SystemExit(f"unknown lever {lever!r}; declare it in levers.py")
    def explicit(levers: dict) -> dict:
        """Levers set to their default say nothing the baseline does not: leave them out, so equal variants are equal."""
        return {k: v for k, v in levers.items() if v != BY_NAME[k].default}

    base = explicit(dict(spec["baseline"]))
    out = [{"name": "base", "levers": base}]
    vary = spec.get("vary", {})
    if spec.get("design", "one-at-a-time") == "grid":
        for combo in itertools.product(*vary.values()):
            levers = explicit({**spec["baseline"], **dict(zip(vary, combo))})
            if levers != base:
                changed = [f"{k}={v}" for k, v in zip(vary, combo) if levers.get(k, BY_NAME[k].default) != base.get(k, BY_NAME[k].default)]
                out.append({"name": ",".join(changed), "levers": levers})
    else:
        for lever, values in vary.items():
            for value in values:
                if base.get(lever, BY_NAME[lever].default) != value:
                    out.append({"name": f"{lever}={value}", "levers": explicit({**base, lever: value})})
    # "also": settings that only make sense together (Qwen as extractor with its thinking switched off), one variant each.
    for combo in spec.get("also", []):
        out.append({"name": ",".join(f"{k}={v}" for k, v in combo.items()), "levers": explicit({**base, **combo})})
    return out


def index_name(levers: dict, spec: dict) -> str:
    """Snapshot name for the index these levers need: only extract and index levers take part."""
    span = [spec["dev"][0], max(spec["dev"][1], spec.get("holdout", spec["dev"])[1])]
    parts = {k: v for k, v in sorted(levers.items()) if BY_NAME[k].stage in INDEX_STAGES or k == "provider"}
    base = {k: v for k, v in sorted(spec["baseline"].items()) if BY_NAME[k].stage in INDEX_STAGES or k == "provider"}
    if spec.get("index") and parts == base:
        return spec["index"]  # an existing snapshot stands in for the baseline's index; it must cover the span
    digest = hashlib.sha256(json.dumps([spec["dataset"], span, parts], sort_keys=True).encode()).hexdigest()[:8]
    return f"idx-{spec['dataset']}-{digest}"


def lever_args(levers: dict, *, stages: tuple[str, ...] | None = None) -> list[str]:
    """experiment.py arguments that apply ``levers`` (optionally only those of some stages)."""
    args: list[str] = []
    for name, value in levers.items():
        lever = BY_NAME[name]
        if stages is not None and lever.stage not in stages and name != "provider":
            continue
        if lever.how == "set":
            args += ["--set", f"{name}={value}"]
        elif name == "communities":
            args += ["--communities"] if value else []
        else:
            args += [f"--{name.replace('_', '-')}", str(value)]
    return args


def result_file(spec: dict, run_name: str) -> Path:
    leaf = "retrieval.json" if spec.get("evaluator") == "retrieval" else f"{spec['dataset']}.json"
    return REPO / "Results" / run_name / leaf


def score(path: Path, metric: str) -> float | None:
    if not path.is_file():
        return None
    rows = json.loads(path.read_text())["results"]
    return sum(float(r.get(metric) or 0) for r in rows) / len(rows) if rows else None


def survivors(scored: list[tuple[str, float | None]], keep: float) -> list[str]:
    """Names to carry into the next rung: the baseline always, then the best of the rest."""
    ranked = sorted((s for s in scored if s[0] != "base" and s[1] is not None), key=lambda s: s[1], reverse=True)
    return ["base"] + [name for name, _ in ranked[: math.ceil(len(ranked) * keep)]]


_planned: set[str] = set()
_lanes: "queue.Queue[int]" = queue.Queue()
_index_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_stop = threading.Event()
SERVE = 0  # parallel slots of a llama.cpp model server per experiment (0 = in-process, one request at a time)


def experiment(name: str, args: list[str], proof: Path, plan: bool, lane: int = 0) -> None:
    if name in _planned:
        return  # already listed in this dry run
    if proof.exists():
        print(f"== skip {name} (done)", flush=True)
        return
    if _stop.is_set():
        return
    print("== " + ("would run" if plan else f"run [lane {lane}]") + f" {name}: {' '.join(args)}", flush=True)
    if plan:
        _planned.add(name)
        return
    serve = ["--serve", str(SERVE)] if SERVE else []
    code = subprocess.run([sys.executable, "tests/benchmark/experiment.py", name, "--lane", str(lane), *args, *serve],
                          cwd=BACKEND, check=False).returncode
    if code >= 128 or code < 0:
        _stop.set()
        raise SystemExit(f"== {name} was interrupted; stopping the sweep")


def index_run(name: str) -> str:
    """Index builds live in one place, so any spec that needs the same index reuses it."""
    return f"_indexes/{name}"


def ensure_index(variant: dict, spec: dict, plan: bool, lane: int = 0) -> str:
    name = index_name(variant["levers"], spec)
    start = spec["dev"][0]
    end = max(spec["dev"][1], spec.get("holdout", spec["dev"])[1])
    with _locks_guard:
        lock = _index_locks.setdefault(name, threading.Lock())
    with lock:  # two lanes never build the same index; the second waits and then finds the snapshot
        experiment(
            index_run(name),
            ["--dataset", spec["dataset"], "--offset", str(start), "--questions", str(end - start), "--fresh", "--ingest",
             "--no-eval", "--download", "--snapshot", name, *lever_args(variant["levers"], stages=INDEX_STAGES)],
            REPO / "snapshots" / name, plan, lane,
        )
    return name


def evaluate(variant: dict, spec: dict, label: str, offset: int, questions: int, plan: bool, lane: int = 0) -> Path:
    run_name = f"{spec['name']}/{variant['name']}/{label}"
    args = ["--dataset", spec["dataset"], "--offset", str(offset), "--questions", str(questions),
            "--restore", index_name(variant["levers"], spec), "--download", "--evaluator", spec.get("evaluator", "full"),
            *lever_args({k: v for k, v in variant["levers"].items() if k != "communities"})]
    experiment(run_name, args, result_file(spec, run_name), plan, lane)
    return result_file(spec, run_name)


def on_a_lane(work):
    """Run ``work(lane)`` on a free lane, holding it until the work is done."""
    lane = _lanes.get()
    try:
        return work(lane)
    finally:
        _lanes.put(lane)


def run_rung(pool, field, alive, spec, label, offset, n, plan) -> list[tuple[str, Path]]:
    """Every variant of one rung. Variants sharing an index run one after another on one lane; groups run side by side."""
    groups: dict[str, list[dict]] = {}
    for variant in field:
        if variant["name"] in alive:
            groups.setdefault(index_name(variant["levers"], spec), []).append(variant)

    def group(members):
        def work(lane):
            ensure_index(members[0], spec, plan, lane)
            return [(v["name"], evaluate(v, spec, label, offset, n, plan, lane)) for v in members]
        return work

    if plan:
        results = [group(members)(0) for members in groups.values()]
    else:
        results = [f.result() for f in [pool.submit(on_a_lane, group(m)) for m in groups.values()]]
    by_name = dict(row for rows in results for row in rows)
    return [(v["name"], by_name[v["name"]]) for v in field if v["name"] in by_name]


def run_spec(spec: dict, pool, plan: bool) -> None:
    metric = spec.get("metric", "context_recall" if spec.get("evaluator") == "retrieval" else "answer_f1")
    spec["metric"] = metric
    field = variants(spec)
    start, end = spec["dev"]
    rungs = [min(r, end - start) for r in spec.get("rungs", [end - start])]
    print(f"== {spec['name']}: {len(field)} variants, {len({index_name(v['levers'], spec) for v in field})} distinct index(es), rungs {rungs}", flush=True)
    report = [f"# Sweep {spec['name']}\n", f"Dataset {spec['dataset']}, dev questions {start} to {end}, evaluator {spec.get('evaluator', 'full')}, metric {metric}.",
              f"Baseline: `{json.dumps(spec['baseline'])}`", f"Varied: `{json.dumps(spec.get('vary', {}))}` ({spec.get('design', 'one-at-a-time')})"]
    alive = [v["name"] for v in field]
    for rung in rungs:
        rows = run_rung(pool, field, alive, spec, f"r{rung}", start, rung, plan)
        report += table(f"Rung: first {rung} dev questions ({len(rows)} variants)", rows, spec)
        if rung != rungs[-1]:
            alive = survivors([(name, score(path, metric)) for name, path in rows], spec.get("keep", 0.5))
            report.append(f"\nKept for the next rung: {', '.join(alive)}")
    if spec.get("holdout"):
        h0, h1 = spec["holdout"]
        final = survivors([(n, score(result_file(spec, f"{spec['name']}/{n}/r{rungs[-1]}"), metric)) for n in alive], spec.get("keep", 0.5))
        rows = run_rung(pool, field, final, spec, "holdout", h0, h1 - h0, plan)
        report += table(f"Held-out questions {h0} to {h1} (never used to choose)", rows, spec)
    if not plan:
        out = REPO / "Results" / spec["name"] / "report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(report) + "\n")
        print(f"== report: {out}", flush=True)


def prefetch(specs: list[dict], plan: bool) -> None:
    """Download every model the specs need once, before lanes start: lanes downloading the same file at once corrupt it."""
    combos: dict[tuple, set[str]] = {}
    for spec in specs:
        for variant in variants(spec):
            levers = variant["levers"]
            key = (levers.get("EMBED_MODEL_ID"), levers.get("RERANK_MODEL_ID"))
            combos.setdefault(key, set()).update(m for m in (levers.get("chat_model"), levers.get("ingestion_model")) if m)
    for (embed, rerank), models in combos.items():
        sets = [a for k, v in (("EMBED_MODEL_ID", embed), ("RERANK_MODEL_ID", rerank)) if v for a in ("--set", f"{k}={v}")]
        print(f"== prefetch {sorted(models)} with {sets or 'default embed/reranker'}", flush=True)
        if plan:
            continue
        # Downloads from Hugging Face sometimes stop short; the backend resumes them on the next attempt.
        for attempt in range(1, 4):
            done = subprocess.run([sys.executable, "tests/benchmark/experiment.py", "_prefetch", "--prefetch", *sorted(models), *sets],
                                  cwd=BACKEND).returncode == 0
            if done:
                break
            print(f"== prefetch attempt {attempt} failed", flush=True)
        else:
            sys.exit("== prefetch failed 3 times; stopping")


def paired(base: Path, other: Path, metric: str) -> str:
    a = {r["test_id"]: r for r in json.loads(base.read_text())["results"]}
    b = {r["test_id"]: r for r in json.loads(other.read_text())["results"]}
    ids = [i for i in a if i in b]
    if not ids or "exact_match" not in a[ids[0]]:
        return ""
    won = sum(1 for i in ids if b[i]["exact_match"] and not a[i]["exact_match"])
    lost = sum(1 for i in ids if a[i]["exact_match"] and not b[i]["exact_match"])
    lo, hi = bootstrap_ci([float(b[i].get(metric) or 0) - float(a[i].get(metric) or 0) for i in ids])
    real = mcnemar_exact(lost, won) < 0.05 and (lo > 0 or hi < 0)
    return f"+{won}/-{lost} EM, {metric} CI {lo:+.3f} to {hi:+.3f}, {'real' if real else 'within noise'}"


def table(title: str, rows: list[tuple], spec: dict) -> list[str]:
    metric = spec.get("metric", "answer_f1")
    out = [f"\n## {title}\n", f"| variant | {metric} | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |", "|---|---|---|---|---|---|---|---|---|"]
    base_path = next((path for name, path in rows if name == "base"), None)
    for name, path in rows:
        if not path.is_file():
            out.append(f"| {name} | not run | | | | | | | |")
            continue
        data = json.loads(path.read_text())
        res = data["results"]
        n = len(res) or 1
        config = path.parent / "config.json"
        bad = json.loads(config.read_text()).get("invalid_replies", {}) if config.is_file() else {}
        recall = sum(float(r.get("retrieval_recall", r.get("candidate_recall")) or 0) for r in res) / n
        indexed = f"{sum(float(r['gold_ingested']) for r in res) / n:.3f}" if "gold_ingested" in res[0] else "n/a"
        em = f"{sum(bool(r.get('exact_match')) for r in res) / n:.0%}" if "exact_match" in res[0] else "n/a"
        versus = paired(base_path, path, metric) if base_path and base_path.is_file() and name != "base" else ""
        levers = next(v["levers"] for v in variants(spec) if v["name"] == name)
        idx = REPO / "Results" / index_run(index_name(levers, spec)) / "config.json"
        built = json.loads(idx.read_text()) if idx.is_file() else {}
        notes = built.get("notes") or {}
        build = f"{built.get('ingest_seconds', '')} ({notes.get('ingested', '?')}/{notes.get('total', '?')} notes)" if built else ""
        out.append(f"| {name} | {score(path, metric):.3f} | {em} | {recall:.3f} | {indexed} | {sum(r['total_time_ms'] for r in res) / n / 1000:.0f} | {build} | "
                   f"{sum(bad.values())} {bad or ''} | {versus} |")
    return out


def stop_on_signal() -> None:
    """A process started in the background inherits SIGINT=ignore, so ``pkill -INT`` would do nothing.
    Both signals become KeyboardInterrupt, which unwinds through ``finally`` and shuts the server down."""
    def interrupt(signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)


def load(path: str) -> tuple[list[dict], int, int]:
    """A spec, or a bundle {"lanes": N, "serve": SLOTS, "specs": [paths]} that runs several specs side by side."""
    raw = json.loads(Path(path).read_text())
    if "specs" in raw:
        specs = [json.loads((Path(path).parent / p).read_text()) for p in raw["specs"]]
        return specs, int(raw.get("lanes", 1)), int(raw.get("serve", 0))
    return [raw], 1, int(raw.get("serve", 0))


def main() -> None:
    stop_on_signal()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="a sweep spec, or a bundle of specs")
    ap.add_argument("--plan", action="store_true", help="print what would run and stop")
    ap.add_argument("--lanes", type=int, help="experiments at once on this machine (overrides the bundle)")
    ap.add_argument("--serve", type=int, help="parallel slots per model server (overrides the bundle; 0 = in-process)")
    args = ap.parse_args()
    specs, lanes, serve = load(args.spec)
    global SERVE  # noqa: PLW0603
    SERVE = serve if args.serve is None else args.serve
    lanes = args.lanes or lanes
    if SERVE and lanes > 1:
        # The parallelism is in the server's slots, and one model is resident at a time.
        print(f"== serving with {SERVE} slots: one lane instead of {lanes}", flush=True)
        lanes = 1
    for lane in range(lanes):
        _lanes.put(lane)
    print(f"== {len(specs)} spec(s) on {lanes} lane(s)", flush=True)
    prefetch(specs, args.plan)
    with ThreadPoolExecutor(max_workers=lanes) as pool, ThreadPoolExecutor(max_workers=len(specs)) as drivers:
        futures = [drivers.submit(run_spec, spec, pool, args.plan) for spec in specs]
        try:
            for future in futures:
                future.result()
        except (KeyboardInterrupt, SystemExit):
            _stop.set()
            raise


if __name__ == "__main__":
    main()
