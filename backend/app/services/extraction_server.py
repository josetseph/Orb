"""llama-server for ingestion: the extraction model with parallel slots and JSON grammar.

In-process, llama.cpp's JSON grammar made extraction 2.4-4x slower; through
llama-server with parallel slots it cost nothing measurable (retrieval study,
notes of Sep 28). So ingestion calls go to a llama-server running the
extraction model, started when a note needs it and stopped after
``IDLE_SECONDS`` without a request so its memory goes back to chat and media.

The binary is a pinned llama.cpp release, downloaded on first use into the
data folder: Metal on macOS; on Windows and Linux CUDA when an NVIDIA driver
is present, else Vulkan.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen, urlretrieve

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger("ExtractionServer")

BUILD = "b11617"
IDLE_SECONDS = 300
_RELEASES = f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}"
# Thinking off, as the in-process runtime renders every template (local_models._ThinkingOff).
_TEMPLATE_KWARGS = {"enable_thinking": False}


def slots_for_ram(ram_gb: float) -> int:
    """Notes extracted at once. ponytail: RAM tiers, not a measured KV budget; tune if 16 GB Macs swap."""
    if ram_gb < 20:
        return 1
    if ram_gb < 40:
        return 2
    if ram_gb < 80:
        return 4
    return 6


def _assets() -> list[str]:
    """Release files for this machine; the first holds llama-server, any second the CUDA runtime."""
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return [f"llama-{BUILD}-bin-macos-{'arm64' if machine == 'arm64' else 'x64'}.tar.gz"]
    nvidia = shutil.which("nvidia-smi") is not None
    if sys.platform == "win32":
        if nvidia:
            return [f"llama-{BUILD}-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"]
        return [f"llama-{BUILD}-bin-win-vulkan-x64.zip"]
    if nvidia:
        return [
            f"llama-{BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz",
            f"cudart-llama-{BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz",
        ]
    return [f"llama-{BUILD}-bin-ubuntu-vulkan-x64.tar.gz"]


def _extract(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")


def binary() -> Path:
    """llama-server, downloading this build first if it is not there yet."""
    root = Path(settings.DATA_DIR) / "bin" / f"llama.cpp-{BUILD}"
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    found = next(root.rglob(name), None) if root.is_dir() else None
    if found:
        return found
    staging = root.with_name(root.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for asset in _assets():
        logger.info("Downloading %s", asset)
        archive = staging / asset
        urlretrieve(f"{_RELEASES}/{asset}", archive)  # noqa: S310 — pinned GitHub release
        _extract(archive, staging)
        archive.unlink()
    found = next(staging.rglob(name), None)
    if found is None:
        raise RuntimeError(f"llama.cpp {BUILD} has no {name} for this machine")
    if sys.platform == "win32" and len(_assets()) > 1:
        # The CUDA runtime DLLs must sit beside the binary.
        for dll in staging.glob("*.dll"):
            if dll.parent != found.parent:
                shutil.move(str(dll), found.parent / dll.name)
    found.chmod(0o755)
    shutil.rmtree(root, ignore_errors=True)
    staging.rename(root)
    return next(root.rglob(name))


def _is_llama_server(pid: int) -> bool:
    """Whether ``pid`` is still a llama-server: the number may have been reused since."""
    cmd = (
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"] if sys.platform == "win32" else ["ps", "-p", str(pid), "-o", "comm="]
    )
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout
    except OSError:
        return False
    return "llama-server" in out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ExtractionServer:
    """One llama-server process; ``url_for`` starts it (or swaps its model) on demand."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._model: Path | None = None
        self._port = 0
        self._last_use = 0.0
        self._in_flight = 0
        self._watcher: threading.Thread | None = None

    @property
    def slots(self) -> int:
        from app.services.model_catalog import total_ram_gb

        return slots_for_ram(total_ram_gb())

    @property
    def _pid_file(self) -> Path:
        return Path(settings.DATA_DIR) / "llama-server.pid"

    def url_for(self, gguf: Path) -> str:
        """The base URL of a server running ``gguf``; blocks while it loads."""
        with self._lock:
            self._last_use = time.monotonic()
            if self._proc and self._proc.poll() is None and self._model == gguf:
                return f"http://127.0.0.1:{self._port}"
            self._stop_locked()
            self._start_locked(gguf)
            return f"http://127.0.0.1:{self._port}"

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1
            self._last_use = time.monotonic()

    def end(self) -> None:
        with self._lock:
            self._in_flight -= 1
            self._last_use = time.monotonic()

    def _start_locked(self, gguf: Path) -> None:
        self._kill_stale()
        from app.services.local_models import find_mmproj

        exe = binary()
        self._port = _free_port()
        slots = self.slots
        cmd = [
            str(exe), "-m", str(gguf), "--host", "127.0.0.1", "--port", str(self._port),
            "-ngl", "999", "-np", str(slots), "-c", str(slots * int(settings.LLAMA_N_CTX)),
            "--jinja", "--chat-template-kwargs", json.dumps(_TEMPLATE_KWARGS),
            # "auto": the reply is what the model generated; with "none" Gemma 4 12B's replies began with the
            # empty thinking block its template opens (retrieval study, 8c4a689).
            "--reasoning-format", "auto", "--no-webui",
            "-fa", "on" if settings.LLAMA_FLASH_ATTN else "off",
            "--repeat-penalty", str(settings.LLAMA_REPEAT_PENALTY),
            # The host-RAM prompt cache can restore an unrelated conversation into a slot under concurrent
            # load (llama.cpp #27148): off.
            "--cache-ram", "0",
        ]
        mmproj = find_mmproj(gguf)
        if mmproj:
            cmd += ["--mmproj", str(mmproj)]  # image descriptions, without a second copy of the model
        log = Path(settings.DATA_DIR) / "logs" / "llama-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Starting llama-server: %s, %d slots", gguf.name, slots)
        with open(log, "ab") as out:
            self._proc = subprocess.Popen(  # pylint: disable=consider-using-with
                cmd, stdout=out, stderr=subprocess.STDOUT, cwd=exe.parent
            )
        self._pid_file.write_text(str(self._proc.pid))
        self._model = gguf
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._proc = None
                raise RuntimeError(f"llama-server exited while loading {gguf.name}; see {log}")
            try:
                with urlopen(f"http://127.0.0.1:{self._port}/health", timeout=2) as resp:  # noqa: S310 — loopback
                    if resp.status == 200:
                        break
            except OSError:
                pass  # 503 while loading, or not listening yet
            time.sleep(1)
        else:
            self._stop_locked()
            raise RuntimeError(f"llama-server did not finish loading {gguf.name} in 10 minutes")
        if self._watcher is None or not self._watcher.is_alive():
            self._watcher = threading.Thread(target=self._watch_idle, daemon=True, name="llama-server-idle")
            self._watcher.start()

    def _watch_idle(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                if self._proc is None:
                    return
                if self._in_flight == 0 and time.monotonic() - self._last_use > IDLE_SECONDS:
                    logger.info("llama-server idle for %d s: stopping", IDLE_SECONDS)
                    self._stop_locked()
                    return

    def _stop_locked(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._model = None
        self._pid_file.unlink(missing_ok=True)

    def _kill_stale(self) -> None:
        """A server left by a crashed backend still holds the model's memory."""
        try:
            pid = int(self._pid_file.read_text())
        except (OSError, ValueError):
            return
        if _is_llama_server(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info("Stopped a llama-server left from an earlier run (pid %d)", pid)
            except OSError:
                pass
        self._pid_file.unlink(missing_ok=True)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


extraction_server = ExtractionServer()


def _gguf_for(model: str | None) -> Path:
    """The GGUF a model ref names; the Setup selection's chat model for none."""
    from app.services.local_models import gguf_paths_if_present, local_llama_runtime

    gguf = local_llama_runtime.resolve_chat_gguf(model)
    if gguf is None:
        chat = (gguf_paths_if_present() or {}).get("chat")
        if not chat:
            raise RuntimeError("No chat model is downloaded. Open Models to download one.")
        gguf = Path(chat)
    return gguf


def has_vision(model: str | None) -> bool:
    from app.services.local_models import find_mmproj

    return find_mmproj(_gguf_for(model)) is not None


class _Completions:
    def create(self, *, model: str | None = None, messages: list[dict], **kwargs):
        """An OpenAI chat completion on the extraction server, which starts (or swaps model) on demand.

        ``response_format`` JSON becomes llama.cpp's JSON grammar, the one the in-process runtime
        applies for it: llama-server's own response_format lets a thinking template reason first.
        """
        from openai import OpenAI

        gguf = _gguf_for(model)
        response_format = kwargs.pop("response_format", None)
        if response_format and response_format.get("type") == "json_object":
            from llama_cpp.llama_grammar import JSON_GBNF

            kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "grammar": JSON_GBNF}
        extraction_server.begin()
        try:
            url = extraction_server.url_for(gguf)
            # Long: a 12B extraction of a long note takes minutes, more with other slots busy.
            client = OpenAI(base_url=f"{url}/v1", api_key="local", timeout=3600.0, max_retries=0)
            return client.chat.completions.create(model=gguf.name, messages=messages, **kwargs)
        finally:
            extraction_server.end()


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class ServerChatClient:
    """The OpenAI client surface ``LLMService`` uses, backed by the extraction server."""

    def __init__(self) -> None:
        self.chat = _Chat()
