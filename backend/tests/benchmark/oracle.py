#!/usr/bin/env python3
"""Oracle evidence: answer each question from given notes in one step, with no index and no retrieval.

``--mode gold``: only the question's supporting notes. Its score is the answering model's ceiling: what is left
wrong when retrieval is perfect. ``--mode all``: every note of the question, distractors included (HotpotQA 10,
MuSiQue 20): long-context reading instead of retrieval.

    python tests/benchmark/oracle.py --dataset musique --offset 0 --limit 50 --mode gold -o ../Results/x/musique.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import fetch_config, gold_groups, score_answer  # noqa: E402

HERE = Path(__file__).resolve().parent


def evidence(test_case: dict, mode: str) -> list[str]:
    """Note files the model reads: one file per gold group (its first), or every note of the question."""
    if mode == "gold":
        return [group[0] for group in gold_groups(test_case) if group]
    return list(test_case.get("notes") or test_case["all_notes"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, choices=["hotpotqa", "musique"])
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--mode", required=True, choices=["gold", "all"])
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--output", "-o", required=True)
    args = ap.parse_args()

    cases = json.loads((HERE / f"{args.dataset}_manifest.json").read_text())["test_cases"]
    cases = cases[args.offset : args.offset + args.limit]
    notes_dir = HERE / f"{args.dataset}_notes"

    def one(tc: dict) -> dict:
        files = evidence(tc, args.mode)
        docs = [{"text": (notes_dir / f).read_text(encoding="utf-8")} for f in files]
        row = {"test_id": tc["id"], "question": tc["question"], "expected_answer": tc["answer"],
               "evidence_notes": files, "actual_answer": "", "error": None}
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=3600) as client:
                r = client.post(f"{args.base_url}/api/v1/benchmark/synthesize", json={"question": tc["question"], "docs": docs})
                r.raise_for_status()
                row["actual_answer"] = r.json()["answer"]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            row["error"] = str(exc)  # scored 0, like any errored question
        row["total_time_ms"] = (time.perf_counter() - t0) * 1000
        row.update(score_answer(tc["answer"], row["actual_answer"]))
        return row

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        rows = list(pool.map(one, cases))

    n = len(rows) or 1
    out = {
        "timestamp": datetime.now().isoformat(), "dataset": args.dataset, "mode": f"oracle-{args.mode}",
        "num_tests": len(rows), "config": fetch_config(args.base_url),
        "metrics": {"answer_exact_match": sum(bool(r["exact_match"]) for r in rows) / n,
                    "answer_f1": sum(r["answer_f1"] for r in rows) / n,
                    "error_count": sum(1 for r in rows if r["error"])},
        "results": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    m = out["metrics"]
    print(f"oracle-{args.mode}: EM {m['answer_exact_match']:.1%} | F1 {m['answer_f1']:.3f} | errors {m['error_count']} -> {args.output}")


if __name__ == "__main__":
    main()
