"""Desktop runtime: the shell spawns only this process.

It sweeps stale listeners and starts the API at once, then — behind it — fetches
Qdrant / Meilisearch on first run, prepares the embedded Firefly III install
(portable PHP + app + migrations) and spawns all three; they are torn down when
the API exits. Boot progress is written to ``DATA_DIR/boot-status.json``, which
the API surfaces in its maintenance status for the UI.

    python -m app.desktop_runtime                       # run the desktop stack
    python -m app.desktop_runtime prefetch-firefly DEST # build-time Firefly seed

Ports come from ``ORB_{API,FIREFLY,QDRANT,MEILI}_PORT`` (defaults below); data
and models dirs from ``ORB_DATA_DIR`` / ``ORB_MODELS_DIR`` / paths.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.request import urlopen

from app.core.paths import BACKEND_DIR, REPO_ROOT, resolve_data_dir, resolve_models_dir

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"

QDRANT_VERSION = os.environ.get("ORB_QDRANT_VERSION", "v1.18.2")
MEILI_VERSION = os.environ.get("ORB_MEILI_VERSION", "v1.49.0")
FIREFLY_VERSION = os.environ.get("ORB_FIREFLY_VERSION", "v6.6.6")
PHP_BIN_VERSION = os.environ.get("ORB_PHP_BIN_VERSION", "1.2.0")
PHP_RUNTIME_ID = f"nativephp:{PHP_BIN_VERSION}:php-8.5"


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _port(name: str, default: int) -> int:
    return int(_env(f"ORB_{name}_PORT") or default)


PORTS = {
    "api": _port("API", 17401),
    "firefly": _port("FIREFLY", 17412),
    "qdrant": _port("QDRANT", 17433),
    "meili": _port("MEILI", 17470),
}


def _local(port: int) -> str:
    return f"http://127.0.0.1:{port}"


# ── Status for the splash screen ─────────────────────────────────────────────

_STATUS_FILE: Path | None = None


def status(message: str) -> None:
    print(f"[desktop] {message}", flush=True)
    if _STATUS_FILE is None:
        return
    try:
        tmp = _STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"status": message, "ts": time.time()}), encoding="utf-8")
        tmp.replace(_STATUS_FILE)
    except OSError:
        pass


# ── Small file / process helpers ─────────────────────────────────────────────


def _chmod_exec(path: Path) -> None:
    if WIN:
        return
    try:
        path.chmod(0o755)
    except OSError:
        pass


def _strip_quarantine(path: Path) -> None:
    if not MAC:
        return
    subprocess.run(
        ["xattr", "-dr", "com.apple.quarantine", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _write_private(path: Path, text: str) -> None:
    """Owner-only file: secrets live in dirs that may be cloud-synced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, data) -> None:
    _write_private(path, json.dumps(data, indent=2) + "\n")


def _find_file(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.is_file():
        return direct
    return next((p for p in root.rglob(name) if p.is_file()), None)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _download(url: str, dest: Path, label: str) -> None:
    from app.services.local_models import download_file  # settings import is heavy; keep it lazy

    last = -10

    def on_progress(pct: int) -> None:
        nonlocal last
        if pct >= last + 10:
            last = pct
            status(f"Downloading {label}… {pct}%")

    dest.parent.mkdir(parents=True, exist_ok=True)
    download_file(url, dest, on_progress)


def _verify_checksum(path: Path, asset: str) -> None:
    """Optional pin: ORB_SHA256_<ASSET> (non-alphanumerics → underscores)."""
    expected = os.environ.get("ORB_SHA256_" + re.sub(r"[^A-Za-z0-9]", "_", asset).upper())
    if not expected:
        return
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected.strip().lower():
        raise RuntimeError(f"Integrity check failed for {asset}: SHA-256 mismatch")


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        # zipfile drops symlinks and exec bits, which the PHP bundle relies on.
        if not WIN and shutil.which("unzip"):
            subprocess.run(["unzip", "-o", "-q", str(archive), "-d", str(dest)], check=True)
        else:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
        return
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="tar")


def _pids_on_port(port: int) -> list[int]:
    try:
        if WIN:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, check=False
            ).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                    if parts[4].isdigit() and parts[4] != "0":
                        pids.add(int(parts[4]))
            return sorted(pids)
        out = subprocess.run(
            ["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return [int(s) for s in out.split() if s.strip().isdigit()]
    except OSError:
        return []


def _kill_pid(pid: int) -> None:
    if WIN:
        subprocess.run(
            ["taskkill", "/pid", str(pid), "/f", "/t"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    time.sleep(0.8)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def free_ports(ports: list[int]) -> int:
    """Clear listeners left by a previous run that was force-quit."""
    killed = []
    for port in ports:
        for pid in _pids_on_port(port):
            if pid in (os.getpid(), os.getppid()):
                continue
            _kill_pid(pid)
            killed.append(f"{port}→{pid}")
    if killed:
        status(f"Cleared stale processes on ports: {', '.join(killed)}")
    return len(killed)


def _tail(path: Path | None, lines: int = 24) -> str:
    try:
        if path and path.exists():
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]).strip()
    except OSError:
        pass
    return ""


def wait_http(url: str, timeout: float, proc: subprocess.Popen | None = None, log: Path | None = None) -> None:
    start = time.monotonic()
    while True:
        if proc is not None and proc.poll() is not None:
            tail = _tail(log)
            raise RuntimeError(f"Process exited before {url} was ready" + (f"\n\n{tail}" if tail else ""))
        try:
            with urlopen(url, timeout=2) as resp:  # noqa: S310 — loopback only
                # A 5xx is a service that bound its port but is broken.
                if resp.status < 500:
                    return
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        if time.monotonic() - start > timeout:
            tail = _tail(log)
            raise RuntimeError(f"Timeout waiting for {url}" + (f"\n\n{tail}" if tail else ""))
        time.sleep(1)


_children: list[tuple[str, subprocess.Popen]] = []


def _spawn(label: str, cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, log: Path | None = None) -> subprocess.Popen:
    status(f"Starting {label}…")
    out = subprocess.DEVNULL
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "ab")  # noqa: SIM115 — the child owns this handle
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        # Own process group: `php artisan serve` forks the real server, and only a
        # group kill takes both down.
        start_new_session=not WIN,
    )
    if out is not subprocess.DEVNULL:
        out.close()
    _children.append((label, proc))
    return proc


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        pass


def stop_sidecars(grace: float = 1.5) -> None:
    for label, proc in reversed(_children):
        if proc.poll() is not None:
            continue
        status(f"Stopping {label}…")
        if WIN:
            _kill_pid(proc.pid)  # taskkill /t follows the tree
        else:
            _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + grace
    for _label, proc in _children:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        if not WIN:
            _signal_group(proc, signal.SIGKILL)  # stragglers and grandchildren
    _children.clear()


def _log_path(data_dir: Path, label: str) -> Path:
    return data_dir / "logs" / f"{label}.log"


# ── Qdrant + Meilisearch ─────────────────────────────────────────────────────


def _arch() -> str:
    if MAC:
        # A Rosetta-run process reports x86_64; ask the hardware.
        hw = subprocess.run(
            ["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, check=False
        ).stdout.strip()
        if hw == "1":
            return "arm64"
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def _triple() -> dict:
    arm = _arch() == "arm64"
    if MAC:
        return {
            "key": "macos-arm64" if arm else "macos-x86_64",
            "qdrant_asset": "qdrant-aarch64-apple-darwin.tar.gz" if arm else "qdrant-x86_64-apple-darwin.tar.gz",
            "meili_asset": "meilisearch-macos-apple-silicon" if arm else "meilisearch-macos-amd64",
        }
    if WIN:
        return {
            "key": "windows-arm64" if arm else "windows-amd64",
            "qdrant_asset": "qdrant-x86_64-pc-windows-msvc.zip",
            "meili_asset": "meilisearch-windows-amd64.exe",
        }
    return {
        "key": "linux-aarch64" if arm else "linux-x86_64",
        "qdrant_asset": "qdrant-aarch64-unknown-linux-musl.tar.gz" if arm else "qdrant-x86_64-unknown-linux-gnu.tar.gz",
        "meili_asset": "meilisearch-linux-aarch64" if arm else "meilisearch-linux-amd64",
    }


def _exe(name: str) -> str:
    return f"{name}.exe" if WIN else name


def ensure_binaries(data_dir: Path) -> dict[str, Path | None]:
    """Fetch Qdrant + Meilisearch into DATA_DIR/bin/<platform> on first run."""
    triple = _triple()
    bin_dir = data_dir / "bin" / triple["key"]
    tmp = data_dir / "bin" / ".tmp"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    qdrant = bin_dir / _exe("qdrant")
    meili = bin_dir / _exe("meilisearch")

    if not qdrant.exists():
        asset = triple["qdrant_asset"]
        archive = tmp / asset
        status(f"Downloading Qdrant {QDRANT_VERSION}…")
        _download(f"https://github.com/qdrant/qdrant/releases/download/{QDRANT_VERSION}/{asset}", archive, "Qdrant")
        status("Verifying Qdrant checksum…")
        _verify_checksum(archive, asset)
        status("Extracting Qdrant…")
        extract_to = tmp / "qdrant-extract"
        _rmtree(extract_to)
        _extract(archive, extract_to)
        found = _find_file(extract_to, _exe("qdrant"))
        if not found:
            raise RuntimeError(f"Qdrant executable not found in {asset}")
        shutil.copyfile(found, qdrant)
        _chmod_exec(qdrant)
        _strip_quarantine(qdrant)
        archive.unlink(missing_ok=True)
        _rmtree(extract_to)
        status("Qdrant ready")

    if not meili.exists():
        asset = triple["meili_asset"]
        dest = tmp / asset
        status(f"Downloading Meilisearch {MEILI_VERSION}…")
        _download(f"https://github.com/meilisearch/meilisearch/releases/download/{MEILI_VERSION}/{asset}", dest, "Meilisearch")
        status("Verifying Meilisearch checksum…")
        _verify_checksum(dest, asset)
        shutil.copyfile(dest, meili)
        _chmod_exec(meili)
        _strip_quarantine(meili)
        dest.unlink(missing_ok=True)
        status("Meilisearch ready")

    return {"qdrant": qdrant if qdrant.exists() else None, "meilisearch": meili if meili.exists() else None}


def resolve_binary(data_dir: Path, name: str) -> Path | None:
    key = _triple()["key"]
    exe = _exe(name)
    for candidate in (
        data_dir / "bin" / key / exe,
        REPO_ROOT / "desktop" / "binaries" / key / exe,
        data_dir / "bin" / exe,
        REPO_ROOT / "desktop" / "binaries" / exe,
    ):
        if candidate.exists():
            return candidate
    return None


def resolve_meili_master_key(data_dir: Path) -> str:
    """Persist a Meili master key under DATA_DIR. Installs that already hold
    Meili data keep ``orb-dev-key`` for compatibility; fresh installs get a
    random key."""
    from_env = _env("MEILI_MASTER_KEY")
    if from_env:
        return from_env
    key_file = data_dir / "meili_master_key"
    if key_file.exists():
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    meili_db = data_dir / "meilisearch"
    key = "orb-dev-key"
    if not meili_db.exists() or not any(meili_db.iterdir()):
        key = secrets.token_urlsafe(32)
    _write_private(key_file, key + "\n")
    return key


def start_search_engines(data_dir: Path, master_key: str) -> None:
    (data_dir / "qdrant").mkdir(parents=True, exist_ok=True)
    (data_dir / "meilisearch").mkdir(parents=True, exist_ok=True)
    try:
        found = ensure_binaries(data_dir)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        status(f"Binary download failed: {exc}. Retrying from cache…")
        found = {}

    qdrant = found.get("qdrant") or resolve_binary(data_dir, "qdrant")
    if not qdrant:
        raise RuntimeError("Could not download or find Qdrant binary. Check network access.")
    storage = data_dir / "qdrant"
    proc = _spawn(
        "Qdrant",
        [str(qdrant)],
        cwd=storage,
        env={
            "QDRANT__STORAGE__STORAGE_PATH": str(storage),
            "QDRANT__SERVICE__HTTP_PORT": str(PORTS["qdrant"]),
        },
        log=_log_path(data_dir, "qdrant"),
    )
    try:
        wait_http(_local(PORTS["qdrant"]) + "/", 60, proc, _log_path(data_dir, "qdrant"))
    except RuntimeError as exc:
        status(f"Qdrant may still be starting: {exc}")

    meili = found.get("meilisearch") or resolve_binary(data_dir, "meilisearch")
    if not meili:
        raise RuntimeError("Could not download or find Meilisearch binary. Check network access.")
    proc = _spawn(
        "Meilisearch",
        [
            str(meili),
            "--db-path",
            str(data_dir / "meilisearch"),
            "--http-addr",
            f"127.0.0.1:{PORTS['meili']}",
            "--master-key",
            master_key,
        ],
        log=_log_path(data_dir, "meilisearch"),
    )
    try:
        wait_http(_local(PORTS["meili"]) + "/health", 60, proc, _log_path(data_dir, "meilisearch"))
    except RuntimeError as exc:
        status(f"Meilisearch may still be starting: {exc}")


# ── Firefly III (embedded PHP) ───────────────────────────────────────────────


def firefly_root(data_dir: Path) -> Path:
    return data_dir / "firefly"


def firefly_app_dir(data_dir: Path) -> Path:
    return firefly_root(data_dir) / "app"


def firefly_runtime_file(data_dir: Path) -> Path:
    return firefly_root(data_dir) / "runtime.json"


def php_binary(data_dir: Path) -> Path:
    root = firefly_root(data_dir) / "php"
    direct = root / "bin" / _exe("php")
    if direct.exists():
        return direct
    return _find_file(root, _exe("php")) or direct


def _php_marker(root: Path) -> Path:
    return root / ".orb-php-runtime"


def _bundled_firefly_root() -> Path | None:
    """Seed shipped with the app (resources/firefly) or prefetched in the repo."""
    resources = _env("ORB_RESOURCES_ROOT")
    for candidate in (
        Path(resources) / "firefly" if resources else None,
        REPO_ROOT / "desktop" / "resources" / "firefly",
    ):
        if candidate and candidate.is_dir():
            return candidate
    return None


def _php_archive_url() -> str:
    base = f"https://raw.githubusercontent.com/NativePHP/php-bin/refs/tags/{PHP_BIN_VERSION}/bin"
    if MAC:
        return f"{base}/mac/{'arm64' if _arch() == 'arm64' else 'x64'}/php-8.5.zip"
    if WIN:
        return f"{base}/win/x64/php-8.5.zip"
    return f"{base}/linux/x64/php-8.5.zip"


def _ensure_symlink(alias: Path, target: str) -> None:
    try:
        if alias.exists():
            return
        if alias.is_symlink():
            alias.unlink()
        alias.symlink_to(target)
    except OSError:
        pass


def _normalize_mac_php_libs(root: Path) -> None:
    """The NativePHP bundle ships absolute symlinks from the build machine."""
    if not MAC:
        return
    lib = root / "bin" / "php7" / "lib"
    if not lib.is_dir():
        return
    for entry in lib.iterdir():
        if not entry.is_symlink():
            continue
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if not os.path.isabs(target) or not (lib / Path(target).name).exists():
            continue
        try:
            entry.unlink()
            entry.symlink_to(Path(target).name)
        except OSError:
            pass
    _ensure_symlink(lib / "libleveldb.1.dylib", "libleveldb.1.23.0.dylib")
    _ensure_symlink(lib / "libleveldb.dylib", "libleveldb.1.23.0.dylib")
    _ensure_symlink(lib / "libzip.5.dylib", "libzip.5.5.dylib")


def _normalize_php_ini(root: Path) -> None:
    ini = _find_file(root, "php.ini")
    ext_root = root / "bin" / "php7" / "lib" / "php" / "extensions"
    ext_dir = next((d for d in ext_root.iterdir() if d.is_dir()), None) if ext_root.is_dir() else None
    if not ini or not ext_dir:
        return
    text = ini.read_text(encoding="utf-8")
    line = f'extension_dir="{ext_dir}"'
    if re.search(r"^extension_dir\s*=.*$", text, flags=re.M):
        text = re.sub(r"^extension_dir\s*=.*$", line, text, count=1, flags=re.M)
    else:
        text += f"\n{line}\n"
    ini.write_text(text, encoding="utf-8")


def _install_php_tree(source: Path, target: Path, data_dir: Path) -> None:
    _rmtree(target)
    shutil.copytree(source, target, symlinks=True)
    _normalize_mac_php_libs(target)
    _normalize_php_ini(target)
    _php_marker(target).write_text(PHP_RUNTIME_ID + "\n", encoding="utf-8")
    _chmod_exec(php_binary(data_dir))
    _strip_quarantine(target)


def ensure_php_runtime(data_dir: Path, *, allow_bundled: bool = True) -> Path:
    target = firefly_root(data_dir) / "php"
    marker = _php_marker(target)
    if php_binary(data_dir).exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == PHP_RUNTIME_ID:
        _normalize_mac_php_libs(target)
        _normalize_php_ini(target)
        return php_binary(data_dir)
    # Keep the working runtime until a replacement is staged.
    bundled = _bundled_firefly_root() if allow_bundled else None
    if bundled and _php_marker(bundled / "php").exists() and _php_marker(bundled / "php").read_text(encoding="utf-8").strip() == PHP_RUNTIME_ID:
        firefly_root(data_dir).mkdir(parents=True, exist_ok=True)
        _install_php_tree(bundled / "php", target, data_dir)
        status("Using bundled PHP runtime seed")
        return php_binary(data_dir)
    tmp = firefly_root(data_dir) / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    archive = tmp / "php-8.5.zip"
    status("Downloading embedded PHP runtime…")
    _download(_php_archive_url(), archive, "embedded PHP")
    extract_to = tmp / "php"
    _rmtree(extract_to)
    _extract(archive, extract_to)
    if not _find_file(extract_to, _exe("php")):
        raise RuntimeError("Portable PHP executable not found after extraction")
    _install_php_tree(extract_to, target, data_dir)
    status("Embedded PHP ready")
    return php_binary(data_dir)


# Everything under app/storage is user state (sqlite DB, Passport keys, uploads).
_PRESERVED = [
    Path("storage/database"),
    Path("storage/upload"),
    Path("storage/oauth-private.key"),
    Path("storage/oauth-public.key"),
]


def _stash_app_state(app_dir: Path) -> tuple[Path, list[Path]]:
    """Copy user state beside the app tree before replacing it. Never clobber an
    older stash with an empty one: after a failed upgrade it may be the only copy."""
    stash = app_dir.parent / ".app-state-stash"
    building = stash.with_name(stash.name + ".new")
    _rmtree(building)
    stashed = []
    for rel in _PRESERVED:
        source = app_dir / rel
        if not source.exists():
            continue
        target = building / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
        stashed.append(rel)
    if stashed:
        _rmtree(stash)
        building.rename(stash)
        return stash, stashed
    _rmtree(building)
    if stash.exists():
        return stash, [rel for rel in _PRESERVED if (stash / rel).exists()]
    return stash, []


def _restore_app_state(stash: Path, stashed: list[Path], app_dir: Path) -> None:
    for rel in stashed:
        source, target = stash / rel, app_dir / rel
        _rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
    _rmtree(stash)


def _swap_in_app_tree(app_dir: Path, populate) -> None:
    """Replace app_dir with populate()'s output, restoring the old tree if the copy dies."""
    backup = app_dir.with_name(app_dir.name + ".bak")
    _rmtree(backup)
    if app_dir.exists():
        app_dir.rename(backup)
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        populate(app_dir)
    except Exception:
        _rmtree(app_dir)
        if backup.exists():
            backup.rename(app_dir)
        raise
    _rmtree(backup)


def ensure_firefly_app(data_dir: Path, *, allow_bundled: bool = True) -> Path:
    app_dir = firefly_app_dir(data_dir)
    marker = app_dir / ".orb-firefly-version"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == FIREFLY_VERSION:
        return app_dir
    stash, stashed = _stash_app_state(app_dir)
    bundled = _bundled_firefly_root() if allow_bundled else None
    bundled_marker = bundled / "app" / ".orb-firefly-version" if bundled else None
    if bundled_marker and bundled_marker.exists() and bundled_marker.read_text(encoding="utf-8").strip() == FIREFLY_VERSION:
        app_dir.parent.mkdir(parents=True, exist_ok=True)
        _swap_in_app_tree(app_dir, lambda dest: shutil.copytree(bundled / "app", dest, dirs_exist_ok=True))
        _restore_app_state(stash, stashed, app_dir)
        status("Using bundled Firefly app seed")
        return app_dir
    tmp = firefly_root(data_dir) / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    name = f"FireflyIII-{FIREFLY_VERSION}.tar.gz"
    archive = tmp / name
    status(f"Downloading Firefly III {FIREFLY_VERSION}…")
    _download(f"https://github.com/firefly-iii/firefly-iii/releases/download/{FIREFLY_VERSION}/{name}", archive, "Firefly III")
    extract_to = tmp / "firefly-app"
    _rmtree(extract_to)
    _extract(archive, extract_to)
    if not any(extract_to.iterdir()):
        raise RuntimeError("Firefly archive extracted to an empty tree")
    _swap_in_app_tree(app_dir, lambda dest: shutil.copytree(extract_to, dest, dirs_exist_ok=True))
    _restore_app_state(stash, stashed, app_dir)
    marker.write_text(FIREFLY_VERSION + "\n", encoding="utf-8")
    status("Firefly III app ready")
    return app_dir


def _run_php(data_dir: Path, args: list[str], *, env: dict | None = None) -> str:
    root = firefly_root(data_dir)
    result = subprocess.run(  # noqa: S603
        [str(php_binary(data_dir)), *args],
        cwd=str(firefly_app_dir(data_dir)),
        env={**os.environ, "HOME": str(root), "USERPROFILE": str(root), **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"php {' '.join(args)} exited {result.returncode}")
    return result.stdout


def _ensure_firefly_env(data_dir: Path) -> None:
    app_dir = firefly_app_dir(data_dir)
    runtime = _read_json(firefly_runtime_file(data_dir), {}) or {}
    if not str(runtime.get("appKey", "")).startswith("base64:"):
        runtime["appKey"] = "base64:" + base64.b64encode(secrets.token_bytes(32)).decode()
    if len(str(runtime.get("cronToken", ""))) != 32:
        runtime["cronToken"] = secrets.token_urlsafe(48)[:32]
    env = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "APP_KEY": runtime["appKey"],
        "APP_URL": _local(PORTS["firefly"]),
        "SITE_OWNER": runtime.get("email") or "orb@local.invalid",
        "TZ": "UTC",
        "DEFAULT_LANGUAGE": "en_US",
        "DEFAULT_LOCALE": "equal",
        "TRUSTED_PROXIES": "127.0.0.1,::1",
        "LOG_CHANNEL": "stack",
        "DB_CONNECTION": "sqlite",
        "DB_DATABASE": str(app_dir / "storage" / "database" / "firefly.sqlite"),
        "CACHE_DRIVER": "file",
        "SESSION_DRIVER": "file",
        "QUEUE_CONNECTION": "sync",
        "MAIL_MAILER": "log",
        "DKR_CHECK_SQLITE": "true",
        "DKR_RUN_MIGRATION": "false",
        "STATIC_CRON_TOKEN": runtime["cronToken"],
        "AUTHENTICATION_GUARD": "web",
        "APP_NAME": "Orb_Finance",
    }
    _write_private(app_dir / ".env", "\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
    _write_json(firefly_runtime_file(data_dir), runtime)


def _ensure_runtime_metadata(data_dir: Path) -> dict:
    file = firefly_runtime_file(data_dir)
    runtime = _read_json(file, {}) or {}
    runtime.setdefault("email", "orb@local.invalid")
    runtime.setdefault("password", secrets.token_urlsafe(24))
    runtime.setdefault("instanceId", str(uuid.uuid4()))
    _write_json(file, runtime)
    return runtime


def _php_esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


_PHP_BOOT = (
    "require 'vendor/autoload.php'; $app = require 'bootstrap/app.php'; "
    "$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap(); "
)


def _bootstrap_state_script(email: str) -> str:
    return _PHP_BOOT + (
        f"$user = \\FireflyIII\\User::where('email', '{_php_esc(email)}')->first(); "
        "$clients = \\Illuminate\\Support\\Facades\\DB::table('oauth_clients')->count(); "
        "echo json_encode(['user_id' => $user?->id, 'group_id' => $user?->user_group_id, 'clients' => $clients], JSON_UNESCAPED_SLASHES);"
    )


def _bootstrap_user_script(email: str, group_title: str) -> str:
    return _PHP_BOOT + (
        f"$email = '{_php_esc(email)}'; "
        # Via env, not argv — command lines are world-visible in `ps` output.
        "$password = getenv('ORB_FIREFLY_BOOTSTRAP_PASSWORD'); "
        "if (!$password) { fwrite(STDERR, 'Missing bootstrap password env'); exit(1); } "
        f"$groupTitle = '{_php_esc(group_title)}'; "
        "$group = \\FireflyIII\\Models\\UserGroup::firstOrCreate(['title' => $groupTitle]); "
        "$role = \\FireflyIII\\Models\\Role::firstOrCreate(['name' => 'owner'], ['display_name' => 'Owner', 'description' => 'Orb desktop owner']); "
        "$userRole = \\FireflyIII\\Models\\UserRole::firstOrCreate(['title' => 'owner']); "
        "$currency = \\FireflyIII\\Models\\TransactionCurrency::where('code', 'EUR')->first(); "
        "if (!$currency) { fwrite(STDERR, 'Missing EUR currency seed'); exit(1); } "
        "$user = \\FireflyIII\\User::where('email', $email)->first(); "
        "if (!$user) { $user = \\FireflyIII\\User::create(['email' => $email, 'password' => bcrypt($password), 'blocked' => 0, 'blocked_code' => null, 'user_group_id' => $group->id]); } "
        "$user->user_group_id = $group->id; $user->password = bcrypt($password); $user->blocked = 0; $user->blocked_code = null; $user->save(); "
        "if (!$user->roles()->where('roles.id', $role->id)->exists()) { $user->roles()->attach($role->id); } "
        "\\FireflyIII\\Models\\GroupMembership::firstOrCreate(['user_id' => $user->id, 'user_group_id' => $group->id, 'user_role_id' => $userRole->id]); "
        "$group->currencies()->syncWithoutDetaching([$currency->id => ['group_default' => true]]); "
        "$user->currencies()->syncWithoutDetaching([$currency->id => ['user_default' => true]]); "
        "$token = $user->createToken('Orb Desktop')->accessToken; "
        "echo json_encode(['user_id' => $user->id, 'group_id' => $group->id, 'token' => $token], JSON_UNESCAPED_SLASHES);"
    )


def _json_tail(raw: str) -> dict:
    start = raw.rfind("{")
    return json.loads(raw[start:] if start >= 0 else raw)


def ensure_firefly_runtime(data_dir: Path) -> None:
    firefly_root(data_dir).mkdir(parents=True, exist_ok=True)
    ensure_php_runtime(data_dir)
    app_dir = ensure_firefly_app(data_dir)
    for sub in ("storage", "storage/database", "storage/upload", "bootstrap/cache"):
        (app_dir / sub).mkdir(parents=True, exist_ok=True)
    sqlite = app_dir / "storage" / "database" / "firefly.sqlite"
    if not sqlite.exists():
        sqlite.write_bytes(b"")
    runtime = _ensure_runtime_metadata(data_dir)
    _ensure_firefly_env(data_dir)

    status("Migrating Firefly database…")
    _run_php(data_dir, ["artisan", "migrate", "--force"])
    # Full base seed: currency-only seeding left account_types empty.
    status("Seeding Firefly base data…")
    _run_php(data_dir, ["artisan", "db:seed", "--force"])

    # Trust the DB and key files, not runtime flags: an upgrade or a half-finished
    # first run can leave the flags set while the state is gone.
    state = _json_tail(_run_php(data_dir, ["-r", _bootstrap_state_script(runtime["email"])]))
    storage = app_dir / "storage"
    keys_ok = all((storage / k).exists() and (storage / k).stat().st_size > 0 for k in ("oauth-private.key", "oauth-public.key"))
    if not keys_ok:
        status("Preparing Firefly API auth…")
        _run_php(data_dir, ["artisan", "passport:keys", "--force"])
        runtime["apiToken"] = None  # tokens minted under the old keypair no longer verify
    if not state.get("clients"):
        status("Preparing Firefly API auth…")
        _run_php(data_dir, ["artisan", "passport:client", "--personal", "--no-interaction"])
        runtime["apiToken"] = None
    runtime["passportReady"] = True
    _write_json(firefly_runtime_file(data_dir), runtime)

    if not state.get("user_id") or not runtime.get("apiToken"):
        status("Creating Firefly desktop user…")
        parsed = _json_tail(
            _run_php(
                data_dir,
                ["-r", _bootstrap_user_script(runtime["email"], "Orb")],
                env={"ORB_FIREFLY_BOOTSTRAP_PASSWORD": runtime["password"]},
            )
        )
        runtime.update(userReady=True, userId=parsed["user_id"], groupId=parsed["group_id"], apiToken=parsed["token"])
        _write_json(firefly_runtime_file(data_dir), runtime)


def start_firefly(data_dir: Path) -> None:
    ensure_firefly_runtime(data_dir)
    root = firefly_root(data_dir)
    log = _log_path(data_dir, "firefly")
    proc = _spawn(
        "Firefly III",
        [str(php_binary(data_dir)), "artisan", "serve", "--host", "127.0.0.1", "--port", str(PORTS["firefly"])],
        cwd=firefly_app_dir(data_dir),
        env={"APP_URL": _local(PORTS["firefly"]), "HOME": str(root), "USERPROFILE": str(root)},
        log=log,
    )
    wait_http(_local(PORTS["firefly"]), 120, proc, log)


# ── Multimodal prep (optional, never blocks the window) ──────────────────────


def start_multimodal_prep(models_dir: Path, data_dir: Path) -> None:
    names = ("qwen3-asr-1.7b", "qwen3-asr-1.7b-hf", "qwen3-asr-0.6b", "qwen3-asr-0.6b-hf")
    if not any((models_dir / n).exists() for n in names):
        status("Multimodal models not installed yet — in-process load deferred")
        return
    status("Preparing in-process transcription / Marlin…")
    _spawn(
        "Multimodal prep",
        [
            sys.executable,
            "-c",
            "from app.services.multimodal_services import ensure_multimodal_services; "
            "import json; print(json.dumps(ensure_multimodal_services(install_deps=True)))",
        ],
        cwd=BACKEND_DIR,
        env={"ORB_MODELS_DIR": str(models_dir), "PYTHONPATH": str(BACKEND_DIR)},
        log=_log_path(data_dir, "multimodal"),
    )


def _exit_with_parent() -> None:
    """Shut down when the shell that spawned us is gone, however it went (crash,
    kill -9): a re-parented process means the window is no longer there."""
    parent = os.getppid()
    if parent <= 1:
        return
    while os.getppid() == parent:
        time.sleep(2)
    status("Shell process gone — shutting down")
    stop_sidecars()
    os._exit(0)  # no window left to serve; skip uvicorn's graceful drain


# ── Entry points ─────────────────────────────────────────────────────────────


def prefetch_firefly(dest: Path) -> None:
    """Build-time: download PHP + Firefly into a temp dir and copy them to dest."""
    with tempfile.TemporaryDirectory(prefix="orb-firefly-seed-") as tmp:
        data_dir = Path(tmp)
        print(f"Preparing Firefly seed from {FIREFLY_VERSION}…")
        ensure_php_runtime(data_dir, allow_bundled=False)
        ensure_firefly_app(data_dir, allow_bundled=False)
        _rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(firefly_root(data_dir) / "php", dest / "php", symlinks=True)
        shutil.copytree(firefly_app_dir(data_dir), dest / "app", symlinks=True)
    print(f"Firefly seed written to {dest}")


def main() -> int:
    global _STATUS_FILE  # noqa: PLW0603
    args = sys.argv[1:]
    if args[:1] == ["prefetch-firefly"]:
        prefetch_firefly(Path(args[1]).resolve())
        return 0

    data_dir = resolve_data_dir()
    models_dir = resolve_models_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _STATUS_FILE = data_dir / "boot-status.json"

    # On-demand packages (torch, transformers, mlx for transcription) install
    # into Python's user site under DATA_DIR, never into the app bundle. The
    # interpreter only picks that directory up at start, so fix the env and
    # re-exec once; the pid, process group and parent stay the same.
    user_base = data_dir / "python-user"
    if os.environ.get("PYTHONUSERBASE") != str(user_base):
        os.environ["PYTHONUSERBASE"] = str(user_base)
        major, minor = sys.version_info[:2]
        site_dir = user_base / (f"Python{major}{minor}" if WIN else f"lib/python{major}.{minor}") / "site-packages"
        site_dir.mkdir(parents=True, exist_ok=True)
        os.execv(sys.executable, [sys.executable, "-m", "app.desktop_runtime", *sys.argv[1:]])

    # Desktop defaults the shell used to pass in; env still overrides.
    for key, value in {
        "LLM_PROVIDER": "local",
        "EMBEDDING_PROVIDER": "local",
        # GGUF context / generation (one model loaded at a time). No default
        # output cap: the API sizes max_tokens per call from the context left.
        "ORB_LLAMA_N_CTX": "16384",
        "ORB_LLAMA_SWA_FULL": "true",
        "ORB_LLAMA_REPEAT_PENALTY": "1.12",
        "ORB_LLAMA_PROMPT_RESERVE": "4096",
        "ORB_EMBED_N_CTX": "8192",
        "ORB_RERANK_N_CTX": "8192",
    }.items():
        os.environ.setdefault(key, value)

    # The API's settings are read once at import, so every sidecar address is
    # fixed here before anything imports app.core.config.
    master_key = resolve_meili_master_key(data_dir)
    os.environ.update(
        QDRANT_HOST="127.0.0.1",
        QDRANT_PORT=str(PORTS["qdrant"]),
        MEILI_HOST="127.0.0.1",
        MEILI_PORT=str(PORTS["meili"]),
        MEILI_MASTER_KEY=master_key,
        FIREFLY_BASE_URL=_local(PORTS["firefly"]),
        FIREFLY_RUNTIME_FILE=str(firefly_runtime_file(data_dir)),
    )

    try:
        # A previous force-quit leaves orphaned sidecars on our ports.
        if free_ports(list(PORTS.values())):
            time.sleep(0.9)  # give SIGTERM a moment before we bind

        # The API comes up first so the window opens at once; search, finance and
        # multimodal join behind it (their clients reconnect on use).
        def boot_sidecars() -> None:
            try:
                start_search_engines(data_dir, master_key)
                start_firefly(data_dir)
                start_multimodal_prep(models_dir, data_dir)
                status("Ready")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                status(f"Local services failed to start: {exc}")

        threading.Thread(target=boot_sidecars, name="sidecars", daemon=True).start()
        threading.Thread(target=_exit_with_parent, name="parent-watch", daemon=True).start()
        status("Starting API…")
        import uvicorn

        # uvicorn re-raises the SIGTERM/SIGINT it captured once the server has
        # shut down, which would kill us before `finally` runs — so the restored
        # handler is ours, and it takes the sidecars down first.
        def on_signal(signum, _frame):
            stop_sidecars()
            sys.exit(128 + signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, on_signal)
        uvicorn.run("app.main:app", host="127.0.0.1", port=PORTS["api"])
        return 0
    except Exception as exc:  # pylint: disable=broad-exception-caught
        status(f"Startup failed: {exc}")
        raise
    finally:
        stop_sidecars()


if __name__ == "__main__":
    sys.exit(main())
