#!/usr/bin/env python3
"""Synthesis replay: answer every question of an earlier run from the evidence that
run retrieved. Retrieval is frozen, so the only thing that differs from the source
run is the answering model / prompt on the server you point this at.

    python tests/benchmark/synthesis.py ../Results/baseline/hotpotqa.json --output ../Results/qwen-synth/synthesis.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import fetch_config, score_answer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="results JSON from evaluate.py (must carry per-question context)")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--output", "-o", required=True)
    args = ap.parse_args()

    source = json.loads(Path(args.source).read_text())
    rows = []
    with httpx.Client(timeout=1800) as client:
        for src in tqdm(source["results"], desc="Synthesizing"):
            docs = [d for d in src.get("context") or [] if d.get("text")]
            row = {k: src[k] for k in ("test_id", "question", "expected_answer")}
            # Retrieval is inherited verbatim from the source run.
            row.update({k: src.get(k) for k in ("retrieved_note_titles", "retrieval_precision", "retrieval_recall")})
            row.update(actual_answer="", error=None, total_time_ms=0.0, **score_answer(src["expected_answer"], ""))
            if not docs:
                row["error"] = "source run retrieved no evidence"
            else:
                t0 = time.perf_counter()
                try:
                    r = client.post(f"{args.base_url}/api/v1/benchmark/synthesize",
                                    json={"question": src["question"], "docs": docs})
                    r.raise_for_status()
                    row["actual_answer"] = r.json()["answer"]
                    row.update(score_answer(src["expected_answer"], row["actual_answer"]))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    row["error"] = str(exc)
                row["total_time_ms"] = (time.perf_counter() - t0) * 1000
            rows.append(row)

    n = len(rows) or 1
    out = {
        "timestamp": datetime.now().isoformat(), "dataset": source.get("dataset"), "mode": "synthesis-replay",
        "source": str(args.source), "num_tests": len(rows), "config": fetch_config(args.base_url),
        "metrics": {"exact_match_rate": sum(r["exact_match"] for r in rows) / n,
                    "avg_answer_f1": sum(r["answer_f1"] for r in rows) / n},
        "results": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f"EM {out['metrics']['exact_match_rate']:.1%} | F1 {out['metrics']['avg_answer_f1']:.3f} -> {args.output}")


if __name__ == "__main__":
    main()
