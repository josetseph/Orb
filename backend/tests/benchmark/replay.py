#!/usr/bin/env python3
"""Retrieval replay: re-decide which reranked candidates survive, offline, and score
the result against the gold notes. No model and no server: it reads the traces a
run already recorded.

    python tests/benchmark/replay.py ../Results/baseline/hotpotqa.json

It answers two questions a run's headline number cannot:
  1. Where are questions lost? Gold note never surfaced (ingestion / graph / query
     problem), surfaced but cut (filter problem), or retrieved but answered wrong
     (answering-model problem).
  2. Would other RERANKER_TOP_K / RERANKER_SCORE_THRESHOLD / context-cap values
     have kept more gold notes?

Limit: the queries are the ones the model actually issued. Changing a filter also
changes what the model reads and so what it asks next; the sweep is exact for the
first search and an estimate after it. Confirm a winner with a real run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import expected_note_names, match_titles  # noqa: E402

TOP_KS = (3, 5, 8, 10, 15, 20, 30, 10_000)
THRESHOLDS = (0.0, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5)
CAPS = (4, 6, 8, 12, 10_000)


def simulate(events: list[dict], top_k: int, threshold: float, cap: int, titles: dict[str, str]) -> list[str]:
    """Note titles the pipeline would cite under these settings (mirrors the loop + chat truncation)."""
    docs: dict[str, dict] = {}
    for ev in events:
        if ev.get("kind") != "rerank":
            continue
        kept = ev["candidates"][:top_k]  # already sorted by score
        if ev.get("stage") == "search":  # graph-expansion results are not thresholded
            kept = [c for c in kept if c["score"] >= threshold]
        for c in kept:
            seen = docs.setdefault(c["name"], {"score": c["score"], "notes": []})
            seen["notes"] += [n for n in c["notes"] if n not in seen["notes"]]
    best = sorted(docs.values(), key=lambda d: d["score"], reverse=True)[:cap]
    out: list[str] = []
    for d in best:
        for note_id in d["notes"]:
            title = titles.get(note_id, "")
            if title and title not in out:
                out.append(title)
    return out


def prf(rows: list[tuple[list[str], set[str]]]) -> tuple[float, float, float]:
    ps, rs = [], []
    for got, gold in rows:
        hit = len(match_titles(got, gold))
        ps.append(hit / len(got) if got else 0.0)
        rs.append(hit / len(gold) if gold else 0.0)
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="results JSON written by evaluate.py")
    ap.add_argument("--top", type=int, default=12, help="rows of the sweep to print")
    args = ap.parse_args()

    run = json.loads(Path(args.results).read_text())
    manifest = json.loads((Path(__file__).parent / f"{run['dataset']}_manifest.json").read_text())
    cases = {tc["id"]: tc for tc in manifest["test_cases"]}
    titles = run.get("note_titles") or {}
    rows = [r for r in run["results"] if r.get("trace") and r["test_id"] in cases]
    if not rows:
        sys.exit("No traces in this file. Re-run evaluate.py from this branch to record them.")
    knobs = (run.get("config") or {}).get("knobs") or {}
    live = (knobs.get("RERANKER_TOP_K", 10), knobs.get("RERANKER_SCORE_THRESHOLD", 0.05), 6)

    # 1. where questions are lost
    retrieval_only = run.get("mode") == "retrieval"
    buckets = ({"gold notes kept": [], "never surfaced": [], "surfaced, then cut": []} if retrieval_only
               else {"answered": [], "never surfaced": [], "surfaced, then cut": [], "retrieved, answered wrong": []})
    for r in rows:
        gold = expected_note_names(cases[r["test_id"]])
        everything = simulate(r["trace"], 10_000, 0.0, 10_000, titles)
        kept = simulate(r["trace"], *live, titles)
        if not retrieval_only and (r["exact_match"] or r.get("answer_contains_expected")):
            buckets["answered"].append(r)
        elif len(match_titles(everything, gold)) < len(gold):
            buckets["never surfaced"].append(r)
        elif len(match_titles(kept, gold)) < len(gold):
            buckets["surfaced, then cut"].append(r)
        else:
            buckets["gold notes kept" if retrieval_only else "retrieved, answered wrong"].append(r)
    print(f"\n{len(rows)} questions with traces  |  recorded settings: top_k={live[0]} threshold={live[1]} cap={live[2]}\n")
    print("Where questions are lost")
    for name, items in buckets.items():
        print(f"  {name:28s} {len(items):4d}  {len(items) / len(rows):6.1%}")
    for name in ("never surfaced", "surfaced, then cut", "retrieved, answered wrong"):
        for r in buckets.get(name, [])[:3]:
            detail = f"query {r['query']!r}" if retrieval_only else f"expected {r['expected_answer']!r}, got {r['actual_answer'][:40]!r}"
            print(f"    [{name}] {r['question'][:90]}  ({detail})")

    invalid: dict[str, int] = {}
    for row in rows:
        for event in row["trace"]:
            if event.get("kind") == "invalid_output":
                invalid[event["stage"]] = invalid.get(event["stage"], 0) + 1
    if invalid:
        print(f"\nUnusable model replies: {sum(invalid.values())}  " + ", ".join(f"{k}: {v}" for k, v in sorted(invalid.items())))

    calls = [e for row in rows for e in row["trace"] if e.get("kind") == "llm_call"]
    if calls:
        fresh = [e for e in calls if not e["cached"]]
        print(f"Model calls: {len(calls)} ({len(calls) - len(fresh)} replayed from the cache), "
              f"{sum(e['seconds'] for e in fresh):.0f} s generating")

    if not retrieval_only:
        steps = [sum(1 for e in row["trace"] if e.get("kind") == "step") for row in rows]
        searches = [sum(1 for e in row["trace"] if e.get("kind") == "rerank" and e.get("stage") == "search") for row in rows]
        exhausted = sum(1 for row in rows if not any(e.get("can_answer") for e in row["trace"] if e.get("kind") == "step"))
        print(f"\nLoop: {sum(steps) / len(rows):.1f} model steps and {sum(searches) / len(rows):.1f} searches per question; "
              f"{exhausted} of {len(rows)} ran out of iterations without answering")

    # 2. filter sweep
    gold_sets = [expected_note_names(cases[r["test_id"]]) for r in rows]
    sweep = []
    for k in TOP_KS:
        for t in THRESHOLDS:
            for cap in CAPS:
                p, rc, f1 = prf([(simulate(r["trace"], k, t, cap, titles), g) for r, g in zip(rows, gold_sets)])
                sweep.append((f1, rc, p, k, t, cap))
    sweep.sort(reverse=True)
    show = lambda v: "all" if v >= 10_000 else v  # noqa: E731
    print(f"\nFilter sweep (best {args.top} of {len(sweep)} by retrieval F1)\n  {'top_k':>6} {'thresh':>7} {'cap':>5} {'P':>7} {'R':>7} {'F1':>7}")
    for f1, rc, p, k, t, cap in sweep[: args.top]:
        mark = "  <- recorded" if (k, t, cap) == live else ""
        print(f"  {show(k)!s:>6} {t:>7} {show(cap)!s:>5} {p:>7.3f} {rc:>7.3f} {f1:>7.3f}{mark}")
    now = next((s for s in sweep if s[3:] == live), None)
    if now:
        print(f"  recorded settings: P={now[2]:.3f} R={now[1]:.3f} F1={now[0]:.3f}")
    ceiling = prf([(simulate(r["trace"], 10_000, 0.0, 10_000, titles), g) for r, g in zip(rows, gold_sets)])
    print(f"  recall ceiling with no filtering at all: {ceiling[1]:.3f}  (above this needs better queries, graph or ingestion)")


if __name__ == "__main__":
    main()
