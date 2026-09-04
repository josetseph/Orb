#!/usr/bin/env node
/**
 * Bundle portable Python + backend app into desktop/resources/backend.
 * Uses python-build-standalone (https://github.com/astral-sh/python-build-standalone).
 */
const fs = require("fs");
const path = require("path");
const https = require("https");
const { execFileSync, spawnSync } = require("child_process");
const { writeStamp, SOURCE_ROOTS } = require("./source-stamp");

const repoRoot = path.resolve(__dirname, "..", "..");
const backendSrc = path.join(repoRoot, "backend");
const outBackend = path.join(__dirname, "..", "resources", "backend");
const tmpDir = path.join(__dirname, "..", "resources", ".tmp");

const PYTHON_RELEASE = process.env.ORB_PYTHON_RELEASE || "20250317";
const PYTHON_VERSION = process.env.ORB_PYTHON_VERSION || "3.12.9";

function pythonAsset() {
  const tag = `${PYTHON_VERSION}+${PYTHON_RELEASE}`;
  const base = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}`;
  if (process.platform === "darwin") {
    const arch = process.arch === "arm64" ? "aarch64" : "x86_64";
    const name = `cpython-${tag}-${arch}-apple-darwin-install_only.tar.gz`;
    return { url: `${base}/${name}`, archive: name };
  }
  if (process.platform === "win32") {
    const name = `cpython-${tag}-x86_64-pc-windows-msvc-install_only.tar.gz`;
    return { url: `${base}/${name}`, archive: name };
  }
  const arch = process.arch === "arm64" ? "aarch64" : "x86_64";
  const gnu = arch === "aarch64" ? "aarch64-unknown-linux-gnu" : "x86_64-unknown-linux-gnu";
  const name = `cpython-${tag}-${gnu}-install_only.tar.gz`;
  return { url: `${base}/${name}`, archive: name };
}

function download(url, dest) {
  // Defer createWriteStream until HTTP 200 so redirects cannot race unlink.
  return new Promise((resolve, reject) => {
    https
      .get(url, { timeout: 300000 }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume();
          const next = new URL(res.headers.location, url).toString();
          download(next, dest).then(resolve).catch(reject);
          return;
        }
        if (res.statusCode !== 200) {
          res.resume();
          reject(new Error(`Download failed ${res.statusCode}: ${url}`));
          return;
        }
        const file = fs.createWriteStream(dest);
        res.pipe(file);
        file.on("finish", () => file.close(() => resolve(dest)));
        file.on("error", reject);
      })
      .on("error", reject);
  });
}

function rmrf(p) {
  fs.rmSync(p, { recursive: true, force: true });
}

function copyDir(src, dest, filter) {
  fs.mkdirSync(dest, { recursive: true });
  for (const name of fs.readdirSync(src)) {
    if (filter && !filter(name)) continue;
    fs.cpSync(path.join(src, name), path.join(dest, name), { recursive: true });
  }
}

function resolvePythonExe(pyRoot) {
  const candidates = [
    path.join(pyRoot, "python.exe"),
    path.join(pyRoot, "bin", "python3"),
    path.join(pyRoot, "bin", "python"),
    path.join(pyRoot, "Scripts", "python.exe"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  throw new Error(`Python executable not found under ${pyRoot}`);
}

function run(python, args, opts = {}) {
  const r = spawnSync(python, args, {
    stdio: "inherit",
    ...opts,
  });
  if (r.status !== 0) {
    throw new Error(`Command failed: ${python} ${args.join(" ")}`);
  }
}

function copyBackendSources() {
  // Remove first: a rename or deletion in backend/app must not leave a stale
  // module behind in the bundle.
  rmrf(path.join(outBackend, "app"));
  copyDir(path.join(backendSrc, "app"), path.join(outBackend, "app"));
  const stamp = writeStamp(outBackend, SOURCE_ROOTS.backend);
  console.log("  backend source stamp", stamp.hash.slice(0, 16));
}

/**
 * Can we keep the interpreter that is already there?
 *
 * Re-downloading Python and rebuilding llama-cpp-python takes 10-20 minutes, so
 * a source-only change used to tempt people into running individual stages by
 * hand — which is exactly how trees drift out of sync. Reuse a bundle whose
 * imports still pass; force a full rebuild with ORB_REBUILD_PYTHON=1.
 */
function existingPythonIsUsable() {
  if (process.env.ORB_REBUILD_PYTHON === "1") return false;
  const pyRoot = path.join(outBackend, "python");
  if (!fs.existsSync(pyRoot)) return false;
  try {
    const python = resolvePythonExe(pyRoot);
    validateImports(python);
    return true;
  } catch (_) {
    return false;
  }
}

function llamaInstallEnv() {
  if (process.platform === "darwin" && process.arch === "arm64") {
    return {
      ...process.env,
      CMAKE_ARGS: "-DGGML_METAL=on",
    };
  }
  return { ...process.env };
}

function validateImports(python) {
  const script = `
import importlib
mods = ["uvicorn", "fastapi", "kuzu", "llama_cpp", "fitz", "numpy", "av", "greenlet"]
failed = []
for m in mods:
    try:
        importlib.import_module(m)
        print("ok", m)
    except Exception as e:
        failed.append((m, str(e)))
        print("FAIL", m, e)
# SQLAlchemy async requires greenlet at runtime (SQLite desktop path)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=NullPool)
print("ok sqlalchemy_async")
# numpy 2.x ships required modules under numpy/_core/tests — pruning that
# tree breaks scipy → sklearn → transformers (Florence/Whisper imports).
try:
    import numpy._core.tests._natype  # noqa: F401
    print("ok numpy._core.tests")
except Exception as e:
    failed.append(("numpy._core.tests", str(e)))
    print("FAIL numpy._core.tests", e)
if failed:
    raise SystemExit(1)
print("All critical imports passed")
`;
  run(python, ["-c", script], { cwd: outBackend });
}

function extractArchive(archivePath, destDir, stripComponents = 0) {
  fs.mkdirSync(destDir, { recursive: true });
  try {
    const args = ["-xzf", archivePath, "-C", destDir];
    if (stripComponents > 0) {
      args.push(`--strip-components=${stripComponents}`);
    }
    execFileSync("tar", args, { stdio: "inherit" });
  } catch (err) {
    if (process.platform !== "win32") throw err;
    console.warn("tar extract failed, trying PowerShell Expand-Archive…");
    const extractTo = path.join(tmpDir, "py-extract-win");
    rmrf(extractTo);
    fs.mkdirSync(extractTo, { recursive: true });
    execFileSync(
      "powershell",
      [
        "-Command",
        `Expand-Archive -Path '${archivePath}' -DestinationPath '${extractTo}' -Force`,
      ],
      { stdio: "inherit" },
    );
    // python-build-standalone windows may still be .tar.gz — if Expand fails, rethrow
    const entries = fs.readdirSync(extractTo);
    if (!entries.length) throw err;
    for (const name of entries) {
      fs.cpSync(path.join(extractTo, name), path.join(destDir, name), {
        recursive: true,
      });
    }
  }
}

function pruneBackendTree() {
  // Never strip tests/ under site-packages: numpy 2.x requires
  // numpy/_core/tests/_natype.py at import time (pulled in via scipy/sklearn
  // when transformers loads GenerationMixin → Florence/Whisper fail otherwise).
  const isSitePackages = (p) =>
    p.replace(/\\/g, "/").includes("/site-packages/");

  const walk = (dir, depth = 0) => {
    if (depth > 12 || !fs.existsSync(dir)) return;
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (_) {
      return;
    }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === "__pycache__" || e.name === ".pytest_cache") {
          rmrf(p);
          continue;
        }
        if (
          (e.name === "tests" || e.name === "test") &&
          !isSitePackages(p)
        ) {
          rmrf(p);
          continue;
        }
        walk(p, depth + 1);
      } else if (e.name.endsWith(".pyc") || e.name.endsWith(".pyo")) {
        try {
          fs.unlinkSync(p);
        } catch (_) {
          /* ignore */
        }
      }
    }
  };
  walk(outBackend);
}

async function main() {
  if (!fs.existsSync(path.join(backendSrc, "requirements.txt"))) {
    throw new Error(`Missing ${backendSrc}/requirements.txt`);
  }

  if (existingPythonIsUsable()) {
    console.log("Existing Python bundle passes its import check — reusing it.");
    console.log("(set ORB_REBUILD_PYTHON=1 to force a full reinstall)");
    copyBackendSources();
    pruneBackendTree();
    console.log("Backend sources refreshed at", outBackend);
    return;
  }

  fs.mkdirSync(tmpDir, { recursive: true });
  const asset = pythonAsset();
  const archivePath = path.join(tmpDir, asset.archive);

  console.log(`Downloading Python ${PYTHON_VERSION} (${PYTHON_RELEASE})…`);
  console.log(asset.url);
  await download(asset.url, archivePath);

  rmrf(outBackend);
  fs.mkdirSync(outBackend, { recursive: true });
  const pyRoot = path.join(outBackend, "python");
  fs.mkdirSync(pyRoot, { recursive: true });
  extractArchive(archivePath, pyRoot, 1);

  copyBackendSources();
  const python = resolvePythonExe(pyRoot);
  const installEnv = llamaInstallEnv();

  console.log("Installing Python dependencies (this may take several minutes)…");
  run(python, ["-m", "pip", "install", "--upgrade", "pip"], { cwd: outBackend });
  // Single install with platform CMAKE_ARGS (Metal on macOS arm64) — avoids double llama build
  run(
    python,
    ["-m", "pip", "install", "-r", path.join(backendSrc, "requirements.txt")],
    { cwd: outBackend, env: installEnv },
  );
  // SQLAlchemy async requires greenlet; pin explicitly so a partial/cached install can't omit it.
  run(python, ["-m", "pip", "install", "--force-reinstall", "greenlet>=3.0.0"], {
    cwd: outBackend,
  });

  console.log("Validating bundled imports…");
  validateImports(python);

  console.log("Pruning Python cache/test trees…");
  pruneBackendTree();

  fs.rmSync(archivePath, { force: true });
  console.log("Backend bundle ready at", outBackend);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
