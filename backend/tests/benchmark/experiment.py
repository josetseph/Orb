#!/usr/bin/env python3
"""One benchmark run, start to finish: restore a snapshot, boot the pipeline with
this run's settings, optionally ingest, evaluate, optionally snapshot, shut down.

    # ingest once with a given ingestion model, keep the index
    python tests/benchmark/experiment.py ingest-gemma --dataset hotpotqa --questions 20 \\
        --ingest --snapshot hotpot20-gemma --ingestion-model gemma4-e4b-q4

    # then try chat models / retrieval knobs against that same index
    python tests/benchmark/experiment.py qwen-chat --dataset hotpotqa --questions 20 \\
        --restore hotpot20-gemma --chat-model qwen3-4b-q4
    python tests/benchmark/experiment.py loops5 --dataset hotpotqa --questions 20 \\
        --restore hotpot20-gemma --set MAX_LOOP_ITERATIONS=5

    # answering model only, over evidence another run already retrieved
    python tests/benchmark/experiment.py qwen-synth --synthesis-from ../Results/loops5/hotpotqa.json \\
        --restore hotpot20-gemma --chat-model qwen3-4b-q4

Results land in <repo>/Results/<name>/; snapshots in <repo>/snapshots/<name>/.
The live data dir is always <repo>/data: the KB registry stores absolute vault
paths, so a snapshot is only valid restored to where it was taken.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BENCH = Path(__file__).resolve().parent
BACKEND = BENCH.parent.parent
REPO = BACKEND.parent
DATA, SNAPSHOTS, RESULTS = REPO / "data", REPO / "snapshots", REPO / "Results"
LANE = 0  # several experiments at once on one GPU: each lane has its own data dir, ports and model selection
KEEP = {"bin"}  # sidecar binaries: large, identical across runs
BASE_URL = "http://127.0.0.1:8000"


def save_snapshot(name: str, selection: dict) -> None:
    dest = SNAPSHOTS / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(DATA, dest, ignore=shutil.ignore_patterns(*KEEP, "logs"))
    # The vectors inside are only valid for this embed model; the paths inside point at this data dir.
    (dest / "snapshot.json").write_text(json.dumps({"selection": selection, "data_dir": str(DATA)}, indent=2))
    print(f"[experiment] snapshot saved: {dest}")


def relocate(old: str) -> None:
    """A snapshot restored into another lane's data dir: point the KB registry's paths at where it now is."""
    import sqlite3

    if not old or old == str(DATA):
        return
    (DATA / "paths.json").unlink(missing_ok=True)
    conn = sqlite3.connect(DATA / "orb.db")
    with conn:
        for column in ("vault_path", "kuzu_path"):
            conn.execute(f"UPDATE knowledge_bases SET {column} = ? || substr({column}, ?) WHERE substr({column}, 1, ?) = ?",
                         (str(DATA), len(old) + 1, len(old), old))
    conn.close()


def restore_snapshot(name: str) -> None:
    src = SNAPSHOTS / name
    if not src.is_dir():
        sys.exit(f"[experiment] no snapshot named {name!r} in {SNAPSHOTS}")
    DATA.mkdir(exist_ok=True)
    for child in DATA.iterdir():
        if child.name not in KEEP:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(src, DATA, dirs_exist_ok=True)
    meta = src / "snapshot.json"
    relocate(json.loads(meta.read_text()).get("data_dir", "") if meta.is_file() else str(REPO / "data"))
    print(f"[experiment] restored snapshot {name} -> {DATA}")


def get_json(path: str, timeout: float = 10) -> dict:
    with urlopen(BASE_URL + path, timeout=timeout) as resp:  # noqa: S310 — loopback
        return json.load(resp)


def wait_until(what: str, check, timeout: float, server: subprocess.Popen) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if server.poll() is not None:
            sys.exit(f"[experiment] server exited ({server.returncode}) while waiting for {what}; see server.log")
        try:
            if check():
                return
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        time.sleep(3)
    sys.exit(f"[experiment] timed out waiting for {what}")


def run(cmd: list[str], env: dict) -> None:
    print("[experiment] $", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BACKEND, env=env, check=True)


def stop_on_signal() -> None:
    """A process started in the background inherits SIGINT=ignore, so ``pkill -INT`` would do nothing.
    Both signals become KeyboardInterrupt, which unwinds through ``finally`` and shuts the server down."""
    def interrupt(signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)


def main() -> None:
    stop_on_signal()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="run name; results go to Results/<name>/")
    ap.add_argument("--dataset", choices=["hotpotqa", "musique"])
    ap.add_argument("--questions", type=int, help="N questions (and, with --ingest, only their notes)")
    ap.add_argument("--offset", type=int, default=0, help="...starting at this question (held-out slices)")
    ap.add_argument("--evaluator", choices=["full", "retrieval"], default="full",
                    help="full = the whole loop with an answering model; retrieval = search-and-expand only, against the gold notes")
    ap.add_argument("--queries-from", nargs="*", default=[], metavar="RESULTS.json",
                    help="retrieval evaluator: also run the follow-up queries these past runs recorded")
    ap.add_argument("--no-cache", action="store_true", help="do not replay unchanged model calls from <repo>/llm-cache")
    ap.add_argument("--lane", type=int, default=0, help="run beside other lanes on the same machine (own data dir, ports, model selection)")
    ap.add_argument("--prefetch", nargs="+", metavar="MODEL", help="only download these catalogue models (and the pinned embed/reranker), then stop")
    ap.add_argument("--give-up-after", type=int, default=20, help="stop an ingest whose first N notes all fail (a model that cannot do the task)")
    ap.add_argument("--restore", metavar="SNAPSHOT", help="start from this snapshot instead of the current data dir")
    ap.add_argument("--fresh", action="store_true", help="start from an empty data dir")
    ap.add_argument("--ingest", action="store_true", help="ingest the dataset's notes before evaluating")
    ap.add_argument("--no-eval", action="store_true", help="ingest/snapshot only")
    ap.add_argument("--download", action="store_true",
                    help="fetch any missing GGUFs for this run's chat / embed / reranker selection first")
    ap.add_argument("--communities", action="store_true",
                    help="after ingesting, rebuild community summaries (on-demand in the app; one model call per cluster)")
    ap.add_argument("--snapshot", metavar="NAME", help="save the data dir under this name when done")
    ap.add_argument("--synthesis-from", metavar="RESULTS.json", help="replay answering only, over that run's evidence")
    ap.add_argument("--provider", help="LLM provider for this run: local | gemini | openai | anthropic | openai_compat")
    ap.add_argument("--chat-model", help="answering model: a catalogue id, a local model ref, or a cloud model name")
    ap.add_argument("--ingestion-model", help="extraction model (defaults to the chat model)")
    ap.add_argument("--base-url", dest="llm_base_url", help="endpoint for --provider openai_compat")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="env override for the server (repeatable)")
    args = ap.parse_args()
    if not args.synthesis_from and not args.dataset and not args.prefetch:
        ap.error("--dataset is required unless --synthesis-from or --prefetch is given")
    global DATA, BASE_URL, LANE  # noqa: PLW0603 — every helper below works on this lane
    LANE = args.lane
    if LANE:
        DATA = REPO / f"data-lane{LANE}"
        BASE_URL = f"http://127.0.0.1:{8000 + 10 * LANE}"

    overrides = dict(kv.split("=", 1) for kv in args.set)
    out = RESULTS / args.name
    out.mkdir(parents=True, exist_ok=True)
    if args.restore:
        meta = SNAPSHOTS / args.restore / "snapshot.json"
        built_with = json.loads(meta.read_text())["selection"].get("embed_id") if meta.is_file() else None
        wanted = overrides.get("EMBED_MODEL_ID")
        if wanted and built_with and wanted != built_with:
            sys.exit(f"[experiment] snapshot {args.restore} was embedded with {built_with}; {wanted} needs its own --fresh --ingest run")
        restore_snapshot(args.restore)
    elif args.fresh and DATA.exists():
        marker = DATA / ".experiment"
        if marker.is_file() and marker.read_text().strip() == args.name:
            # Same run, started before and interrupted: keep the hours already ingested (--resume skips them).
            print(f"[experiment] resuming {args.name}: data dir kept")
        else:
            for child in DATA.iterdir():
                if child.name not in KEEP:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
    DATA.mkdir(exist_ok=True)
    (DATA / ".experiment").write_text(args.name)

    cache = {} if args.no_cache else {"LLM_CALL_CACHE_DIR": str(REPO / "llm-cache")}
    failures = DATA / "logs" / "invalid_model_output.jsonl"
    failures.unlink(missing_ok=True)  # counted per run
    lane_env = {}
    if LANE:
        # Weights are shared (one copy on disk); the manifest, which records the model selection, is per lane.
        shared, own = REPO / "models" / "gguf", REPO / f"models-lane{LANE}"
        shared.mkdir(parents=True, exist_ok=True)
        own.mkdir(exist_ok=True)
        if not (own / "gguf").exists():
            (own / "gguf").symlink_to(shared, target_is_directory=True)
        lane_env = {"ORB_MODELS_DIR": str(own), "ORB_API_PORT": str(8000 + 10 * LANE), "QDRANT_PORT": str(6333 + 10 * LANE),
                    "QDRANT_GRPC_PORT": str(6334 + 10 * LANE), "MEILI_PORT": str(7700 + 10 * LANE)}
    env = {**os.environ, "BENCHMARK_MODE": "true", **cache, **lane_env, **overrides,
           "ORB_DATA_DIR": str(DATA), "ORB_BENCH_PROGRESS": str(DATA / "prepare_progress.json"),
           "PYTHONHASHSEED": "0", "BENCHMARK_TODAY": "2026-10-01"}  # a fixed "today": query analysis puts it in its prompt  # set and dict-of-set iteration order, so tied candidates keep one order across runs
    record = {"name": args.name, "dataset": args.dataset, "questions": args.questions, "restore": args.restore,
              "ingest": args.ingest, "communities": args.communities, "overrides": overrides, "provider": args.provider,
              "chat_model": args.chat_model, "ingestion_model": args.ingestion_model, "started": datetime.now().isoformat(timespec="seconds"),
              "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()}

    with open(out / "server.log", "ab") as log:
        server = subprocess.Popen([sys.executable, "run.py"], cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_until("the API", lambda: get_json("/health")["status"] == "healthy", 1800, server)
        # The manifest is branch-local state that outlives a run: re-assert the selection so
        # EMBED_MODEL_ID / RERANK_MODEL_ID (or their absence) decide it, not the previous experiment.
        # Only the download route records where a model file lives, and it is a no-op for files
        # already present, so it is the one to call; --download is the permission to fetch new ones.
        selection = get_json("/api/v1/benchmark/config")["selection"]
        catalogue = {m["id"]: m for m in get_json("/api/v1/models", 60)["local"]["downloadable"]}
        # Ingestion model first, chat model last: the last one set up is what the manifest selects.
        wanted = [m for m in (args.ingestion_model, args.chat_model) if m in catalogue] or [selection.get("chat_id")]
        if args.prefetch:
            unknown = [m for m in args.prefetch if m not in catalogue]
            if unknown:
                sys.exit(f"[experiment] not in the model catalogue: {', '.join(unknown)}")
            wanted, args.download, args.no_eval = list(args.prefetch), True, True
        if not args.download:
            missing = [m for m in wanted if m in catalogue and not catalogue[m]["downloaded"]]
            missing += [f"{k}={v}" for k, v in overrides.items() if k in ("EMBED_MODEL_ID", "RERANK_MODEL_ID")
                        and v not in (selection.get("embed_id"), selection.get("reranker_id"))]
            if missing:
                sys.exit(f"[experiment] not downloaded yet: {', '.join(missing)}. Re-run with --download.")
        for model_id in dict.fromkeys(wanted):
            req = Request(BASE_URL + "/api/v1/setup/download-models", method="POST",
                          data=json.dumps({"chat_id": model_id}).encode(), headers={"content-type": "application/json"})
            try:
                urlopen(req, timeout=14400).read()  # noqa: S310 — loopback; a first download can take a while
            except HTTPError as exc:
                sys.exit(f"[experiment] model setup failed for {model_id}: {exc.read().decode()}")
        if args.provider or args.chat_model or args.ingestion_model:
            # Pinned on the KB, which lives in the data dir: no shared manifest is touched.
            pin = {"provider": args.provider, "model": args.chat_model,
                   "ingestion_model": args.ingestion_model, "base_url": args.llm_base_url}
            req = Request(BASE_URL + "/api/v1/kb/default/llm", method="PATCH", data=json.dumps(pin).encode(),
                          headers={"content-type": "application/json"})
            try:
                urlopen(req, timeout=60).read()  # noqa: S310 — loopback
            except HTTPError as exc:
                sys.exit(f"[experiment] model pin rejected: {exc.read().decode()}")
        record["server"] = get_json("/api/v1/benchmark/config")
        if not get_json("/api/v1/models", 60)["global"]["configured"]:
            sys.exit("[experiment] the server reports no usable model (AI not configured); see server.log")
        print(f"[experiment] server up: chat={record['server'].get('chat_model')} ingestion={record['server'].get('ingestion_model')}")

        if args.ingest:
            cmd = [sys.executable, "tests/benchmark/prepare_dataset.py", "--dataset", args.dataset, "--resume", "--base-url", BASE_URL]
            if args.questions:
                cmd += ["--questions", str(args.questions), "--question-offset", str(args.offset)]
            cmd += ["--give-up-after", str(args.give_up_after)]
            t0 = time.monotonic()
            run(cmd, env)
            wait_until("ingestion to drain", lambda: get_json("/api/v1/benchmark/idle")["idle"], 7200, server)
            record["ingest_seconds"] = round(time.monotonic() - t0)
            states = list(json.loads((DATA / "prepare_progress.json").read_text()).get(args.dataset, {}).values())
            ingested = sum(1 for v in states if not str(v).startswith(("pending", "failed", "missing", "empty")))
            record["notes"] = {"ingested": ingested, "rejected": sum(1 for v in states if v == "failed"), "total": len(states)}
            print(f"[experiment] notes ingested {ingested} of {len(states)}", flush=True)
            if ingested == 0:
                raise SystemExit("[experiment] no note was ingested; an empty index is not evaluated")
        if args.communities:  # works on a restored snapshot too: same index, with and without summaries
            t0 = time.monotonic()
            urlopen(Request(BASE_URL + "/api/v1/admin/rebuild-communities", method="POST"), timeout=60).read()  # noqa: S310
            time.sleep(10)  # the rebuild is a background task; give its running flag time to rise
            wait_until("the community rebuild", lambda: get_json("/api/v1/benchmark/idle")["idle"], 14400, server)
            record["communities_seconds"] = round(time.monotonic() - t0)

        if args.synthesis_from:
            run([sys.executable, "tests/benchmark/synthesis.py", args.synthesis_from, "--base-url", BASE_URL,
                 "--output", str(out / "synthesis.json")], env)
        elif not args.no_eval:
            if args.evaluator == "retrieval":
                cmd = [sys.executable, "tests/benchmark/retrieval_eval.py", "--dataset", args.dataset, "--base-url", BASE_URL,
                       "--output", str(out / "retrieval.json"), "--queries-from", *args.queries_from]
            else:
                cmd = [sys.executable, "tests/benchmark/evaluate.py", "--dataset", args.dataset, "--base-url", BASE_URL,
                       "--output", str(out / f"{args.dataset}.json")]
            cmd += ["--offset", str(args.offset)]
            if args.questions:
                cmd += ["--limit", str(args.questions)]
            run(cmd, env)
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=90)
        except subprocess.TimeoutExpired:
            server.kill()
            # a killed server cannot stop its sidecars; they run from this data dir's bin folder
            subprocess.run(["pkill", "-TERM", "-f", str(DATA / "bin")], check=False)
        record["finished"] = datetime.now().isoformat(timespec="seconds")
        tally: dict[str, int] = {}
        if failures.is_file():
            for line in failures.read_text(encoding="utf-8").splitlines():
                stage = json.loads(line)["stage"]
                tally[stage] = tally.get(stage, 0) + 1
            shutil.copyfile(failures, out / "invalid_model_output.jsonl")
        record["invalid_replies"] = tally  # a result, not an error: how often the model gave nothing usable
        (out / "config.json").write_text(json.dumps(record, indent=2))
    if args.snapshot:
        save_snapshot(args.snapshot, record.get("server", {}).get("selection") or {})
    print(f"[experiment] done -> {out}")


if __name__ == "__main__":
    main()
