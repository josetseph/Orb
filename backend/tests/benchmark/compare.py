#!/usr/bin/env python3
"""Compare runs question by question. The first file is the baseline.

    python tests/benchmark/compare.py ../Results/baseline/hotpotqa.json ../Results/qwen-chat/hotpotqa.json ...

At N=100 the standard error on exact match is about five points, so a headline
gap that small is noise. This pairs the runs on the same questions instead:
McNemar's exact test on the questions that flipped, and a bootstrap interval on
the per-question F1 difference.
"""

from __future__ import annotations

import argparse
import json
import random
from math import comb
from pathlib import Path


def load(path: str) -> dict:
    run = json.loads(Path(path).read_text())
    # Results/<run>/<dataset>.json is named by its folder; anything else by its file
    run["label"] = Path(path).parent.name if Path(path).stem in ("hotpotqa", "musique", "synthesis") else Path(path).stem
    run["by_id"] = {r["test_id"]: r for r in run["results"]}
    return run


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact p-value for b vs c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2**n)


def bootstrap_ci(diffs: list[float], rounds: int = 4000) -> tuple[float, float]:
    rng = random.Random(0)
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(rounds))
    return means[int(0.025 * rounds)], means[int(0.975 * rounds)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="results JSON files; first is the baseline")
    args = ap.parse_args()
    runs = [load(p) for p in args.runs]

    print(f"\n{'run':24s} {'chat model':26s} {'N':>4} {'EM':>7} {'F1':>7} {'contains':>9} {'recall':>7} {'s/q':>7}")
    for run in runs:
        rows = run["results"]
        n = len(rows) or 1
        model = ((run.get("config") or {}).get("chat_model") or "?")[:26]
        print(f"{run['label'][:24]:24s} {model:26s} {len(rows):>4} "
              f"{sum(r['exact_match'] for r in rows) / n:>7.1%} {sum(r.get('answer_f1', 0) for r in rows) / n:>7.3f} "
              f"{sum(bool(r.get('answer_contains_expected')) for r in rows) / n:>9.1%} "
              f"{sum(r.get('retrieval_recall') or 0 for r in rows) / n:>7.3f} {sum(r['total_time_ms'] for r in rows) / n / 1000:>7.1f}")

    base = runs[0]
    for other in runs[1:]:
        ids = [i for i in base["by_id"] if i in other["by_id"]]
        if not ids:
            print(f"\n{other['label']}: no questions in common with {base['label']}")
            continue
        lost = [i for i in ids if base["by_id"][i]["exact_match"] and not other["by_id"][i]["exact_match"]]
        won = [i for i in ids if not base["by_id"][i]["exact_match"] and other["by_id"][i]["exact_match"]]
        diffs = [other["by_id"][i].get("answer_f1", 0) - base["by_id"][i].get("answer_f1", 0) for i in ids]
        lo, hi = bootstrap_ci(diffs)
        p = mcnemar_exact(len(lost), len(won))
        verdict = "real difference" if p < 0.05 and (lo > 0 or hi < 0) else "within noise"
        print(f"\n{other['label']} vs {base['label']}  ({len(ids)} shared questions)")
        print(f"  exact match: gained {len(won)}, lost {len(lost)}  (McNemar exact p = {p:.3f})")
        print(f"  F1 change:   {sum(diffs) / len(diffs):+.3f}  (95% CI {lo:+.3f} to {hi:+.3f})  ->  {verdict}")
        for tag, group, src in (("gained", won, other), ("lost", lost, other)):
            for i in group[:5]:
                r = src["by_id"][i]
                print(f"    {tag}: {r['question'][:80]}  (expected {r['expected_answer']!r}, got {r['actual_answer'][:40]!r})")


if __name__ == "__main__":
    main()
