"""One llama.cpp server per model, with parallel slots, for experiments that run many notes or questions at once.

The desktop app runs the model in-process, one request at a time. On a rented GPU that leaves most of the card
idle: decoding one sequence is limited by memory bandwidth, and llama-server decodes all its slots in one batch.
Each model gets a fixed port, so its URL (part of the call-cache key) is the same in every lane and every round.
One model is resident at a time; starting another stops the rest to free GPU memory.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from levers import LOCAL_CHAT  # noqa: E402

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
STATE = REPO / "model-servers"
BINARY = Path(os.environ.get("LLAMA_SERVER") or REPO / "llama.cpp" / "build" / "bin" / "llama-server")


def port(model_id: str) -> int:
    return 8100 + LOCAL_CHAT.index(model_id)


def url(model_id: str) -> str:
    return f"http://127.0.0.1:{port(model_id)}"


def _props(model_id: str) -> dict | None:
    try:
        with urlopen(url(model_id) + "/props", timeout=5) as resp:  # noqa: S310 — loopback
            return json.loads(resp.read())
    except OSError:
        return None


def _stop(pid_file: Path) -> None:
    try:
        os.kill(int(pid_file.read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pid_file.unlink(missing_ok=True)


# Thinking off unless a request asks for it (chat_template_kwargs, ENABLE_THINKING / EXTRACTION_ENABLE_THINKING).
# llama-server otherwise passes enable_thinking=true, and Gemma 4 then reasons for thousands of tokens before
# its JSON; in-process, its template leaves thinking off. (Qwen 3.5 4B/9B think by default in-process.)
TEMPLATE_KWARGS = {"enable_thinking": False}


def fingerprint(ctx_per_slot: int, flash_attn: bool, repeat_penalty: float) -> str:
    """What about the server shapes a reply (not its slot count): it joins the call-cache key."""
    return json.dumps({"engine": version(), "template_kwargs": TEMPLATE_KWARGS, "ctx_per_slot": ctx_per_slot,
                       "flash_attn": flash_attn, "repeat_penalty": repeat_penalty, "reasoning_format": "none",
                       "json_constraint": "in-process GBNF", "cache_ram": 0}, sort_keys=True)


def ensure(model_id: str, gguf: Path, slots: int, ctx_per_slot: int, flash_attn: bool, repeat_penalty: float) -> str:
    """Start (or reuse) the server for this model and return its URL."""
    STATE.mkdir(exist_ok=True)
    template_kwargs = TEMPLATE_KWARGS
    wanted = {"model": str(gguf), "slots": slots, "ctx_per_slot": ctx_per_slot, "flash_attn": flash_attn,
              "repeat_penalty": repeat_penalty, "template_kwargs": template_kwargs, "cache_ram": 0}
    config = STATE / f"{model_id}.json"
    pid_file = STATE / f"{model_id}.pid"
    if _props(model_id) is not None and config.is_file() and json.loads(config.read_text()) == wanted:
        return url(model_id)
    for other in STATE.glob("*.pid"):  # one model resident: the GPU also holds each lane's embedder and reranker
        _stop(other)
    time.sleep(3)
    cmd = [str(BINARY), "-m", str(gguf), "--alias", model_id, "--host", "127.0.0.1", "--port", str(port(model_id)),
           "-ngl", "999", "-np", str(slots), "-c", str(slots * ctx_per_slot), "--jinja",
           # Raw replies, as the in-process runtime returns them: the backend separates any thinking itself.
           "--reasoning-format", "none", "-fa", "on" if flash_attn else "off", "--no-webui",
           "--chat-template-kwargs", json.dumps(template_kwargs),
           # The in-process runtime's sampling defaults (llama-cpp-python's top-k/top-p/min-p match llama-server's).
           "--repeat-penalty", str(repeat_penalty),
           # The host-RAM prompt cache can restore an unrelated conversation into a slot under concurrent load,
           # silently (llama.cpp issue #27148, open as of 2026-10): off.
           "--cache-ram", "0"]
    with open(STATE / f"{model_id}.log", "ab") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pid_file.write_text(str(proc.pid))
    config.write_text(json.dumps(wanted))
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"[model-server] {model_id} exited while loading; see {STATE / f'{model_id}.log'}")
        props = _props(model_id)
        if props and props.get("total_slots") == slots:
            print(f"[model-server] {model_id}: {slots} slots x {ctx_per_slot} tokens at {url(model_id)}", flush=True)
            return url(model_id)
        time.sleep(3)
    sys.exit(f"[model-server] {model_id} did not come up in 15 minutes")


def version() -> str:
    out = subprocess.run([str(BINARY), "--version"], capture_output=True, text=True, check=False)
    return (out.stdout + out.stderr).strip().splitlines()[0] if (out.stdout + out.stderr).strip() else "unknown"
