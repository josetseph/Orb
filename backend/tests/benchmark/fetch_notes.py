#!/usr/bin/env python3
"""
fetch_notes.py — Download public HotpotQA / LongBench-MuSiQue sources and
materialize the markdown note files referenced by the local manifests.

Notes are intentionally not committed (large, regenerable). Run this once
before prepare_dataset.py / evaluate.py.

Usage:
    python tests/benchmark/fetch_notes.py
    python tests/benchmark/fetch_notes.py --dataset hotpotqa
    python tests/benchmark/fetch_notes.py --dataset musique --force
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / ".cache"

HOTPOT_URL = (
    "https://web.archive.org/web/20230315000000id_/"
    "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json"
)
HOTPOT_CACHE = CACHE_DIR / "hotpot_dev_distractor_v1.json"

LONGBENCH_ZIP_URL = (
    "https://huggingface.co/datasets/THUDM/LongBench/resolve/main/data.zip"
)
LONGBENCH_ZIP = CACHE_DIR / "longbench_data.zip"
MUSIQUE_CACHE = CACHE_DIR / "musique.jsonl"

USER_AGENT = "OrbBenchmarkFetcher/1.0"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    print(f"   Downloading {url}")
    print(f"   → {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)
    print(f"   Saved {dest.stat().st_size:,} bytes")


def _ensure_hotpot() -> Path:
    if not HOTPOT_CACHE.exists() or HOTPOT_CACHE.stat().st_size < 1_000_000:
        _download(HOTPOT_URL, HOTPOT_CACHE)
    return HOTPOT_CACHE


def _ensure_musique() -> Path:
    if MUSIQUE_CACHE.exists() and MUSIQUE_CACHE.stat().st_size > 1_000:
        return MUSIQUE_CACHE
    if not LONGBENCH_ZIP.exists() or LONGBENCH_ZIP.stat().st_size < 1_000_000:
        _download(LONGBENCH_ZIP_URL, LONGBENCH_ZIP)
    print("   Extracting data/musique.jsonl from LongBench zip…")
    with zipfile.ZipFile(LONGBENCH_ZIP) as zf:
        hits = [n for n in zf.namelist() if n.endswith("musique.jsonl")]
        if not hits:
            raise RuntimeError("musique.jsonl not found inside LongBench data.zip")
        with zf.open(hits[0]) as src, open(MUSIQUE_CACHE, "wb") as dst:
            dst.write(src.read())
    print(f"   Saved {MUSIQUE_CACHE.stat().st_size:,} bytes")
    # Zip is ~114MB and only needed once for extraction.
    try:
        LONGBENCH_ZIP.unlink()
    except OSError:
        pass
    return MUSIQUE_CACHE


def _sanitize_hotpot_filename(title: str) -> str:
    t = html.unescape(title)
    for old, new in [
        ("&", "_amp_"),
        ('"', "_quot_"),
        ("'", "_"),
        ("(", "_"),
        (")", "_"),
        (",", "_"),
        (":", "_"),
        (".", "_"),
        ("/", "_"),
        ("?", "_"),
        ("!", "_"),
        ("–", "_"),
        ("—", "_"),
        (";", "_"),
        ("+", "_"),
        ("×", "_"),
        ("*", "_"),
        ("#", "_"),
        ("%", "_"),
        ("<", "_"),
        (">", "_"),
        ("|", "_"),
        ("\\", "_"),
    ]:
        t = t.replace(old, new)
    return t + ".md"


def _escape_hotpot_h1(title: str) -> str:
    return html.unescape(title).replace("&", "&amp;").replace('"', "&quot;")


def _sanitize_musique_mid(title: str) -> str:
    t = title
    for old, new in [
        ("&", "_"),
        ('"', "_"),
        ("'", "_"),
        ("(", "_"),
        (")", "_"),
        (",", "_"),
        (":", "_"),
        (".", "_"),
        ("/", "_"),
        ("?", "_"),
        ("!", "_"),
        ("–", "_"),
        ("—", "_"),
        (";", "_"),
        ("+", "_"),
        ("×", "_"),
        ("*", "_"),
        ("#", "_"),
        ("%", "_"),
    ]:
        t = t.replace(old, new)
    return t


def _musique_trunc_for_mid(title: str, mid: str) -> str:
    for n in range(len(title), 0, -1):
        if _sanitize_musique_mid(title[:n]) == mid:
            return title[:n]
    return title


def _get_passage(context: str, one_based: int) -> str:
    start = re.search(rf"(?:^|\n)(Passage {one_based}:\n)", context)
    if not start:
        raise KeyError(f"Passage {one_based} not found in context")
    s = start.start(1)
    nxt = re.search(rf"\nPassage {one_based + 1}:\n", context[s:])
    return context[s : s + nxt.start()] if nxt else context[s:]


def fetch_hotpotqa(*, force: bool) -> int:
    manifest_path = BASE_DIR / "hotpotqa_manifest.json"
    notes_dir = BASE_DIR / "hotpotqa_notes"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {fname for tc in manifest["test_cases"] for fname in tc["all_notes"]}

    if notes_dir.exists() and not force:
        present = {p.name for p in notes_dir.glob("*.md")}
        if expected <= present:
            print(f"✅ HotpotQA notes already present ({len(expected)} files)")
            return 0

    print("📚 Materializing HotpotQA notes…")
    source = json.loads(_ensure_hotpot().read_text(encoding="utf-8"))
    by_id = {ex["_id"]: ex for ex in source}

    notes_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for tc in manifest["test_cases"]:
        ex = by_id.get(tc["id"])
        if ex is None:
            raise KeyError(f"HotpotQA example not found for id={tc['id']}")
        for title, sents in ex["context"]:
            fname = _sanitize_hotpot_filename(title)
            if fname in written:
                continue
            body = " ".join(sents)
            written[fname] = f"# {_escape_hotpot_h1(title)}\n\n{body}".rstrip() + "\n"

    missing = expected - set(written)
    if missing:
        raise RuntimeError(f"Failed to materialize {len(missing)} HotpotQA notes")

    for fname in sorted(expected):
        (notes_dir / fname).write_text(written[fname], encoding="utf-8")
    print(f"✅ Wrote {len(expected)} HotpotQA notes → {notes_dir}")
    return len(expected)


_MUSIQUE_NAME = re.compile(r"^q(\d+)_p(\d+)_(.*) _Passage (\d+)_\.md$")


def fetch_musique(*, force: bool) -> int:
    manifest_path = BASE_DIR / "musique_manifest.json"
    notes_dir = BASE_DIR / "musique_notes"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {fname for tc in manifest["test_cases"] for fname in tc["notes"]}

    if notes_dir.exists() and not force:
        present = {p.name for p in notes_dir.glob("*.md")}
        if expected <= present:
            print(f"✅ MuSiQue notes already present ({len(expected)} files)")
            return 0

    print("📚 Materializing MuSiQue notes…")
    rows = [
        json.loads(line)
        for line in _ensure_musique().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    notes_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for tc in manifest["test_cases"]:
        q_idx = int(tc["id"].split("_")[1])
        row = rows[q_idx]
        for fname in tc["notes"]:
            m = _MUSIQUE_NAME.match(fname)
            if not m:
                raise ValueError(f"Unexpected MuSiQue note name: {fname}")
            p_idx = int(m.group(2))
            mid = m.group(3)
            passage = _get_passage(row["context"], p_idx + 1).rstrip()
            title = passage.splitlines()[1]
            trunc = _musique_trunc_for_mid(title, mid)
            content = f"# {trunc} (Passage {p_idx})\n\n{passage}\n"
            (notes_dir / fname).write_text(content, encoding="utf-8")
            count += 1

    print(f"✅ Wrote {count} MuSiQue notes → {notes_dir}")
    annotate_musique_support(manifest_path, notes_dir)
    return count


def annotate_musique_support(manifest_path: Path, notes_dir: Path) -> None:
    """Add ``supporting_notes`` to each MuSiQue test case: the gold for retrieval scoring.

    LongBench keeps no supporting flags, and its ``required_notes`` are every passage of the question.
    The original MuSiQue answerable dev set marks the supporting paragraphs; each LongBench question is
    in it verbatim. LongBench rebuilt the passages from Wikipedia, so they are matched by article title
    (the passage's title line), exactly. An article can sit in two passages: such a title is one group,
    found when either passage is retrieved. A supporting article LongBench left out is listed in
    ``supporting_missing`` and not counted.
    """
    from huggingface_hub import hf_hub_download

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if all("supporting_notes" in tc for tc in manifest["test_cases"]):
        return
    source = hf_hub_download("bdsaglam/musique", "musique_ans_v1.0_dev.jsonl", repo_type="dataset")
    by_question = {row["question"]: row for row in map(json.loads, Path(source).read_text(encoding="utf-8").splitlines())}
    for tc in manifest["test_cases"]:
        titles = {fname: (notes_dir / fname).read_text(encoding="utf-8").splitlines()[3] for fname in tc["notes"]}
        supporting = dict.fromkeys(p["title"] for p in by_question[tc["question"]]["paragraphs"] if p["is_supporting"])
        groups = {title: [f for f, t in titles.items() if t == title] for title in supporting}
        tc["supporting_notes"] = [files for files in groups.values() if files]
        tc["supporting_missing"] = [title for title, files in groups.items() if not files]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    found = sum(len(tc["supporting_notes"]) for tc in manifest["test_cases"])
    missing = sum(len(tc["supporting_missing"]) for tc in manifest["test_cases"])
    print(f"✅ Supporting notes: {found} articles found, {missing} not in LongBench's passages → {manifest_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and materialize benchmark note markdown files"
    )
    parser.add_argument(
        "--dataset",
        choices=["hotpotqa", "musique", "all"],
        default="all",
        help="Which dataset notes to fetch (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite notes even if they already exist",
    )
    args = parser.parse_args()

    try:
        if args.dataset in ("hotpotqa", "all"):
            fetch_hotpotqa(force=args.force)
        if args.dataset in ("musique", "all"):
            fetch_musique(force=args.force)
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
