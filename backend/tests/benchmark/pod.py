#!/usr/bin/env python3
"""Rent a RunPod GPU for the benchmarks, run a queue on it unattended, bring the results home.

    python tests/benchmark/pod.py up                          # rent, upload code, install (about 20 min), leave it ready
    python tests/benchmark/pod.py setup                       # redo upload + install on the recorded pod
    python tests/benchmark/pod.py run live-check sweeps/round1-retrieval.json sweeps/round1-loop.json
    python tests/benchmark/pod.py follow                      # copy progress home every 15 min; stop before credit runs out
    python tests/benchmark/pod.py --account main up --restore # continue on another account from the saved progress
    python tests/benchmark/pod.py status                      # state, $/hr and spend so far, tail of the queue log
    python tests/benchmark/pod.py logs                        # follow the queue log (Ctrl-C only stops following)
    python tests/benchmark/pod.py pull                        # results + cache -> pod-state/ (restarts a stopped pod to do it)
    python tests/benchmark/pod.py down                        # pull, then delete the pod and its volume

Credentials come from <repo>/.env (RUNPOD_API_KEY, RUNPOD_SSH_PRIVATE_KEY, RUNPOD_SSH_PUBLIC_KEY), the second
RunPod account also used by content-machine. The pod's own watchdog ends the container when the queue has been
idle for ORB_IDLE_HOURS or after ORB_MAX_HOURS, so a forgotten pod stops billing for GPU time; its volume keeps
the results until `down`. How it rents, reaches and tears down a pod follows content-machine's remote.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parent
BACKEND = BENCH.parent.parent
REPO = BACKEND.parent
STATE = REPO / "pod.json"
REST = "https://rest.runpod.io/v1"
REMOTE = "/workspace/orb"
# RunPod's Cloudflare front blocks urllib's default User-Agent (content-machine found this the hard way).
USER_AGENT = "orb-testing/1.0 (+https://github.com/josetseph/Orb)"
# 16 GB and up is enough: models load one at a time and the largest we test (Gemma 4 12B Q4) is 7.7 GB.
# Cheapest first: the two checks ran no faster on a 4090 than on an A40, so the card is not the bottleneck.
GPU_TYPES = [
    "NVIDIA RTX A4500", "NVIDIA RTX A5000", "NVIDIA RTX 4000 Ada Generation", "NVIDIA L4", "NVIDIA A40",
    "NVIDIA RTX A6000", "NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 4090",
]
STATE_DIR = REPO / "pod-state"  # everything a pod produced, kept at home so a run can resume on any account
RESERVE_USD = 1.5  # left on the account when a pod stops itself: enough to restart it and copy its volume home
IMAGE = "runpod/pytorch:1.1.0-cu1290-torch291-ubuntu2404"


class PodError(RuntimeError):
    pass


ACCOUNT = "alt"  # set from --account / the recorded pod: "alt" reads RUNPOD_*, "main" reads MAIN_RUNPOD_*


def env() -> dict[str, str]:
    values = {}
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    prefix = "MAIN_" if ACCOUNT == "main" else ""
    names = ("RUNPOD_API_KEY", "RUNPOD_SSH_PRIVATE_KEY", "RUNPOD_SSH_PUBLIC_KEY")
    missing = [prefix + k for k in names if not values.get(prefix + k)]
    if missing:
        raise PodError(f"missing in {REPO / '.env'} for the {ACCOUNT} account: {', '.join(missing)}")
    return {k: values[prefix + k] for k in names}


def balance() -> tuple[float, float]:
    """(credit left in USD, what the account is spending per hour right now)."""
    req = urllib.request.Request(
        f"https://api.runpod.io/graphql?api_key={env()['RUNPOD_API_KEY']}", method="POST",
        data=json.dumps({"query": "query { myself { clientBalance currentSpendPerHr } }"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        me = json.load(resp)["data"]["myself"]
    return float(me["clientBalance"]), float(me["currentSpendPerHr"] or 0)


def api(method: str, path: str, body: dict | None = None) -> object:
    req = urllib.request.Request(
        f"{REST}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {env()['RUNPOD_API_KEY']}", "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise PodError(f"RunPod {method} {path} failed ({exc.code}): {exc.read().decode()[:400]}") from exc
    return json.loads(raw) if raw.strip() else None


def saved() -> dict:
    global ACCOUNT  # noqa: PLW0603 — the recorded pod decides which account's keys reach it
    if not STATE.is_file():
        raise PodError("no pod recorded; run: pod.py up")
    state = json.loads(STATE.read_text())
    ACCOUNT = state.get("account", "alt")
    return state


def pod_info(pod_id: str) -> dict:
    return api("GET", f"/pods/{pod_id}")


def ssh_endpoint(pod: dict) -> tuple[str, int] | None:
    ports = pod.get("portMappings") or {}
    public = ports.get("22") if isinstance(ports, dict) else None
    return (pod["publicIp"], int(public)) if pod.get("publicIp") and public else None


def wait_ssh(pod_id: str, timeout: float = 900) -> tuple[str, int]:
    deadline, last = time.time() + timeout, ""
    while time.time() < deadline:
        pod = pod_info(pod_id)
        status = str(pod.get("desiredStatus"))
        if status != last:
            print(f"  pod {pod_id}: {status}", flush=True)
            last = status
        endpoint = ssh_endpoint(pod)
        if status == "RUNNING" and endpoint and ssh_ok(endpoint):
            return endpoint
        time.sleep(10)
    raise PodError(f"pod {pod_id} not reachable over SSH within {timeout / 60:.0f} min; it is still rented (pod.py down)")


def ssh_args(endpoint: tuple[str, int]) -> list[str]:
    host, port = endpoint
    return ["-i", str(Path(env()["RUNPOD_SSH_PRIVATE_KEY"]).expanduser()), "-p", str(port),
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30", f"root@{host}"]


def ssh(endpoint: tuple[str, int], command: str, *, check: bool = True, capture: bool = False) -> str:
    proc = subprocess.run(["ssh", *ssh_args(endpoint), command], text=True,
                          capture_output=capture, check=False)
    if check and proc.returncode != 0:
        raise PodError(f"remote command failed ({proc.returncode}): {command}\n{proc.stderr if capture else ''}")
    return proc.stdout if capture else ""


def ssh_ok(endpoint: tuple[str, int]) -> bool:
    return subprocess.run(["ssh", *ssh_args(endpoint), "true"], capture_output=True, check=False).returncode == 0


def push_code(endpoint: tuple[str, int], extra: list[str] = ()) -> None:
    """The repo's tracked files (as they are in the working tree), streamed as a tar. Data on the pod is kept.

    The list of uploaded files is left on the pod as ``.uploaded``, so ``pull`` brings back only what the pod made.
    """
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    # Files named explicitly (a new spec) go up even when git does not track them yet.
    files += b"".join(str(Path(f).resolve().relative_to(REPO)).encode() + bytes([0]) for f in extra)
    tar = subprocess.Popen(["tar", "--no-xattrs", "--no-mac-metadata", "-czf", "-", "--null", "-T", "-"], cwd=REPO,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={"COPYFILE_DISABLE": "1"})
    untar = subprocess.Popen(["ssh", *ssh_args(endpoint), f"mkdir -p {REMOTE} && tar --no-same-owner --warning=no-unknown-keyword -xzf - -C {REMOTE}"], stdin=tar.stdout)
    tar.stdin.write(files)
    tar.stdin.close()
    tar.stdout.close()
    if untar.wait() != 0 or tar.wait() != 0:
        raise PodError("uploading the code failed")
    listing = subprocess.run(["ssh", *ssh_args(endpoint), f"cat > {REMOTE}/.uploaded"],
                             input=files.replace(bytes([0]), bytes([10])), check=False).returncode
    if listing != 0:
        raise PodError("recording the uploaded file list failed")
    print("  code uploaded", flush=True)


def provision(endpoint: tuple[str, int], state: dict) -> None:
    llama = subprocess.run([str(BACKEND / ".venv/bin/python"), "-c", "import llama_cpp; print(llama_cpp.__version__)"],
                           capture_output=True, text=True, check=True).stdout.strip()
    ssh(endpoint, f"cd {REMOTE}/backend && export LLAMA_CPP_VERSION={llama} ORB_MAX_HOURS={state['max_hours']} "
                  f"ORB_IDLE_HOURS={state['idle_hours']} && ( nohup setsid tests/benchmark/pod/provision.sh "
                  f"> /dev/null 2>&1 < /dev/null & )")
    print(f"  installing on the pod (llama-cpp-python {llama} built for CUDA); log: {REMOTE}/pod-provision.log", flush=True)
    shown = 0
    while True:
        time.sleep(20)
        log = ssh(endpoint, f"cat {REMOTE}/pod-provision.log 2>/dev/null; echo; echo STATE=$(cat {REMOTE}/pod-state)",
                  check=False, capture=True)
        lines = [line for line in log.splitlines() if line.startswith("[provision")]
        for line in lines[shown:]:
            print("  " + line, flush=True)
        shown = len(lines)
        state_line = log.strip().splitlines()[-1] if log.strip() else ""
        if state_line == "STATE=ready":
            return
        if state_line.startswith("STATE=failed"):
            raise PodError(f"provisioning {state_line[6:]}; the pod is still rented (pod.py down to stop billing)")


def cmd_up(args) -> None:
    if STATE.is_file():
        raise PodError(f"a pod is already recorded in {STATE}; use it, or pod.py down first")
    cfg = env()
    body = {
        "name": "orb-benchmark", "imageName": args.image, "gpuTypeIds": args.gpu or GPU_TYPES,
        "gpuTypePriority": "availability", "cloudType": "SECURE", "gpuCount": 1,
        "containerDiskInGb": 40, "volumeInGb": args.volume_gb, "volumeMountPath": "/workspace",
        "ports": ["22/tcp"], "supportPublicIp": True,
        "env": {"PUBLIC_KEY": Path(cfg["RUNPOD_SSH_PUBLIC_KEY"]).expanduser().read_text().strip()},
    }
    credit, _ = balance()
    if credit <= RESERVE_USD + 0.5:
        raise PodError(f"the {ACCOUNT} account has ${credit:.2f}; not renting below the ${RESERVE_USD:.2f} reserve")
    pod = api("POST", "/pods", body)
    rate = float(pod.get("costPerHr") or 0) or 1.0
    # The pod stops itself before the account runs dry, keeping the reserve to restart it and copy its volume home.
    budget_hours = max(0.25, (credit - RESERVE_USD) / rate)
    state = {"id": pod["id"], "account": ACCOUNT, "created": time.time(), "rate": rate, "credit_at_start": credit,
             "max_hours": round(min(args.max_hours, budget_hours), 2), "idle_hours": args.idle_hours}
    STATE.write_text(json.dumps(state, indent=2))
    print(f"  rented pod {pod['id']} on the {ACCOUNT} account ({pod.get('machine', {}).get('gpuTypeId') or pod.get('gpuTypeId') or 'gpu'}"
          f", ${rate:.2f}/hr). Credit ${credit:.2f}: it stops itself after {state['max_hours']} h at the latest.", flush=True)
    try:
        endpoint = wait_ssh(pod["id"])
        push_code(endpoint)
        if args.restore:
            restore(endpoint)
        provision(endpoint, state)
    except BaseException:
        print("\n  setup did not finish. The pod is still rented and billing: pod.py status / pod.py down", file=sys.stderr)
        raise
    print("  ready. Start work with: pod.py run live-check sweeps/<spec>.json ...")


def restore(endpoint: tuple[str, int]) -> None:
    """Put what earlier pods produced (results, model-call cache) onto this one, so finished runs are skipped."""
    if not STATE_DIR.is_dir():
        raise PodError(f"nothing to restore: {STATE_DIR} does not exist (pod.py pull saves there)")
    tar = subprocess.Popen(["tar", "--no-xattrs", "--no-mac-metadata", "-czf", "-", "-C", str(STATE_DIR), "."],
                           stdout=subprocess.PIPE, env={"COPYFILE_DISABLE": "1"})
    untar = subprocess.run(["ssh", *ssh_args(endpoint), f"tar --no-same-owner -xzf - -C {REMOTE}"], stdin=tar.stdout, check=False)
    if untar.returncode != 0 or tar.wait() != 0:
        raise PodError("restoring the saved state onto the pod failed")
    print(f"  restored {STATE_DIR} onto the pod", flush=True)


def cmd_setup(_args) -> None:
    """Upload and install again on the recorded pod: after a failed or interrupted `up`, or new dependencies."""
    state = saved()
    endpoint = wait_ssh(state["id"])
    push_code(endpoint)
    provision(endpoint, state)
    print("  ready. Start work with: pod.py run live-check sweeps/<spec>.json ...")


def running_endpoint() -> tuple[str, int]:
    pod = pod_info(saved()["id"])
    endpoint = ssh_endpoint(pod)
    if pod.get("desiredStatus") != "RUNNING" or not endpoint:
        raise PodError(f"pod is {pod.get('desiredStatus')}; `pod.py pull` restarts a stopped pod to collect results")
    return endpoint


def cmd_run(args) -> None:
    endpoint = running_endpoint()
    busy = ssh(endpoint, f"[ -f {REMOTE}/Results/queue.pid ] && kill -0 $(cat {REMOTE}/Results/queue.pid) 2>/dev/null && echo busy",
               check=False, capture=True).strip()
    if busy:
        raise PodError("a queue is already running on the pod (pod.py logs)")
    for item in args.items:
        if item != "live-check" and not (BACKEND / item).is_file():
            raise PodError(f"no such spec: {BACKEND / item}")
    push_code(endpoint, [str(BACKEND / i) for i in args.items if i != "live-check"])
    items = " ".join(f"'{i}'" for i in args.items)
    ssh(endpoint, f"cd {REMOTE}/backend && mkdir -p {REMOTE}/Results && ( nohup setsid tests/benchmark/pod/queue.sh {items} "
                  f">> {REMOTE}/Results/queue.log 2>&1 < /dev/null & )")
    print(f"  queue started: {' -> '.join(args.items)}\n  follow it with: pod.py logs")


def cmd_status(_args) -> None:
    state = saved()
    pod = pod_info(state["id"])
    rate = float(pod.get("costPerHr") or 0)
    hours = (time.time() - state["created"]) / 3600
    print(f"pod {state['id']}  {pod.get('desiredStatus')}  {pod.get('machine', {}).get('gpuTypeId') or ''}  "
          f"${rate:.2f}/hr  {hours:.1f} h since rented (about ${rate * hours:.2f} if it ran throughout)")
    endpoint = ssh_endpoint(pod)
    if pod.get("desiredStatus") == "RUNNING" and endpoint:
        print(ssh(endpoint, f"echo \"setup: $(cat {REMOTE}/pod-state 2>/dev/null)\"; "
                            f"[ -f {REMOTE}/Results/queue.done ] && echo \"queue finished $(cat {REMOTE}/Results/queue.done)\"; "
                            f"tail -n 15 {REMOTE}/Results/queue.log 2>/dev/null | tr '\\r' '\\n' | tail -n 15; "
                            f"nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null",
                  check=False, capture=True))


def cmd_logs(_args) -> None:
    try:
        subprocess.run(["ssh", *ssh_args(running_endpoint()), f"tail -n 40 -f {REMOTE}/Results/queue.log"], check=False)
    except KeyboardInterrupt:
        pass


def copy_home(endpoint: tuple[str, int]) -> None:
    """Results the pod produced and its model-call cache -> pod-state/. Safe to repeat; it only adds and updates."""
    STATE_DIR.mkdir(exist_ok=True)
    produced = (f"cd {REMOTE} && {{ find Results -type f | grep -vxF -f .uploaded; find llm-cache -type f 2>/dev/null; "
                f"ls pod-provision.log pod-watchdog.log 2>/dev/null; }} | tar -czf - -T -")
    tar = subprocess.Popen(["ssh", *ssh_args(endpoint), produced], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-xzf", "-", "-C", str(STATE_DIR)], stdin=tar.stdout, check=True)
    if tar.wait() != 0:
        raise PodError("copying results from the pod failed")


def cmd_pull(_args) -> None:
    state = saved()
    pod = pod_info(state["id"])
    restarted = False
    if pod.get("desiredStatus") != "RUNNING":
        print(f"  pod is {pod.get('desiredStatus')}; starting it to reach its volume", flush=True)
        api("POST", f"/pods/{state['id']}/start")
        restarted = True
    copy_home(wait_ssh(state["id"]))
    print(f"  results and cache copied to {STATE_DIR}")
    if restarted:
        api("POST", f"/pods/{state['id']}/stop")
        print("  pod stopped again")


def stop_gracefully(state: dict, reason: str) -> None:
    """Interrupt the queue (finished runs are already on disk), copy everything home, stop the pod (volume kept)."""
    try:
        endpoint = running_endpoint()
        ssh(endpoint, "pkill -INT -f tests/benchmark/ ; sleep 45; pkill -f 'run.py|data/bin/' ; sync", check=False)
        copy_home(endpoint)
    finally:
        api("POST", f"/pods/{state['id']}/stop")
    (STATE_DIR / "STOPPED.txt").write_text(f"{time.strftime('%F %T')} {reason}\n")
    print(f"STOPPED: {reason}. Progress saved in {STATE_DIR}; the pod is stopped with its volume kept.", flush=True)


def cmd_follow(args) -> None:
    """Keep progress at home while a queue runs, and stop before the account runs dry. Prints one line per event."""
    state = saved()
    while True:
        pod = pod_info(state["id"])
        status = pod.get("desiredStatus")
        try:
            credit, spend = balance()
        except (urllib.error.URLError, KeyError, ValueError):
            credit, spend = float("inf"), 0.0
        if status != "RUNNING":
            # The watchdog stopped it (budget, time cap, idle) or RunPod did (no credit). Collect what is there.
            try:
                cmd_pull(args)
            except PodError as exc:
                print(f"STOPPED: pod is {status} and could not be restarted to copy its volume: {exc}", flush=True)
                return
            (STATE_DIR / "STOPPED.txt").write_text(f"{time.strftime('%F %T')} pod was {status}\n")
            print(f"STOPPED: pod is {status} (watchdog or RunPod). Progress saved in {STATE_DIR}.", flush=True)
            return
        endpoint = running_endpoint()
        copy_home(endpoint)
        done = ssh(endpoint, f"cat {REMOTE}/Results/queue.done 2>/dev/null", check=False, capture=True).strip()
        if done:
            print(f"DONE: queue finished {done}. Results in {STATE_DIR}; the pod idles until its watchdog stops it (or pod.py down).", flush=True)
            return
        hours_left = (credit - RESERVE_USD) / spend if spend else float("inf")
        print(f"{time.strftime('%H:%M')} saved; credit ${credit:.2f}, spending ${spend:.2f}/hr, about {hours_left:.1f} h before the reserve", flush=True)
        if credit - RESERVE_USD < spend * (args.every / 60 + 0.25):
            stop_gracefully(state, f"credit ${credit:.2f} is about to reach the ${RESERVE_USD:.2f} reserve on the {ACCOUNT} account")
            return
        time.sleep(args.every * 60)


def cmd_down(args) -> None:
    state = saved()
    if not args.no_pull:
        cmd_pull(args)
    for attempt in range(3):
        try:
            api("DELETE", f"/pods/{state['id']}")
            STATE.unlink()
            print(f"  pod {state['id']} deleted")
            return
        except PodError as exc:
            if attempt == 2:
                raise PodError(f"{exc}\n  the pod is still billing; retry pod.py down or delete it in the RunPod console") from exc
            time.sleep(5 * (attempt + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up")
    up.add_argument("--gpu", action="append", help="GPU type id, in order of preference (repeatable)")
    up.add_argument("--image", default=IMAGE)
    up.add_argument("--volume-gb", type=int, default=80, help="persistent /workspace: models, indexes, cache, results")
    up.add_argument("--max-hours", type=float, default=48, help="the pod stops itself after this long, whatever it is doing")
    up.add_argument("--idle-hours", type=float, default=3, help="...or after this long with no queue running")
    up.add_argument("--restore", action="store_true", help=f"put {STATE_DIR.name}/ (results + model-call cache) onto the new pod")
    run = sub.add_parser("run")
    run.add_argument("items", nargs="+", help='"live-check" or sweep spec paths relative to backend/')
    for name in ("setup", "status", "logs", "pull"):
        sub.add_parser(name)
    follow = sub.add_parser("follow")
    follow.add_argument("--every", type=float, default=15, help="minutes between copies home and balance checks")
    down = sub.add_parser("down")
    down.add_argument("--no-pull", action="store_true")
    ap.add_argument("--account", choices=["alt", "main"], default="alt", help="which RunPod account's keys to use for `up`")
    args = ap.parse_args()
    global ACCOUNT  # noqa: PLW0603
    ACCOUNT = args.account
    try:
        {"up": cmd_up, "setup": cmd_setup, "follow": cmd_follow, "run": cmd_run, "status": cmd_status, "logs": cmd_logs, "pull": cmd_pull, "down": cmd_down}[args.cmd](args)
    except PodError as exc:
        sys.exit(f"pod: {exc}")


if __name__ == "__main__":
    main()
