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
KEEP = {"bin"}  # sidecar binaries: large, identical across runs
BASE_URL = "http://127.0.0.1:8000"


def save_snapshot(name: str) -> None:
    dest = SNAPSHOTS / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(DATA, dest, ignore=shutil.ignore_patterns(*KEEP, "logs"))
    print(f"[experiment] snapshot saved: {dest}")


def restore_snapshot(name: str) -> None:
    src = SNAPSHOTS / name
    if not src.is_dir():
        sys.exit(f"[experiment] no snapshot named {name!r} in {SNAPSHOTS}")
    DATA.mkdir(exist_ok=True)
    for child in DATA.iterdir():
        if child.name not in KEEP:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(src, DATA, dirs_exist_ok=True)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="run name; results go to Results/<name>/")
    ap.add_argument("--dataset", choices=["hotpotqa", "musique"])
    ap.add_argument("--questions", type=int, help="first N questions (and, with --ingest, only their notes)")
    ap.add_argument("--restore", metavar="SNAPSHOT", help="start from this snapshot instead of the current data dir")
    ap.add_argument("--fresh", action="store_true", help="start from an empty data dir")
    ap.add_argument("--ingest", action="store_true", help="ingest the dataset's notes before evaluating")
    ap.add_argument("--no-eval", action="store_true", help="ingest/snapshot only")
    ap.add_argument("--snapshot", metavar="NAME", help="save the data dir under this name when done")
    ap.add_argument("--synthesis-from", metavar="RESULTS.json", help="replay answering only, over that run's evidence")
    ap.add_argument("--provider", help="LLM provider for this run: local | gemini | openai | anthropic | openai_compat")
    ap.add_argument("--chat-model", help="answering model: a catalogue id, a local model ref, or a cloud model name")
    ap.add_argument("--ingestion-model", help="extraction model (defaults to the chat model)")
    ap.add_argument("--base-url", dest="llm_base_url", help="endpoint for --provider openai_compat")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="env override for the server (repeatable)")
    args = ap.parse_args()
    if not args.synthesis_from and not args.dataset:
        ap.error("--dataset is required unless --synthesis-from is given")

    overrides = dict(kv.split("=", 1) for kv in args.set)
    out = RESULTS / args.name
    out.mkdir(parents=True, exist_ok=True)
    if args.restore:
        restore_snapshot(args.restore)
    elif args.fresh and DATA.exists():
        for child in DATA.iterdir():
            if child.name not in KEEP:
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    env = {**os.environ, "BENCHMARK_MODE": "true", **overrides,
           "ORB_DATA_DIR": str(DATA), "ORB_BENCH_PROGRESS": str(DATA / "prepare_progress.json")}
    record = {"name": args.name, "dataset": args.dataset, "questions": args.questions, "restore": args.restore,
              "ingest": args.ingest, "overrides": overrides, "provider": args.provider,
              "chat_model": args.chat_model, "ingestion_model": args.ingestion_model, "started": datetime.now().isoformat(timespec="seconds"),
              "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()}

    with open(out / "server.log", "ab") as log:
        server = subprocess.Popen([sys.executable, "run.py"], cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_until("the API", lambda: get_json("/health")["status"] == "healthy", 1800, server)
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
        print(f"[experiment] server up: chat={record['server'].get('chat_model')} ingestion={record['server'].get('ingestion_model')}")

        if args.ingest:
            cmd = [sys.executable, "tests/benchmark/prepare_dataset.py", "--dataset", args.dataset, "--resume"]
            if args.questions:
                cmd += ["--questions", str(args.questions)]
            t0 = time.monotonic()
            run(cmd, env)
            wait_until("background graph jobs", lambda: get_json("/api/v1/benchmark/idle")["idle"], 7200, server)
            record["ingest_seconds"] = round(time.monotonic() - t0)

        if args.synthesis_from:
            run([sys.executable, "tests/benchmark/synthesis.py", args.synthesis_from,
                 "--output", str(out / "synthesis.json")], env)
        elif not args.no_eval:
            cmd = [sys.executable, "tests/benchmark/evaluate.py", "--dataset", args.dataset,
                   "--output", str(out / f"{args.dataset}.json")]
            if args.questions:
                cmd += ["--limit", str(args.questions)]
            run(cmd, env)
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            server.kill()
        record["finished"] = datetime.now().isoformat(timespec="seconds")
        (out / "config.json").write_text(json.dumps(record, indent=2))
    if args.snapshot:
        save_snapshot(args.snapshot)
    print(f"[experiment] done -> {out}")


if __name__ == "__main__":
    main()
