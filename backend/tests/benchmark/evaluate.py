#!/usr/bin/env python3
"""
Evaluate Orb retrieval and answer quality against benchmark datasets.

Core metrics:
- Exact Match (EM): strict string match with ground truth
- F1 Score: token-level overlap (standard QA metric)
- Fuzzy Match: semantic similarity using fuzzy string matching
- Retrieval Precision/Recall: context retrieval quality

Usage:
    # Basic evaluation (results saved automatically)
    python tests/benchmark/evaluate.py --dataset hotpotqa --verbose

    # Verbose output with limit
    python tests/benchmark/evaluate.py --dataset musique --verbose --limit 10
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from tqdm import tqdm

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import settings from config


@dataclass
class EvaluationResult:
    """Result of evaluating a single test case."""

    test_id: str
    question: str
    expected_answer: str
    actual_answer: str

    # Answer quality (basic)
    exact_match: bool = False
    fuzzy_match: bool = False
    answer_contains_expected: bool = False
    answer_f1: float = 0.0  # Token-level F1 (standard QA metric)

    # Retrieval quality
    retrieved_contexts: list = field(default_factory=list)
    retrieved_note_titles: list = field(default_factory=list)
    retrieved_note_ids: list = field(default_factory=list)
    expected_notes: list = field(default_factory=list)
    retrieval_precision: float = 0.0
    retrieval_recall: float = 0.0
    gold_ingested: float = 0.0  # share of gold groups that made it into the index at all

    # Timing
    retrieval_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    total_time_ms: float = 0.0

    # Replay inputs: every rerank candidate + loop step, and the evidence the answer cited
    trace: list = field(default_factory=list)
    context: list = field(default_factory=list)

    # Error tracking
    error: Optional[str] = None


def normalize_answer(answer: str) -> str:
    """Normalize answer for comparison."""
    answer = answer.lower().strip()
    answer = re.sub(r"[^\w\s]", "", answer)
    answer = re.sub(r"\s+", " ", answer)
    return answer


def compute_answer_f1(predicted: str, ground_truth: str) -> float:
    """
    Compute token-level F1 score between predicted and ground truth answers.
    This is the standard metric for QA benchmarks (SQuAD, HotpotQA, etc.).
    """
    predicted_tokens = normalize_answer(predicted).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()

    if len(predicted_tokens) == 0 or len(ground_truth_tokens) == 0:
        return 1.0 if predicted_tokens == ground_truth_tokens else 0.0

    common_tokens = set(predicted_tokens) & set(ground_truth_tokens)

    if len(common_tokens) == 0:
        return 0.0

    precision = len(common_tokens) / len(predicted_tokens)
    recall = len(common_tokens) / len(ground_truth_tokens)

    f1 = 2 * (precision * recall) / (precision + recall)
    return f1


def extract_answer_from_response(answer: str) -> str:
    """Extract the main answer from Orb response (first line before reasoning)."""
    # Orb returns: "Answer\n\n**Reasoning:**\n..."
    # We want just the first line(s) before reasoning starts
    lines = answer.split("\n")
    answer_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Stop at reasoning section
        if line.startswith("**Reasoning") or line.startswith("### Reference"):
            break
        answer_lines.append(line)
        # Usually the answer is in the first non-empty line
        if len(answer_lines) >= 1:
            break

    return " ".join(answer_lines).strip()


def fuzzy_match(
    expected: str, actual: str, threshold: float = 0.6, extract_answer: bool = True
) -> bool:
    """
    Check if answers match with some flexibility.

    Args:
        expected: Expected answer
        actual: Actual answer from Orb
        threshold: Jaccard similarity threshold (default 0.6)
        extract_answer: If True (default), extract just first line. If False, use full answer.

    Lowered threshold from 0.8 to 0.6 to account for minor variations
    (e.g., "Robert Erskine Childers" vs "Robert Erskine Childers DSC").

    Note: We extract by default to avoid false positives from answers mentioned
    in reasoning sections (e.g., "World War II" appearing in reasoning text
    when actual answer is "The answer is not in the provided context").
    """
    # Extract just the answer portion (first line before reasoning)
    # This prevents false matches from reasoning/reference sections
    actual_text = extract_answer_from_response(actual) if extract_answer else actual

    expected_norm = normalize_answer(expected)
    actual_norm = normalize_answer(actual_text)

    # Exact match
    if expected_norm == actual_norm:
        return True

    # Expected is substring of actual (most common case)
    if expected_norm in actual_norm:
        return True

    # Actual is substring of expected (handles "DSC" suffix cases)
    if actual_norm in expected_norm:
        # Only match if actual contains most of expected
        if len(actual_norm) >= len(expected_norm) * 0.7:
            return True

    # Remove common filler phrases from actual answer to get core content
    # E.g., "Virginia Woolf was born earlier" -> "Virginia Woolf"
    filler_phrases = [
        "was born earlier",
        "was born first",
        "came first",
        "is the answer",
        "the answer is",
        "is correct",
        "is true",
        "is false",
        "was earlier",
        "was later",
        "was formed by",
        "is located in",
        "is based in",
    ]
    actual_cleaned = actual_norm
    for phrase in filler_phrases:
        actual_cleaned = actual_cleaned.replace(phrase, "").strip()

    # After removing filler, check again
    if expected_norm in actual_cleaned:
        return True
    if actual_cleaned in expected_norm:
        if len(actual_cleaned) >= len(expected_norm) * 0.5:
            return True

    # Special handling for numeric answers (e.g., "3,677 seated" vs "3,677 seats")
    # If both contain the same numbers, consider it a match
    expected_numbers = set(re.findall(r"\d+", expected_norm))
    actual_numbers = set(re.findall(r"\d+", actual_cleaned))
    if expected_numbers and actual_numbers == expected_numbers:
        # Same numbers found - check if words are similar
        expected_words = set(expected_norm.split()) - expected_numbers
        actual_words = set(actual_cleaned.split()) - actual_numbers
        if len(expected_words) <= 2 and len(actual_words) <= 2:
            # Short answers with matching numbers are likely correct
            return True

    # Jaccard similarity for word overlap (using cleaned actual)
    expected_words = set(expected_norm.split())
    actual_words = set(actual_cleaned.split())

    if not expected_words or not actual_words:
        return False

    intersection = expected_words & actual_words

    # For name-based answers, if 2+ significant words match, likely correct
    # E.g., "Adeline Virginia Woolf" vs "Virginia Woolf"
    # Intersection: {virginia, woolf} = 2 significant name words
    if len(intersection) >= 2 and len(expected_words) <= 5:
        # Check if intersection contains the key identifying words
        # (usually last names or unique first names)
        if len(intersection) / len(expected_words) >= 0.5:
            return True

    # Standard Jaccard similarity
    union = expected_words | actual_words
    jaccard = len(intersection) / len(union)

    return jaccard >= threshold


def score_answer(expected: str, actual: str) -> dict:
    """Answer-quality metrics. The extracted first line is scored, not the whole reply."""
    extracted = extract_answer_from_response(actual)
    return {
        "exact_match": normalize_answer(expected) == normalize_answer(extracted),
        "fuzzy_match": fuzzy_match(expected, actual),
        "answer_contains_expected": normalize_answer(expected) in normalize_answer(extracted),
        "answer_f1": compute_answer_f1(extracted, expected),
    }


def gold_groups(test_case: dict) -> list[list[str]]:
    """Gold note files, grouped: a group is found when any one of its files is retrieved.

    HotpotQA: ``required_notes`` are the supporting articles, one file each. MuSiQue: ``supporting_notes``,
    the supporting articles from the original MuSiQue release (an article can sit in two passages);
    LongBench's ``required_notes`` there are every passage of the question, distractors included.
    """
    if "supporting_notes" in test_case:
        return test_case["supporting_notes"]
    return [[name] for name in test_case["required_notes"]]


def load_note_files(dataset: str) -> dict[str, str]:
    """{note id: note file} from the ingestion progress file (``ORB_BENCH_PROGRESS``).

    Retrieval is scored by which notes came back, not by their titles: titles are written by the
    extraction model, so title matching measured how each model phrases titles.
    """
    path = os.environ.get("ORB_BENCH_PROGRESS")
    if not path or not Path(path).is_file():
        sys.exit("retrieval scoring needs ORB_BENCH_PROGRESS: the ingestion progress file that maps notes to their ids")
    progress = json.loads(Path(path).read_text()).get(dataset, {})
    return {state: name for name, state in progress.items()
            if state not in ("failed", "missing", "empty") and not state.startswith("pending:")}


def score_retrieval(note_ids: list[str], groups: list[list[str]], note_files: dict[str, str]) -> dict:
    """Recall over gold groups, precision over the retrieved notes, and how much of the gold made it into the index."""
    got = {note_files[n] for n in note_ids if n in note_files}
    gold = {name for group in groups for name in group}
    indexed = set(note_files.values())
    if not groups:
        return {"retrieval_recall": 0.0, "retrieval_precision": 0.0, "gold_ingested": 0.0}
    return {
        "retrieval_recall": sum(1 for g in groups if got & set(g)) / len(groups),
        "retrieval_precision": len(got & gold) / len(got) if got else 0.0,
        "gold_ingested": sum(1 for g in groups if indexed & set(g)) / len(groups),
    }


def result_row(r: "EvaluationResult") -> dict:
    return {
        "test_id": r.test_id,
        "question": r.question,
        "expected_answer": r.expected_answer,
        "actual_answer": r.actual_answer,
        "exact_match": r.exact_match,
        "fuzzy_match": r.fuzzy_match,
        "answer_contains_expected": r.answer_contains_expected,
        "answer_f1": r.answer_f1,
        "retrieved_note_titles": r.retrieved_note_titles,
        "retrieval_precision": r.retrieval_precision,
        "retrieval_recall": r.retrieval_recall,
        "gold_ingested": r.gold_ingested,
        "retrieved_note_ids": r.retrieved_note_ids,
        "total_time_ms": r.total_time_ms,
        "error": r.error,
        "context": r.context,
        "trace": r.trace,
    }


def fetch_config(base_url: str) -> dict:
    """Models and retrieval knobs the server is running with (empty if unreachable)."""
    try:
        return httpx.get(f"{base_url}/api/v1/benchmark/config", timeout=30).json()
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Warning: could not fetch server config: {e}")
        return {}


async def fetch_note_title_map(base_url: str) -> dict[str, str]:
    """Pre-fetch all notes and return {note_id: title} for retrieval metric matching."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.get(f"{base_url}/api/v1/notes")
            response.raise_for_status()
            notes = response.json()
            return {n["id"]: (n.get("title") or "") for n in notes if n.get("id")}
        except Exception as e:
            print(f"Warning: could not fetch note title map: {e}")
            return {}


async def query_orb(question: str, base_url: str = "http://localhost:8700") -> dict:
    """Send a question to Orb chat endpoint."""
    async with httpx.AsyncClient(timeout=1800.0) as client:
        try:
            response = await client.post(
                f"{base_url}/api/v1/chat",
                json={"query": question, "trace": True},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}


def extract_contexts_from_response(
    response: dict,
    note_title_map: dict[str, str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Extract context texts, titles, and note IDs from chat response.

    ``note_title_map`` is a pre-fetched {note_id: title} dict used to resolve
    titles for linked_notes entries that only carry an ``id`` field (the common
    case — note titles live in Postgres, not in the retrieval pipeline dicts).
    """
    # API uses 'context' key which contains structured doc objects
    context_items = response.get("context", [])
    contexts = []
    titles = []
    note_ids = []
    _title_map = note_title_map or {}

    for item in context_items:
        # Extract text content (this is what gets sent to LLM)
        text = item.get("text", "")
        if text:
            contexts.append(text)

        # Extract note IDs and titles from linked_notes.
        # Evidence dicts only carry {"id": uuid} — resolve title from map.
        linked_notes = item.get("linked_notes", [])
        for note in linked_notes:
            # Current API sends bare note ids; older builds sent {"id", "title"} dicts.
            note_id = note if isinstance(note, str) else note.get("id", "")
            title = ("" if isinstance(note, str) else note.get("title", "")) or _title_map.get(note_id, "")
            if note_id and note_id not in note_ids:  # Deduplicate
                note_ids.append(note_id)
            if title and title not in titles:  # Deduplicate
                titles.append(title)

        # Also check for direct note_id on the doc (for recent notes)
        direct_note_id = item.get("note_id")
        direct_title = item.get("title") or _title_map.get(direct_note_id or "", "")
        if direct_note_id and direct_note_id not in note_ids:
            note_ids.append(direct_note_id)
        if direct_title and direct_title not in titles:
            titles.append(direct_title)

    # ``sources`` names the notes the answer actually drew on ({"id", "title"}).
    for src in response.get("sources") or []:
        sid, stitle = src.get("id", ""), src.get("title", "") or _title_map.get(src.get("id", ""), "")
        if sid and sid not in note_ids:
            note_ids.append(sid)
        if stitle and stitle not in titles:
            titles.append(stitle)
    return contexts, titles, note_ids


async def evaluate_single(
    test_case: dict,
    base_url: str,
    verbose: bool = False,
    note_title_map: dict[str, str] | None = None,
    note_files: dict[str, str] | None = None,
) -> EvaluationResult:
    """Evaluate a single test case."""
    question = test_case["question"]
    expected_answer = test_case["answer"]
    expected_notes = test_case.get("required_notes", test_case.get("notes", []))

    # Query Orb
    start_time = time.perf_counter()
    response = await query_orb(question, base_url)
    total_time = (time.perf_counter() - start_time) * 1000

    result = EvaluationResult(
        test_id=test_case.get("id", "unknown"),
        question=question,
        expected_answer=expected_answer,
        actual_answer="",
        expected_notes=expected_notes,
        total_time_ms=total_time,
    )

    if "error" in response:
        result.error = response["error"]
        return result

    # Extract answer and contexts (API uses 'answer' key, not 'response')
    actual_answer = response.get("answer", "")
    result.actual_answer = actual_answer

    # Check for empty answer (likely a timeout or generation failure)
    if not actual_answer or not actual_answer.strip():
        result.error = "Empty answer returned from API"
        return result

    contexts, titles, note_ids = extract_contexts_from_response(
        response, note_title_map
    )
    result.trace = response.get("trace") or []
    result.context = [
        {"name": (d.get("original_obj") or {}).get("name", ""), "text": d.get("text", "")}
        for d in response.get("context", [])
    ]
    result.retrieved_contexts = contexts
    result.retrieved_note_titles = titles
    result.retrieved_note_ids = note_ids

    for key, value in score_answer(expected_answer, actual_answer).items():
        setattr(result, key, value)

    for key, value in score_retrieval(note_ids, gold_groups(test_case), note_files or {}).items():
        setattr(result, key, value)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Q: {question}...")
        print(f"Expected: {expected_answer}")
        print(f"Actual: {actual_answer}...")
        print(f"Exact Match: {result.exact_match} | Fuzzy: {result.fuzzy_match}")
        print(f"Retrieved {len(titles)} notes")

    return result


async def run_evaluation(
    manifest_path: Path,
    limit: Optional[int] = None,
    offset: int = 0,
    base_url: str = "http://localhost:8000",
    verbose: bool = False,
    concurrency: int = 1,
) -> list[EvaluationResult]:
    """Run evaluation on all test cases, ``concurrency`` questions at a time (results keep the manifest order).

    Questions are independent: each reads a fixed index, so running several at once changes how long a run
    takes, not its answers. Per-question times are then wall times under that load.
    """

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    test_cases = manifest["test_cases"][offset : offset + limit if limit else None]

    print(f"\n🧪 Evaluating {len(test_cases)} test cases from {manifest['dataset']}")
    print(f"   Endpoint: {base_url}")

    # Titles are for reading the results; retrieval is scored by note id (note_files).
    note_title_map = await fetch_note_title_map(base_url)
    print(f"   Note title map: {len(note_title_map)} notes loaded")
    note_files = load_note_files(manifest_path.name.removesuffix("_manifest.json"))

    gate = asyncio.Semaphore(max(1, concurrency))
    progress = tqdm(total=len(test_cases), desc="Evaluating")

    async def one(test_case: dict) -> EvaluationResult:
        async with gate:
            result = await evaluate_single(test_case, base_url, verbose, note_title_map, note_files)
        progress.update()
        return result

    results = await asyncio.gather(*(one(tc) for tc in test_cases))
    progress.close()
    return list(results)


def calculate_metrics(results: list[EvaluationResult]) -> dict:
    """Calculate aggregate metrics from evaluation results."""
    total = len(results)
    if total == 0:
        return {}

    # A question that errored is a wrong answer, not a missing one: averaging over the rest inflated a served run
    # with 16 of 20 errors to F1 0.94. Every average below is over all questions (an errored row scores 0).
    valid_results = results
    valid_count = total
    error_count = sum(1 for r in results if r.error is not None)

    # Answer quality
    exact_matches = sum(1 for r in valid_results if r.exact_match)
    fuzzy_matches = sum(1 for r in valid_results if r.fuzzy_match)
    contains_matches = sum(1 for r in valid_results if r.answer_contains_expected)

    # Token-level F1 (standard QA benchmark metric)
    avg_answer_f1 = sum(r.answer_f1 for r in valid_results) / valid_count

    # Retrieval quality
    avg_precision = sum(r.retrieval_precision for r in valid_results) / valid_count
    avg_recall = sum(r.retrieval_recall for r in valid_results) / valid_count
    avg_gold_ingested = sum(r.gold_ingested for r in valid_results) / valid_count

    # Timing
    avg_time = sum(r.total_time_ms for r in valid_results) / valid_count

    metrics = {
        "total_tests": total,
        "valid_tests": valid_count,
        "error_count": error_count,
        "answer_exact_match": exact_matches / valid_count,
        "answer_f1": avg_answer_f1,  # Standard QA benchmark metric
        "answer_fuzzy_match": fuzzy_matches / valid_count,
        "answer_contains_expected": contains_matches / valid_count,
        "retrieval_precision": avg_precision,
        "retrieval_recall": avg_recall,
        "gold_ingested": avg_gold_ingested,
        "retrieval_f1": (
            (2 * avg_precision * avg_recall / (avg_precision + avg_recall))
            if (avg_precision + avg_recall) > 0
            else 0
        ),
        "avg_response_time_ms": avg_time,
    }

    return metrics


def print_report(metrics: dict, results: list[EvaluationResult]):
    """Print evaluation report."""
    print("\n" + "=" * 70)
    print("📊 EVALUATION REPORT")
    print(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    print("\n📈 SUMMARY")
    print(f"   Total Tests: {metrics.get('total_tests', 0)}")
    print(f"   Valid Tests: {metrics.get('valid_tests', 0)}")
    print(f"   Errors: {metrics.get('error_count', 0)}")

    print("\n🎯 ANSWER QUALITY")
    print(f"   Exact Match (EM): {metrics.get('answer_exact_match', 0):.1%}")
    print(
        f"   F1 Score:         {metrics.get('answer_f1', 0):.1%}  ⭐ (Standard QA Metric)"
    )
    print(f"   Fuzzy Match:      {metrics.get('answer_fuzzy_match', 0):.1%}")
    print(f"   Contains Answer:  {metrics.get('answer_contains_expected', 0):.1%}")

    print("\n🔍 RETRIEVAL QUALITY")
    print(f"   Precision: {metrics.get('retrieval_precision', 0):.1%}")
    print(f"   Recall:    {metrics.get('retrieval_recall', 0):.1%}")
    print(f"   F1 Score:  {metrics.get('retrieval_f1', 0):.1%}")

    print("\n⏱️  PERFORMANCE")
    print(f"   Avg Response Time: {metrics.get('avg_response_time_ms', 0):.0f}ms")

    # Show sample failures
    failures = [r for r in results if not r.fuzzy_match and r.error is None]
    if failures:
        print(f"\n❌ SAMPLE FAILURES ({len(failures)} total):")
        for r in failures:
            print(f"\n   Q: {r.question}...")
            print(f"   Expected: {r.expected_answer}")
            print(f"   Got: {r.actual_answer}...")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Orb retrieval and answer quality"
    )
    parser.add_argument(
        "--dataset",
        choices=["hotpotqa", "musique"],
        required=True,
        help="Dataset to evaluate",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit number of test cases"
    )
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N test cases (held-out slices)")
    parser.add_argument("--concurrency", type=int, default=1, help="questions in flight at once (a server with parallel slots)")
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        help="Orb API base URL",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output for each test",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Custom output path (default: auto-generated in tests/benchmark/results/)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save results to file",
    )

    args = parser.parse_args()

    # Find manifest
    base_dir = Path(__file__).parent
    manifest_path = base_dir / f"{args.dataset}_manifest.json"

    if not manifest_path.exists():
        print(f"❌ Manifest not found: {manifest_path}")
        print(
            f"   Run prepare_dataset.py --dataset {args.dataset} first to ingest notes"
        )
        return

    # Run evaluation
    results = asyncio.run(
        run_evaluation(
            manifest_path,
            limit=args.limit,
            offset=args.offset,
            base_url=args.base_url,
            verbose=args.verbose,
            concurrency=args.concurrency,
        )
    )

    # Calculate and print metrics
    metrics = calculate_metrics(results)
    print_report(metrics, results)

    # Save results by default (unless --no-save)
    if not args.no_save:
        # Create results directory
        results_dir = base_dir / "results"
        results_dir.mkdir(exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        limit_suffix = f"_n{args.limit}" if args.limit else ""

        if args.output:
            output_path = Path(args.output)
        else:
            output_path = results_dir / f"{args.dataset}{limit_suffix}_{timestamp}.json"

        output_data = {
            "timestamp": datetime.now().isoformat(),
            "dataset": args.dataset,
            "num_tests": len(results),
            "metrics": metrics,
            "config": fetch_config(args.base_url),
            "note_titles": asyncio.run(fetch_note_title_map(args.base_url)),
            "note_files": load_note_files(args.dataset),
            "results": [result_row(r) for r in results],
        }
        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n💾 Results saved to {output_path}")


if __name__ == "__main__":
    main()
