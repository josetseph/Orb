#!/usr/bin/env python3
"""
prepare_dataset.py — Ingest benchmark notes into Orb via the API.

Reads notes from a benchmark manifest's notes_dir and POSTs each one to
the Orb create+ingest endpoints. Tracks progress so runs can be resumed.

Usage:
    # Ingest all HotpotQA notes (990 notes, ~8h on local hardware)
    python tests/benchmark/prepare_dataset.py --dataset hotpotqa

    # Ingest MuSiQue notes
    python tests/benchmark/prepare_dataset.py --dataset musique

    # Preview without sending
    python tests/benchmark/prepare_dataset.py --dataset hotpotqa --dry-run

    # Resume after interruption
    python tests/benchmark/prepare_dataset.py --dataset hotpotqa --resume

    # Limit to a subset for quick testing
    python tests/benchmark/prepare_dataset.py --dataset hotpotqa --limit 10
"""
import argparse
import asyncio
import json
import re
import os
import sys
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
# experiment.py points this into the data dir so a snapshot carries its own ingest state.
PROGRESS_FILE = Path(os.environ.get("ORB_BENCH_PROGRESS") or BASE_DIR / ".prepare_progress.json")
API_BASE = "http://localhost:8000"

# How often (seconds) to poll /status while waiting for ingestion to complete.
POLL_INTERVAL = 5.0


def _clean_content(content: str) -> str:
    """Strip Obsidian YAML frontmatter and navigation links."""
    content = re.sub(
        r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL | re.MULTILINE
    )
    for pattern in [
        r"^##?\s*Previous\s+Note\s*\n\[\[.*?\]\]\s*\n?",
        r"^##?\s*Next\s+Note\s*\n\[\[.*?\]\]\s*\n?",
        r"^Previous\s+Note:\s*\[\[.*?\]\]\s*\n?",
        r"^Next\s+Note:\s*\[\[.*?\]\]\s*\n?",
    ]:
        content = re.sub(pattern, "", content, flags=re.MULTILINE | re.IGNORECASE)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def _save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


async def _create_and_ingest(
    client: httpx.AsyncClient, content: str, title: str
) -> str | None:
    """Create a note and trigger ingestion. Returns the note_id or None on failure."""
    try:
        r = await client.post(
            f"{API_BASE}/api/v1/notes",
            json={"content": content},
            timeout=30.0,
        )
        r.raise_for_status()
        note_id: str = r.json()["id"]
    except Exception as exc:
        print(f"     ❌ Create failed for '{title}': {exc}")
        return None

    try:
        r = await client.post(
            f"{API_BASE}/api/v1/notes/{note_id}/ingest",
            timeout=30.0,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"      Ingest trigger failed for '{title}' ({note_id}): {exc}")
        # Note was created; still return the id so we don't retry create.

    return note_id


async def _poll_status(client: httpx.AsyncClient, note_id: str) -> tuple[bool, bool]:
    """
    Single GET /api/v1/notes/{note_id}/status poll.
    Returns (processed, failed).
    """
    r = await client.get(
        f"{API_BASE}/api/v1/notes/{note_id}/status",
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("processed", False), data.get("failed", False)


async def _wait_for_completion(
    client: httpx.AsyncClient,
    note_id: str,
    poll_interval: float = POLL_INTERVAL,
) -> bool:
    """
    Poll /status until ingestion completes or fails.
    Returns True on success, False on failure.  No timeout.
    """
    t_start = time.time()
    last_report = t_start
    while True:
        try:
            processed, failed = await _poll_status(client, note_id)
        except Exception as exc:
            print(f"    Status poll error for {note_id}…: {exc}", flush=True)
            await asyncio.sleep(poll_interval)
            continue

        if processed:
            elapsed = int(time.time() - t_start)
            print(f"   ✅ [{elapsed}s] {note_id}… done", flush=True)
            return True
        if failed:
            elapsed = int(time.time() - t_start)
            print(f"   ❌ [{elapsed}s] {note_id}… FAILED", flush=True)
            return False

        now = time.time()
        if now - last_report >= 30:
            elapsed = int(now - t_start)
            print(f"   ⏳ [{elapsed}s] {note_id}… still processing…", flush=True)
            last_report = now

        await asyncio.sleep(poll_interval)


async def _resolve_pending(
    client: httpx.AsyncClient,
    note_ids: list[str],
) -> dict[str, bool | None]:
    """
    Check the current status of multiple notes concurrently (used on --resume).
    Returns dict[note_id -> True (completed) | False (failed) | None (still processing)].
    """

    async def check_one(nid: str) -> tuple[str, bool | None]:
        try:
            processed, failed = await _poll_status(client, nid)
            if processed:
                return nid, True
            if failed:
                return nid, False
            return nid, None
        except Exception:
            return nid, None

    results = await asyncio.gather(*[check_one(nid) for nid in note_ids])
    return dict(results)


async def retry_failed(dataset: str) -> None:
    """Re-ingest all notes recorded as 'failed' in the progress file."""
    progress = _load_progress()
    dataset_progress: dict[str, str] = progress.get(dataset, {})

    to_retry = [fname for fname, val in dataset_progress.items() if val == "failed"]

    if not to_retry:
        print(f"✨ No failed notes found for '{dataset}'.")
        return

    print(f"\n🔁 Retrying {len(to_retry)} failed notes for '{dataset}'")

    manifest_path = BASE_DIR / f"{dataset}_manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    notes_dir = BASE_DIR / Path(manifest["notes_dir"]).name
    if not notes_dir.exists():
        notes_dir = Path(__file__).parent.parent.parent / manifest["notes_dir"]

    items: list[tuple[str, str, str]] = []
    for fname in to_retry:
        note_path = notes_dir / fname
        if not note_path.exists():
            print(f"    File not found, skipping: {fname}")
            dataset_progress[fname] = "missing"
            continue
        content = _clean_content(note_path.read_text(encoding="utf-8"))
        if not content:
            print(f"    Empty content, skipping: {fname}")
            dataset_progress[fname] = "empty"
            continue
        title = fname.replace(".md", "").replace("_", " ")
        items.append((fname, content, title))

    if not items:
        print("   Nothing to retry.")
        progress[dataset] = dataset_progress
        _save_progress(progress)
        return

    print(f"   Sending {len(items)} notes sequentially (1 at a time)…\n", flush=True)
    succeeded = 0
    failed_count = 0
    consecutive_failures = 0
    CIRCUIT_BREAKER_THRESHOLD = 3

    async with httpx.AsyncClient() as client:
        for i, (fname, content, title) in enumerate(items, 1):
            print(f"   [{i}/{len(items)}] {title}…", flush=True)
            note_id = await _create_and_ingest(client, content, title)
            if not note_id:
                dataset_progress[fname] = "failed"
                progress[dataset] = dataset_progress
                _save_progress(progress)
                failed_count += 1
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    print(
                        f"\n🔴 Circuit breaker: {consecutive_failures} consecutive failures."
                        f" LLM backend may be down. Stopping early — run with --resume to continue.",
                        flush=True,
                    )
                    break
                continue

            # Persist as pending immediately so --resume can recover if interrupted.
            dataset_progress[fname] = f"pending:{note_id}"
            progress[dataset] = dataset_progress
            _save_progress(progress)

            ok = await _wait_for_completion(client, note_id)
            dataset_progress[fname] = note_id if ok else "failed"
            progress[dataset] = dataset_progress
            _save_progress(progress)

            if ok:
                succeeded += 1
                consecutive_failures = 0
            else:
                failed_count += 1
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    print(
                        f"\n🔴 Circuit breaker: {consecutive_failures} consecutive failures."
                        f" LLM backend may be down. Stopping early — run with --resume to continue.",
                        flush=True,
                    )
                    break

    print(f"\n✨ Retry done: {succeeded} succeeded, {failed_count} failed.")
    still = [f for f, v in dataset_progress.items() if v == "failed"]
    if still:
        print(f"   {len(still)} still failed — run --retry-failed again.")
    print(f"   Progress saved to: {PROGRESS_FILE}\n")


async def prepare(
    dataset: str,
    limit: int | None,
    resume: bool,
    dry_run: bool,
    questions: int | None = None,
) -> None:
    manifest_path = BASE_DIR / f"{dataset}_manifest.json"
    if not manifest_path.exists():
        print(f"❌ Manifest not found: {manifest_path}")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    notes_dir = BASE_DIR / Path(manifest["notes_dir"]).name
    if not notes_dir.exists():
        notes_dir = Path(__file__).parent.parent.parent / manifest["notes_dir"]
    if not notes_dir.exists():
        print(f"❌ Notes directory not found: {notes_dir}")
        print(
            f"   Run: python tests/benchmark/fetch_notes.py --dataset {dataset}"
        )
        sys.exit(1)

    # Collect all unique note filenames referenced by the manifest
    all_note_files: list[str] = []
    seen: set[str] = set()
    for tc in manifest["test_cases"][:questions]:
        for fname in (
            tc.get("all_notes", [])
            + tc.get("notes", [])
            + tc.get("required_notes", [])
        ):
            if fname not in seen:
                seen.add(fname)
                all_note_files.append(fname)

    if limit:
        all_note_files = all_note_files[:limit]

    progress = _load_progress() if resume else {}
    dataset_progress: dict[str, str] = progress.get(dataset, {})

    def _is_confirmed(val: str) -> bool:
        """True if the value is a confirmed UUID (ingestion complete)."""
        return bool(val and len(val) == 36 and val.count("-") == 4)

    # On resume, resolve any "pending:xxx" notes left by an interrupted run.
    pending_entries = {
        fname: val[len("pending:") :]
        for fname, val in dataset_progress.items()
        if val.startswith("pending:")
    }
    if pending_entries and resume:
        print(
            f"   Checking {len(pending_entries)} in-progress notes from previous run…"
        )
        async with httpx.AsyncClient() as client:
            statuses = await _resolve_pending(client, list(pending_entries.values()))
        for fname, note_id in pending_entries.items():
            result = statuses.get(note_id)
            if result is True:
                dataset_progress[fname] = note_id
                print(f"   ✅ Resolved (completed): {fname}")
            elif result is False:
                dataset_progress[fname] = "failed"
                print(f"   ❌ Resolved (failed): {fname}")
            else:
                # Still processing or unknown — treat as failed so it gets re-queued.
                dataset_progress[fname] = "failed"
                print(f"    Unresolved (re-queued): {fname}")
        progress[dataset] = dataset_progress
        _save_progress(progress)

    to_ingest = [
        f for f in all_note_files if not _is_confirmed(dataset_progress.get(f, ""))
    ]
    # Also skip permanent non-retryable states
    to_ingest = [
        f
        for f in to_ingest
        if dataset_progress.get(f) not in ("missing", "empty", "dry-run")
    ]
    already_done = len(all_note_files) - len(to_ingest)

    print(f"\n📚 Dataset: {manifest['dataset']}")
    print(f"   Total notes  : {len(all_note_files)}")
    print(f"   Already done : {already_done}")
    print(f"   To ingest    : {len(to_ingest)}")
    if dry_run:
        print("   DRY RUN — no requests will be sent\n")
    print()

    if not to_ingest:
        print("✨ All notes already ingested.")
        return

    if dry_run:
        for fname in to_ingest:
            dataset_progress[fname] = "dry-run"
        progress[dataset] = dataset_progress
        _save_progress(progress)
        return

    # Build batch: collect file contents, skip missing/empty
    items: list[tuple[str, str, str]] = []
    for fname in to_ingest:
        note_path = notes_dir / fname
        if not note_path.exists():
            print(f"    File not found, skipping: {fname}")
            dataset_progress[fname] = "missing"
            continue
        content = _clean_content(note_path.read_text(encoding="utf-8"))
        if not content:
            print(f"    Empty after cleaning, skipping: {fname}")
            dataset_progress[fname] = "empty"
            continue
        title = fname.replace(".md", "").replace("_", " ")
        items.append((fname, content, title))

    if not items:
        print(" Nothing to send.")
        return

    # Capture log position BEFORE any requests so we don't miss completions.
    print(f"   Sending {len(items)} notes sequentially (1 at a time)…\n", flush=True)
    succeeded = 0
    failed_count = 0
    consecutive_failures = 0
    CIRCUIT_BREAKER_THRESHOLD = 3

    async with httpx.AsyncClient() as client:
        for i, (fname, content, title) in enumerate(items, 1):
            print(f"   [{i}/{len(items)}] {title}…", flush=True)
            note_id = await _create_and_ingest(client, content, title)
            if not note_id:
                dataset_progress[fname] = "failed"
                progress[dataset] = dataset_progress
                _save_progress(progress)
                failed_count += 1
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    print(
                        f"\n🔴 Circuit breaker: {consecutive_failures} consecutive failures."
                        f" LLM backend may be down. Stopping early — run with --resume to continue.",
                        flush=True,
                    )
                    break
                continue

            # Persist as pending immediately so --resume can recover if interrupted.
            dataset_progress[fname] = f"pending:{note_id}"
            progress[dataset] = dataset_progress
            _save_progress(progress)

            ok = await _wait_for_completion(client, note_id)
            dataset_progress[fname] = note_id if ok else "failed"
            progress[dataset] = dataset_progress
            _save_progress(progress)

            if ok:
                succeeded += 1
                consecutive_failures = 0
            else:
                failed_count += 1
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    print(
                        f"\n🔴 Circuit breaker: {consecutive_failures} consecutive failures."
                        f" LLM backend may be down. Stopping early — run with --resume to continue.",
                        flush=True,
                    )
                    break

    total = len(all_note_files)
    confirmed_total = sum(1 for v in dataset_progress.values() if _is_confirmed(v))
    print(f"\n✨ Done: {succeeded} newly succeeded, {failed_count} failed.")
    print(f"   Total confirmed in dataset: {confirmed_total}/{total}")
    print(f"   Progress saved to: {PROGRESS_FILE}\n")


def main() -> None:
    global API_BASE  # must precede any use of API_BASE in this scope
    parser = argparse.ArgumentParser(description="Ingest benchmark notes into Orb")
    parser.add_argument(
        "--dataset",
        choices=["hotpotqa", "musique"],
        required=True,
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Ingest at most N notes"
    )
    parser.add_argument(
        "--questions", type=int, default=None,
        help="Ingest only the notes the first N questions reference (pairs with evaluate.py --limit N)",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip already-ingested notes"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry all failed/timeout notes sequentially",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without sending"
    )
    parser.add_argument("--delay", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--base-url", type=str, default=API_BASE)

    args = parser.parse_args()

    API_BASE = args.base_url.rstrip("/")

    if args.retry_failed:
        asyncio.run(retry_failed(dataset=args.dataset))
        return

    asyncio.run(
        prepare(
            dataset=args.dataset,
            limit=args.limit,
            resume=args.resume,
            dry_run=args.dry_run,
            questions=args.questions,
        )
    )


if __name__ == "__main__":
    main()
