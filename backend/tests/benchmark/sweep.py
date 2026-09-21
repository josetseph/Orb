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
     "rungs": [5, 10, 20], "keep": 0.5, "metric": "answer_f1"}

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
    for lever in list(spec["baseline"]) + list(spec.get("vary", {})):
        if lever not in BY_NAME:
            raise SystemExit(f"unknown lever {lever!r}; declare it in levers.py")
    base = dict(spec["baseline"])
    out = [{"name": "base", "levers": base}]
    vary = spec.get("vary", {})
    if spec.get("design", "one-at-a-time") == "grid":
        for combo in itertools.product(*vary.values()):
            levers = {**base, **dict(zip(vary, combo))}
            if levers != base:
                out.append({"name": ",".join(f"{k}={v}" for k, v in zip(vary, combo)), "levers": levers})
    else:
        for lever, values in vary.items():
            for value in values:
                if base.get(lever, BY_NAME[lever].default) != value:
                    out.append({"name": f"{lever}={value}", "levers": {**base, lever: value}})
    return out


def index_name(levers: dict, spec: dict) -> str:
    """Snapshot name for the index these levers need: only extract and index levers take part."""
    span = [spec["dev"][0], max(spec["dev"][1], spec.get("holdout", spec["dev"])[1])]
    parts = {k: v for k, v in sorted(levers.items()) if BY_NAME[k].stage in INDEX_STAGES or k == "provider"}
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


def experiment(name: str, args: list[str], proof: Path, plan: bool) -> None:
    if name in _planned:
        return  # already listed in this dry run
    if proof.exists():
        print(f"== skip {name} (done)")
        return
    cmd = [sys.executable, "tests/benchmark/experiment.py", name, *args]
    print("== " + ("would run" if plan else "run") + f" {name}: {' '.join(args)}", flush=True)
    if plan:
        _planned.add(name)
        return
    code = subprocess.run(cmd, cwd=BACKEND, check=False).returncode
    if code >= 128:
        raise SystemExit(f"== {name} was interrupted; stopping the sweep")


def ensure_index(variant: dict, spec: dict, plan: bool) -> str:
    name = index_name(variant["levers"], spec)
    start = spec["dev"][0]
    end = max(spec["dev"][1], spec.get("holdout", spec["dev"])[1])
    experiment(
        f"{spec['name']}/_index/{name}",
        ["--dataset", spec["dataset"], "--offset", str(start), "--questions", str(end - start), "--fresh", "--ingest",
         "--no-eval", "--download", "--snapshot", name, *lever_args(variant["levers"], stages=INDEX_STAGES)],
        REPO / "snapshots" / name, plan,
    )
    return name


def evaluate(variant: dict, spec: dict, label: str, offset: int, questions: int, plan: bool) -> Path:
    run_name = f"{spec['name']}/{variant['name']}/{label}"
    args = ["--dataset", spec["dataset"], "--offset", str(offset), "--questions", str(questions),
            "--restore", index_name(variant["levers"], spec), "--download", "--evaluator", spec.get("evaluator", "full"),
            *lever_args({k: v for k, v in variant["levers"].items() if k != "communities"})]
    experiment(run_name, args, result_file(spec, run_name), plan)
    return result_file(spec, run_name)


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
    out = [f"\n## {title}\n", f"| variant | {metric} | exact match | retrieval recall | s / question | unusable replies | vs base |", "|---|---|---|---|---|---|---|"]
    base_path = next((path for name, path in rows if name == "base"), None)
    for name, path in rows:
        if not path.is_file():
            out.append(f"| {name} | not run | | | | | |")
            continue
        data = json.loads(path.read_text())
        res = data["results"]
        n = len(res) or 1
        config = path.parent / "config.json"
        bad = json.loads(config.read_text()).get("invalid_replies", {}) if config.is_file() else {}
        recall = sum(float(r.get("retrieval_recall", r.get("candidate_recall")) or 0) for r in res) / n
        em = f"{sum(bool(r.get('exact_match')) for r in res) / n:.0%}" if "exact_match" in res[0] else "n/a"
        versus = paired(base_path, path, metric) if base_path and base_path.is_file() and name != "base" else ""
        out.append(f"| {name} | {score(path, metric):.3f} | {em} | {recall:.3f} | {sum(r['total_time_ms'] for r in res) / n / 1000:.0f} | "
                   f"{sum(bad.values())} {bad or ''} | {versus} |")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--plan", action="store_true", help="print what would run and stop")
    args = ap.parse_args()
    spec = json.loads(Path(args.spec).read_text())
    metric = spec.get("metric", "candidate_recall" if spec.get("evaluator") == "retrieval" else "answer_f1")
    spec["metric"] = metric
    field = variants(spec)
    start, end = spec["dev"]
    rungs = [min(r, end - start) for r in spec.get("rungs", [end - start])]
    print(f"== {spec['name']}: {len(field)} variants, {len({index_name(v['levers'], spec) for v in field})} distinct index(es), rungs {rungs}")

    report = [f"# Sweep {spec['name']}\n", f"Dataset {spec['dataset']}, dev questions {start} to {end}, evaluator {spec.get('evaluator', 'full')}, metric {metric}.",
              f"Baseline: `{json.dumps(spec['baseline'])}`", f"Varied: `{json.dumps(spec.get('vary', {}))}` ({spec.get('design', 'one-at-a-time')})"]
    alive = [v["name"] for v in field]
    for rung in rungs:
        rows = []
        for variant in (v for v in field if v["name"] in alive):
            ensure_index(variant, spec, args.plan)
            rows.append((variant["name"], evaluate(variant, spec, f"r{rung}", start, rung, args.plan)))
        report += table(f"Rung: first {rung} dev questions ({len(rows)} variants)", rows, spec)
        if rung != rungs[-1]:
            alive = survivors([(name, score(path, metric)) for name, path in rows], spec.get("keep", 0.5))
            report.append(f"\nKept for the next rung: {', '.join(alive)}")
    if spec.get("holdout"):
        h0, h1 = spec["holdout"]
        final = survivors([(n, score(result_file(spec, f"{spec['name']}/{n}/r{rungs[-1]}"), metric)) for n in alive], spec.get("keep", 0.5))
        rows = [(v["name"], evaluate(v, spec, "holdout", h0, h1 - h0, args.plan)) for v in field if v["name"] in final]
        report += table(f"Held-out questions {h0} to {h1} (never used to choose)", rows, spec)
    if not args.plan:
        out = REPO / "Results" / spec["name"] / "report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(report) + "\n")
        print(f"== report: {out}")


if __name__ == "__main__":
    main()
