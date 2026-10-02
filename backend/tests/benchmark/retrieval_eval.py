#!/usr/bin/env python3
"""Retrieval-only evaluation: run a bank of queries through search-and-expand, with no answering model.

    python tests/benchmark/retrieval_eval.py --dataset hotpotqa --limit 20 -o ../Results/r1/retrieval.json
    python tests/benchmark/retrieval_eval.py --dataset hotpotqa --queries-from ../Results/base/hotpotqa.json -o ...

The bank is each benchmark question, plus (with --queries-from) every follow-up query a past run's model
issued for that question. Each query is scored against that question's gold notes. The output has the same
shape as an evaluate.py result, so replay.py reads it: where gold notes are lost, and the filter sweep.
One query costs a query analysis call (cached after the first time), an embedding, a search and a rerank.
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
from evaluate import fetch_config, gold_groups, load_note_files, score_retrieval  # noqa: E402


def query_bank(cases: list[dict], past_runs: list[str]) -> list[tuple[dict, str]]:
    """(test case, query) pairs: the question itself, then each distinct recorded follow-up query."""
    recorded: dict[str, list[str]] = {}
    for path in past_runs:
        for row in json.loads(Path(path).read_text())["results"]:
            for event in row.get("trace") or []:
                if event.get("kind") == "step" and event.get("next_query"):
                    recorded.setdefault(row["test_id"], []).append(event["next_query"])
    bank = []
    for case in cases:
        for query in dict.fromkeys([case["question"], *recorded.get(case["id"], [])]):
            bank.append((case, query))
    return bank


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["hotpotqa", "musique"], required=True)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--queries-from", nargs="*", default=[], metavar="RESULTS.json")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--output", "-o", required=True)
    args = ap.parse_args()

    manifest = json.loads((Path(__file__).parent / f"{args.dataset}_manifest.json").read_text())
    cases = manifest["test_cases"][args.offset : args.offset + args.limit if args.limit else None]
    bank = query_bank(cases, args.queries_from)
    titles = {n["id"]: n.get("title") or "" for n in httpx.get(f"{args.base_url}/api/v1/notes", timeout=120).json()}
    note_files = load_note_files(args.dataset)

    rows = []
    with httpx.Client(timeout=1800) as client:
        for case, query in tqdm(bank, desc="Retrieving"):
            t0 = time.perf_counter()
            row = {"test_id": case["id"], "question": case["question"], "query": query,
                   "is_question": query == case["question"], "trace": [], "context_notes": [], "error": None}
            try:
                reply = client.post(f"{args.base_url}/api/v1/benchmark/retrieve", json={"query": query})
                reply.raise_for_status()
                row["trace"] = reply.json()["trace"]
                row["context_notes"] = reply.json()["context_notes"]
            except Exception as exc:  # pylint: disable=broad-exception-caught
                row["error"] = str(exc)
            groups = gold_groups(case)
            candidates = [n for e in row["trace"] if e.get("kind") == "rerank" for c in e["candidates"] for n in c["notes"]]
            # Every reranked candidate, before the cut; then what the model would actually read.
            row["candidate_recall"] = score_retrieval(candidates, groups, note_files)["retrieval_recall"]
            scored = score_retrieval(row["context_notes"], groups, note_files)
            row["context_recall"] = row["retrieval_recall"] = scored["retrieval_recall"]
            row["gold_ingested"] = scored["gold_ingested"]
            row["total_time_ms"] = (time.perf_counter() - t0) * 1000
            rows.append(row)

    n = len(rows) or 1
    out = {"timestamp": datetime.now().isoformat(), "dataset": args.dataset, "mode": "retrieval",
           "num_tests": len(rows), "config": fetch_config(args.base_url), "note_titles": titles, "note_files": note_files,
           "metrics": {"candidate_recall": sum(r["candidate_recall"] for r in rows) / n,
                       "context_recall": sum(r["context_recall"] for r in rows) / n,
                       "gold_ingested": sum(r["gold_ingested"] for r in rows) / n,
                       "errors": sum(1 for r in rows if r["error"])},
           "results": rows}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2))
    m = out["metrics"]
    print(f"{len(rows)} queries | gold among the candidates {m['candidate_recall']:.1%}, in the model's context "
          f"{m['context_recall']:.1%}, in the index {m['gold_ingested']:.1%} -> {args.output}")


if __name__ == "__main__":
    main()
