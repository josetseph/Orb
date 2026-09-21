"""Run the pipeline bare: fetch Qdrant + Meilisearch once, start them, start the API.

    cd backend && python run.py            # API on :8000, Qdrant :6333, Meili :7700

Binaries land in DATA_DIR/bin/<platform> (DATA_DIR = ORB_DATA_DIR or ../data).
Both sidecars are stopped when the API exits. No Docker.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
QDRANT_VERSION = os.environ.get("ORB_QDRANT_VERSION", "v1.18.2")
MEILI_VERSION = os.environ.get("ORB_MEILI_VERSION", "v1.49.0")
API_PORT = int(os.environ.get("ORB_API_PORT", "8000"))
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
MEILI_PORT = int(os.environ.get("MEILI_PORT", "7700"))
MEILI_MASTER_KEY = os.environ.get("MEILI_MASTER_KEY", "orb-dev-key")

_children: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[run] {msg}", flush=True)


def _triple() -> dict:
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if MAC and not arm:  # Rosetta reports x86_64; ask the hardware
        arm = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, check=False).stdout.strip() == "1"
    if MAC:
        return {"key": "macos-arm64" if arm else "macos-x86_64",
                "qdrant": "qdrant-aarch64-apple-darwin.tar.gz" if arm else "qdrant-x86_64-apple-darwin.tar.gz",
                "meili": "meilisearch-macos-apple-silicon" if arm else "meilisearch-macos-amd64"}
    if WIN:
        return {"key": "windows-amd64", "qdrant": "qdrant-x86_64-pc-windows-msvc.zip", "meili": "meilisearch-windows-amd64.exe"}
    return {"key": "linux-aarch64" if arm else "linux-x86_64",
            "qdrant": "qdrant-aarch64-unknown-linux-musl.tar.gz" if arm else "qdrant-x86_64-unknown-linux-gnu.tar.gz",
            "meili": "meilisearch-linux-aarch64" if arm else "meilisearch-linux-amd64"}


def _exe(name: str) -> str:
    return f"{name}.exe" if WIN else name


def _download(url: str, dest: Path) -> None:
    log(f"Downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".partial")
    with urlopen(url, timeout=600) as resp, open(tmp, "wb") as out:  # noqa: S310
        total, done, shown = int(resp.headers.get("Content-Length") or 0), 0, -1
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and done * 10 // total > shown:
                shown = done * 10 // total
                log(f"  {dest.name}: {shown * 10}% of {total >> 20} MB")
    tmp.replace(dest)


def _make_runnable(path: Path) -> None:
    if WIN:
        return
    path.chmod(0o755)
    if MAC:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(path)], check=False, capture_output=True)


def ensure_binaries(data_dir: Path) -> tuple[Path, Path]:
    t = _triple()
    bin_dir = data_dir / "bin" / t["key"]
    bin_dir.mkdir(parents=True, exist_ok=True)
    qdrant, meili = bin_dir / _exe("qdrant"), bin_dir / _exe("meilisearch")

    if not qdrant.exists():
        archive = bin_dir / t["qdrant"]
        _download(f"https://github.com/qdrant/qdrant/releases/download/{QDRANT_VERSION}/{t['qdrant']}", archive)
        extract = bin_dir / "qdrant-extract"
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(extract, filter="tar")
        found = next(p for p in extract.rglob(_exe("qdrant")) if p.is_file())
        shutil.copyfile(found, qdrant)
        _make_runnable(qdrant)
        shutil.rmtree(extract, ignore_errors=True)
        archive.unlink()
    if not meili.exists():
        _download(f"https://github.com/meilisearch/meilisearch/releases/download/{MEILI_VERSION}/{t['meili']}", meili)
        _make_runnable(meili)
    return qdrant, meili


def _spawn(cmd: list[str], *, cwd: Path, env: dict, logfile: Path) -> subprocess.Popen:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with open(logfile, "ab") as out:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env={**os.environ, **env}, stdin=subprocess.DEVNULL,  # noqa: S603
                                stdout=out, stderr=subprocess.STDOUT, start_new_session=not WIN)
    _children.append(proc)
    return proc


def _wait(url: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"{url}: process exited with {proc.returncode}")
        try:
            with urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status < 500:
                    return
        except Exception:  # pylint: disable=broad-exception-caught
            time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}")


def start_sidecars(data_dir: Path) -> None:
    qdrant, meili = ensure_binaries(data_dir)
    logs = data_dir / "logs"
    storage = data_dir / "qdrant"
    storage.mkdir(parents=True, exist_ok=True)
    log("Starting Qdrant")
    q = _spawn([str(qdrant)], cwd=storage, logfile=logs / "qdrant.log",
               env={"QDRANT__STORAGE__STORAGE_PATH": str(storage), "QDRANT__SERVICE__HTTP_PORT": str(QDRANT_PORT),
                    "QDRANT__SERVICE__HOST": "127.0.0.1"})  # loopback only, like Meilisearch
    _wait(f"http://127.0.0.1:{QDRANT_PORT}/", q)
    log("Starting Meilisearch")
    m = _spawn([str(meili), "--db-path", str(data_dir / "meilisearch"), "--http-addr", f"127.0.0.1:{MEILI_PORT}",
                "--master-key", MEILI_MASTER_KEY], cwd=data_dir, env={}, logfile=logs / "meilisearch.log")
    _wait(f"http://127.0.0.1:{MEILI_PORT}/health", m)


def stop_sidecars() -> None:
    for proc in reversed(_children):
        if proc.poll() is None:
            if WIN:
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
    for proc in _children:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    _children.clear()


def load_dotenv(path: Path) -> None:
    """Settings no longer read .env themselves; KEY=VALUE lines here seed the environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


def main() -> int:
    # Installed before anything slow: a backgrounded process inherits SIGINT=ignore,
    # and uvicorn re-raises the signal after shutdown, which would skip `finally`.
    def on_signal(signum, _frame):
        stop_sidecars()
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    load_dotenv(here / ".env")
    from app.core import paths as orb_paths

    # The desktop app's paths.json points DATA_DIR and the default vault at real
    # user data. Benchmarks must never see it: borrow its models dir (read-only
    # GGUF reuse), then pin everything else to this repo.
    desktop = orb_paths.load_paths_file()
    if "ORB_MODELS_DIR" not in os.environ:
        # Own models dir, shared weights: MODELS_DIR/manifest.json holds the model
        # selection, and the desktop app reads the same file. Link its gguf/ folder
        # (downloads are reused both ways) and start from a copy of its manifest.
        models = here.parent / "models"
        models.mkdir(exist_ok=True)
        shared = Path(desktop["models_dir"]).expanduser() if desktop.get("models_dir") else None
        if shared and (shared / "gguf").is_dir() and not (models / "gguf").exists():
            (models / "gguf").symlink_to(shared / "gguf", target_is_directory=True)
        if shared and (shared / "manifest.json").is_file() and not (models / "manifest.json").exists():
            shutil.copyfile(shared / "manifest.json", models / "manifest.json")
        os.environ["ORB_MODELS_DIR"] = str(models)
    os.environ.setdefault("ORB_DATA_DIR", str(here.parent / "data"))
    data_dir = Path(os.environ["ORB_DATA_DIR"]).expanduser().resolve()
    os.environ["ORB_PATHS_FILE"] = str(data_dir / "paths.json")
    orb_paths._PATHS_CACHE = None  # pylint: disable=protected-access  # re-read from the pinned file
    data_dir = orb_paths.ensure_data_layout(orb_paths.resolve_data_dir())
    # settings are read once at import: fix the sidecar addresses before app.* loads
    os.environ.update(QDRANT_HOST="127.0.0.1", QDRANT_PORT=str(QDRANT_PORT), MEILI_HOST="127.0.0.1",
                      MEILI_PORT=str(MEILI_PORT), MEILI_MASTER_KEY=MEILI_MASTER_KEY)
    log(f"DATA_DIR={data_dir}")
    # Cloud keys live in the OS keychain (no env fallback in the app): seed the
    # store from OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY / HUGGINGFACE_API_KEY.
    from app.services.credentials import CLOUD_PROVIDERS, credentials

    for name in CLOUD_PROVIDERS:
        key = os.environ.get(f"{name.upper()}_API_KEY")
        if key and credentials.get(name) != key:
            credentials.set(name, key)
            log(f"Stored {name} API key from the environment")
    from app.services.kb_registry import kb_registry

    for meta in kb_registry.list_kbs():
        vault = Path(meta.get("vault_path") or "").expanduser().resolve()
        if data_dir != vault and data_dir not in vault.parents:
            log(f"REFUSING TO START: KB '{meta.get('name')}' uses vault {vault}, outside {data_dir}.")
            return 2
    try:
        start_sidecars(data_dir)
        import uvicorn

        log(f"Starting API on http://127.0.0.1:{API_PORT}")
        uvicorn.run("app.main:app", host="127.0.0.1", port=API_PORT)
        return 0
    finally:
        stop_sidecars()


if __name__ == "__main__":
    sys.exit(main())
