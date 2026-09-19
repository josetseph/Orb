#!/usr/bin/env python3
"""Build the Orb desktop app. Runs with the system Python; Node is only used to
build the React UI in frontend/.

    python3 build.py prepare    # bundle Python + backend, build UI, seed Firefly
    python3 build.py check      # preflight: trees exist, imports pass, stamps fresh
    python3 build.py dist       # check, then `cargo tauri build` (extra args pass through)
    python3 build.py python | frontend | firefly     # one stage

Resources land in desktop/resources/{backend,frontend,firefly}, which
src-tauri/tauri.conf.json bundles. Each tree carries a stamp of the sources it
was built from so packaging refuses to ship a stale tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RES = HERE / "resources"
TMP = RES / ".tmp"
OUT_BACKEND, OUT_FRONTEND, OUT_FIREFLY = RES / "backend", RES / "frontend", RES / "firefly"
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
ARM = os.uname().machine in ("arm64", "aarch64") if not WIN else False

PY_RELEASE = os.environ.get("ORB_PYTHON_RELEASE", "20250317")
PY_VERSION = os.environ.get("ORB_PYTHON_VERSION", "3.12.9")

STAMP = ".orb-source-stamp.json"
SKIP_DIRS = {"node_modules", "dist", "__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
SOURCE_ROOTS = {
    "backend": [("backend/app", ROOT / "backend" / "app")],
    "frontend": [
        ("frontend/src", ROOT / "frontend" / "src"),
        ("frontend/public", ROOT / "frontend" / "public"),
        ("frontend/vite.config.ts", ROOT / "frontend" / "vite.config.ts"),
        ("frontend/index.html", ROOT / "frontend" / "index.html"),
    ],
}
# Modules the packaged API cannot run without; a pip install that died half way
# leaves a valid-looking interpreter with an empty site-packages.
CRITICAL_IMPORTS = ["uvicorn", "fastapi", "pydantic", "sqlalchemy", "aiosqlite", "kuzu", "qdrant_client", "meilisearch", "llama_cpp", "fitz", "numpy", "av", "greenlet", "keyring"]


def run(cmd: list, **kw) -> None:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = -10

    def hook(blocks: int, size: int, total: int) -> None:
        nonlocal last
        if total > 0:
            pct = min(100, blocks * size * 100 // total)
            if pct >= last + 10:
                last = pct
                print(f"  {pct}%", flush=True)

    urllib.request.urlretrieve(url, dest, hook)


# ── Source stamps ────────────────────────────────────────────────────────────


def hash_sources(roots: list[tuple[str, Path]]) -> str:
    h = hashlib.sha256()
    for label, root in roots:
        if not root.exists():
            h.update(f"{label}:missing\n".encode())
        elif root.is_file():
            h.update(f"{label}\0".encode() + root.read_bytes() + b"\0")
        else:
            for file in sorted(p for p in root.rglob("*") if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts)):
                h.update(f"{label}/{file.relative_to(root).as_posix()}\0".encode() + file.read_bytes() + b"\0")
    return h.hexdigest()


def write_stamp(out: Path, roots: list[tuple[str, Path]]) -> str:
    out.mkdir(parents=True, exist_ok=True)
    digest = hash_sources(roots)
    (out / STAMP).write_text(json.dumps({"hash": digest, "builtAt": datetime.now(timezone.utc).isoformat()}, indent=2))
    print(f"  source stamp {digest[:16]}")
    return digest


def stale_reason(out: Path, roots: list[tuple[str, Path]], label: str) -> str | None:
    try:
        stamp = json.loads((out / STAMP).read_text())
    except (OSError, ValueError):
        return f"{label} has no build stamp — it predates this check or was built by hand."
    if stamp.get("hash") != hash_sources(roots):
        return f"{label} was built from different sources (built {stamp.get('builtAt')}); sources have changed since."
    return None


# ── Python + backend ─────────────────────────────────────────────────────────


def bundled_python() -> Path:
    root = OUT_BACKEND / "python"
    for candidate in (root / "python.exe", root / "bin" / "python3", root / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Python executable not found under {root}")


def python_asset() -> tuple[str, str]:
    tag = f"{PY_VERSION}+{PY_RELEASE}"
    if MAC:
        triple = "aarch64-apple-darwin" if ARM else "x86_64-apple-darwin"
    elif WIN:
        triple = "x86_64-pc-windows-msvc"
    else:
        triple = "aarch64-unknown-linux-gnu" if ARM else "x86_64-unknown-linux-gnu"
    name = f"cpython-{tag}-{triple}-install_only.tar.gz"
    return f"https://github.com/astral-sh/python-build-standalone/releases/download/{PY_RELEASE}/{name}", name


def validate_imports(python: Path) -> None:
    script = f"""
import importlib, sys
failed = []
for m in {CRITICAL_IMPORTS!r}:
    try:
        importlib.import_module(m)
    except Exception as exc:
        failed.append(f"{{m}}: {{exc}}")
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=NullPool)
# numpy 2.x needs numpy/_core/tests at import time (scipy -> sklearn -> transformers).
try:
    import numpy._core.tests._natype  # noqa: F401
except Exception as exc:
    failed.append(f"numpy._core.tests: {{exc}}")
if failed:
    print("\\n".join(failed)); sys.exit(1)
print("All critical imports passed")
"""
    run([python, "-c", script], cwd=OUT_BACKEND)


def copy_backend_sources() -> None:
    # Remove first: a rename in backend/app must not leave a stale module behind.
    rmtree(OUT_BACKEND / "app")
    shutil.copytree(ROOT / "backend" / "app", OUT_BACKEND / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_stamp(OUT_BACKEND, SOURCE_ROOTS["backend"])


def prune_backend() -> None:
    """Drop caches and test trees — except under site-packages, where numpy needs its."""
    for path in sorted(OUT_BACKEND.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.exists():
            continue
        in_site = "site-packages" in path.parts
        if path.is_dir() and (path.name in ("__pycache__", ".pytest_cache") or (path.name in ("tests", "test") and not in_site)):
            rmtree(path)
        elif path.is_file() and path.suffix in (".pyc", ".pyo"):
            path.unlink(missing_ok=True)


def bundle_python() -> None:
    """Portable CPython (python-build-standalone) + pip deps + backend/app.

    Re-downloading Python and rebuilding llama-cpp-python takes 10–20 minutes, so
    an interpreter whose imports still pass is reused; ORB_REBUILD_PYTHON=1 forces it.
    """
    reuse = os.environ.get("ORB_REBUILD_PYTHON") != "1"
    if reuse:
        try:
            validate_imports(bundled_python())
            print("Existing Python bundle passes its import check — reusing it.")
            copy_backend_sources()
            prune_backend()
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    url, name = python_asset()
    archive = TMP / name
    TMP.mkdir(parents=True, exist_ok=True)
    download(url, archive)
    if not archive.exists():
        raise SystemExit(f"Download did not produce {archive}")
    print(f"  archive {archive.stat().st_size // 1048576} MB", flush=True)
    rmtree(OUT_BACKEND)
    if not archive.exists():
        raise SystemExit(f"{archive} vanished while removing {OUT_BACKEND}")
    py_root = OUT_BACKEND / "python"
    py_root.mkdir(parents=True)
    with tarfile.open(archive) as tf:  # strip the leading "python/" component
        members = [m for m in tf.getmembers() if "/" in m.name]
        for m in members:
            m.name = m.name.split("/", 1)[1]
        tf.extractall(py_root, members=members, filter="tar")
    copy_backend_sources()
    python = bundled_python()
    env = {**os.environ, **({"CMAKE_ARGS": "-DGGML_METAL=on"} if MAC and ARM else {})}
    print("Installing Python dependencies (this may take several minutes)…")
    run([python, "-m", "pip", "install", "--upgrade", "pip"], cwd=OUT_BACKEND)
    run([python, "-m", "pip", "install", "-r", ROOT / "backend" / "requirements.txt"], cwd=OUT_BACKEND, env=env)
    # SQLAlchemy async needs greenlet; a partial/cached install used to omit it.
    run([python, "-m", "pip", "install", "--force-reinstall", "greenlet>=3.0.0"], cwd=OUT_BACKEND)
    validate_imports(python)
    prune_backend()
    archive.unlink(missing_ok=True)
    print("Backend bundle ready at", OUT_BACKEND)


# ── Frontend ─────────────────────────────────────────────────────────────────


def build_frontend() -> None:
    npm = "npm.cmd" if WIN else "npm"
    # A user .npmrc with allow-scripts leaks into nested npm calls as
    # npm_config_* and breaks project-scoped installs; strip those keys.
    env = {k: v for k, v in os.environ.items() if k not in ("npm_config_allow_scripts", "npm_config_allowscripts")}
    frontend = ROOT / "frontend"
    run([npm, "ci"], cwd=frontend, env=env, shell=WIN)
    run([npm, "run", "build"], cwd=frontend, env=env, shell=WIN)
    dist = frontend / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("Missing frontend/dist/index.html — vite build failed")
    rmtree(OUT_FRONTEND)
    shutil.copytree(dist, OUT_FRONTEND)
    write_stamp(OUT_FRONTEND, SOURCE_ROOTS["frontend"])
    print("Frontend ready at", OUT_FRONTEND)


# ── Firefly seed ─────────────────────────────────────────────────────────────


def prefetch_firefly() -> None:
    """PHP + Firefly III app, fetched by the same code that installs them at runtime."""
    if os.environ.get("ORB_REBUILD_FIREFLY") != "1" and (OUT_FIREFLY / "app" / ".orb-firefly-version").exists() and (OUT_FIREFLY / "php" / ".orb-php-runtime").exists():
        print("Firefly seed already present — reusing it (ORB_REBUILD_FIREFLY=1 to refetch).")
        return
    run(
        [bundled_python(), "-m", "app.desktop_runtime", "prefetch-firefly", OUT_FIREFLY],
        cwd=OUT_BACKEND,
        env={**os.environ, "PYTHONPATH": str(OUT_BACKEND)},
    )


# ── Preflight + packaging ────────────────────────────────────────────────────


def check() -> None:
    failures = []
    for path in (OUT_BACKEND / "app", OUT_FRONTEND / "index.html", OUT_FIREFLY / "app"):
        if not path.exists():
            failures.append(f"Missing resource: {path}")
    try:
        validate_imports(bundled_python())
    except FileNotFoundError as exc:
        failures.append(str(exc))
    except subprocess.CalledProcessError:
        failures.append("The bundled Python cannot import the backend's dependencies — the pip step did not finish.")
    for tree, roots in SOURCE_ROOTS.items():
        out = OUT_BACKEND if tree == "backend" else OUT_FRONTEND
        if out.exists() and (reason := stale_reason(out, roots, f"resources/{tree}")):
            failures.append(f"{reason}\n      Packaging now would ship a stale {tree}.")
    if failures:
        print("Packaged resources are not ready:\n")
        for f in failures:
            print(f" - {f}")
        print("\nRun: python3 build.py prepare")
        raise SystemExit(1)
    print("Packaged resources OK (trees, Python imports, stamps)")


def dist(extra: list[str]) -> None:
    check()
    # The resource map lives here, not in tauri.conf.json: tauri-build would
    # otherwise copy these multi-GB trees into target/debug on every dev build.
    resources = {f"../resources/{name}": name for name in ("backend", "frontend", "firefly")}
    config = json.dumps({"bundle": {"resources": resources}})
    run(["cargo", "tauri", "build", "--config", config, *extra], cwd=HERE / "src-tauri")


STAGES = {"python": bundle_python, "frontend": build_frontend, "firefly": prefetch_firefly, "check": check}


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else "prepare"
    if stage == "prepare":
        for name in ("python", "frontend", "firefly"):
            print(f"\n=== {name} ===\n")
            STAGES[name]()
        print("\n=== prepare complete === next: python3 build.py dist")
    elif stage == "dist":
        dist(argv[1:])
    elif stage in STAGES:
        STAGES[stage]()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
