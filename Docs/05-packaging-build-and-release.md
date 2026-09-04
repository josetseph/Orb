# Packaging, Build and Release

**What this covers.** How the Orb desktop installers are produced: the `prepare-dist` pipeline that assembles a portable CPython + backend, a Next.js standalone frontend, a portable Node runtime and a Firefly III/PHP seed under `desktop/resources/`; the electron-builder configuration that turns those into `.dmg`/`.zip`/`.exe`/`.AppImage` artifacts; the GitHub Actions release workflow (`desktop-v*` tags); code signing / notarization hooks and their current (unsigned) state; version bumping; how to test the packaged layout without an installer; and the contributor-only Docker Compose stack. Runtime behaviour of the shell (boot sequence, supervisor, path resolution, first-run downloads) is documented in [Desktop shell](04-desktop-shell.md) and is only referenced here where it constrains the build.

**Related docs:** [Overview](01-overview.md) · [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Desktop shell](04-desktop-shell.md) · [Backend core and configuration](06-backend-core-and-configuration.md) · [Local models and inference](12-local-models-and-inference.md) · [Finance / Firefly](17-finance-firefly.md) · [Frontend architecture](18-frontend-architecture.md) · [Configuration reference](21-configuration-reference.md) · [Data directory layout](22-data-directory-layout.md) · [Decisions and constraints](26-decisions-and-constraints.md) · [Development guide](27-development-guide.md) · [Development history](25-development-history.md)

---

## 1. Responsibilities and boundaries

**Owned by this area**

- `desktop/scripts/*.js` — the build scripts (`prepare-dist` and its four stages, the `predist` gate, the two optional prefetchers, the notarize hook).
- The `build` block in `desktop/package.json` (electron-builder configuration) and the `scripts` block that wires the pipeline.
- `desktop/build/entitlements.mac.plist`, `desktop/build/icon.png`.
- `.github/workflows/desktop-release.yml` — the four-job release matrix.
- `frontend/next.config.ts` as far as it affects the standalone build (`output: "standalone"`, rewrite targets baked at build time).
- The contributor Docker stack: `docker-compose.yml`, `backend/Dockerfile`, `backend/.dockerignore`, `frontend/Dockerfile`.
- Version strings in `desktop/package.json`, `frontend/package.json`, `backend/app/main.py`.

**Not owned here**

- What the shell does with the bundled resources at runtime (`paths.js`, `supervisor.js`, `firefly-runtime.js`, `download-binaries.js`) — see [Desktop shell](04-desktop-shell.md) §7, §8, §10, §11. This doc only states the on-disk contract the build must satisfy for those modules.
- Python dependency semantics (which package does what) — see [Backend core](06-backend-core-and-configuration.md), [Local models](12-local-models-and-inference.md), [Multimedia enrichment](11-multimedia-enrichment.md).
- GGUF model downloads, Qdrant/Meilisearch binaries: these are **never bundled**; they are fetched at first run into `DATA_DIR` (see §5.4 and [Data directory layout](22-data-directory-layout.md)).

---

## 2. Files

| Path | Purpose | Key entry points / exports |
|---|---|---|
| `desktop/package.json` | npm scripts (`prepare-dist`, `predist`, `dist*`, `pack`, prefetchers) and the electron-builder `build` block | `scripts`, `build`, `version` |
| `desktop/scripts/prepare-dist.js` | Orchestrator: runs the four bundling stages sequentially with `execFileSync(process.execPath, …)` | `main()` |
| `desktop/scripts/bundle-python.js` | Downloads python-build-standalone, extracts to `resources/backend/python`, copies `backend/app`, pip-installs `requirements.txt`, validates imports, prunes | `pythonAsset`, `llamaInstallEnv`, `validateImports`, `pruneBackendTree`, `extractArchive` |
| `desktop/scripts/build-frontend.js` | `npm ci` + `next build` in `frontend/`, assembles standalone output in `resources/frontend`, renames `node_modules` → `node_deps`, writes `run-server.js` | `writeRunServer`, `findServerJs` |
| `desktop/scripts/bundle-node.js` | Downloads official Node tarball/zip to `resources/node`, prunes headers/docs/npm/corepack and helper symlinks | `nodeAsset` |
| `desktop/scripts/prefetch-firefly.js` | Runs `ensurePhpRuntime` + `ensureFireflyApp` into a temp dir and copies the result to `resources/firefly/{php,app}` | `main()` |
| `desktop/scripts/check-resources.js` | `predist` gate: fails if any required bundled path is missing | — |
| `desktop/scripts/prefetch-binaries.js` | Optional: pre-download Qdrant + Meilisearch into `./data/bin` (or `ORB_DATA_DIR`). Not part of `prepare-dist`, not bundled | — |
| `desktop/scripts/notarize.js` | electron-builder `afterSign` hook; no-op unless `APPLE_*` env set | `exports.default(context)` |
| `desktop/build/entitlements.mac.plist` | Hardened-runtime entitlements (JIT, unsigned executable memory, library validation off, network client/server) | — |
| `desktop/build/icon.png` | App icon for all three OS targets | — |
| `desktop/PACKAGING.md` | Human quick-start for local builds (partly stale; see §12) | — |
| `desktop/README.md` | Dev vs installer quick-start | — |
| `desktop/binaries/README.md` | Notes on Qdrant/Meili versions and llama-cpp env knobs (no code lives there) | — |
| `.github/workflows/desktop-release.yml` | Release CI: 4 jobs (mac arm64, mac x64, windows x64, linux x64) | — |
| `frontend/next.config.ts` | `output: "standalone"`, rewrites baked from `API_PROXY_TARGET`/`FILES_PROXY_TARGET` at build time | — |
| `frontend/package.json` | `next build`; version `0.2.0` | — |
| `frontend/Dockerfile` | Contributor-only multi-stage Node 20 alpine image (standalone output, port 3000) | — |
| `backend/Dockerfile` | Contributor-only `python:3.11-slim` image running uvicorn on 8000 | — |
| `backend/.dockerignore` | Excludes venvs, models, logs, tests, `.env*` from the image context | — |
| `docker-compose.yml` | Contributor stack: postgres, qdrant, meilisearch, backend, frontend, optional `rustfs` (`legacy-s3` profile) | — |
| `.gitignore` | Ignores `/desktop/resources`, `/desktop/dist`, `/frontend/.next`, `.tmp/`, etc. | — |

Everything under `desktop/resources/` and `desktop/dist/` is **build output** and gitignored; never commit it and never document its internals as source.

---

## 3. Build pipeline at a glance

Two commands produce an installer, always run from `desktop/`:

```bash
cd desktop
npm ci                 # electron 43.2.0, electron-builder ^26.0.12, @electron/notarize ^2.5.0, electron-updater ^6.8.9
npm run prepare-dist   # assembles desktop/resources/{backend,frontend,node,firefly}   (10–20 min, network required)
npm run dist:mac       # or dist:win / dist:linux / dist / pack  → desktop/dist/
```

`npm run dist*` triggers the npm `predist` lifecycle script (`scripts/check-resources.js`) first; it refuses to start electron-builder if `prepare-dist` output is missing (§4.6). CI runs `npx electron-builder …` directly and therefore **bypasses** the `predist` gate (it relies on the preceding `prepare-dist` step having failed the job instead).

```mermaid
flowchart TD
    A[npm run prepare-dist<br/>scripts/prepare-dist.js] --> B[1. bundle-python.js]
    B --> B1[download python-build-standalone<br/>cpython-3.12.9+20250317-&lt;triple&gt;-install_only.tar.gz]
    B1 --> B2[tar --strip-components=1 → resources/backend/python]
    B2 --> B3[copy backend/app → resources/backend/app]
    B3 --> B4[pip install --upgrade pip<br/>pip install -r backend/requirements.txt<br/>CMAKE_ARGS=-DGGML_METAL=on on darwin/arm64]
    B4 --> B5[pip install --force-reinstall greenlet>=3.0.0]
    B5 --> B6[validateImports: uvicorn fastapi kuzu llama_cpp fitz numpy av greenlet<br/>+ sqlalchemy async engine + numpy._core.tests._natype]
    B6 --> B7[pruneBackendTree: __pycache__, .pytest_cache, *.pyc/*.pyo,<br/>tests/ outside site-packages]
    B7 --> C[2. build-frontend.js]
    C --> C1[npm ci in frontend/]
    C1 --> C2[npm run build with NEXT_PUBLIC_API_URL=/api/v1<br/>API_PROXY_TARGET=FILES_PROXY_TARGET=http://127.0.0.1:17401]
    C2 --> C3[copy .next/standalone → resources/frontend<br/>copy .next/static → resources/frontend/.next/static<br/>copy public → resources/frontend/public]
    C3 --> C4[rename node_modules → node_deps<br/>write run-server.js]
    C4 --> D[3. bundle-node.js]
    D --> D1[download nodejs.org/dist/v24.4.1/node-v24.4.1-&lt;os&gt;-&lt;arch&gt;.tar.gz|.zip|.tar.xz]
    D1 --> D2[extract → resources/node<br/>drop include/ share/ docs, npm/npx/corepack links + lib/node_modules/npm,corepack]
    D2 --> E[4. prefetch-firefly.js]
    E --> E1[ensurePhpRuntime + ensureFireflyApp into os.tmpdir/orb-firefly-seed-*]
    E1 --> E2[copy php → resources/firefly/php<br/>copy app → resources/firefly/app]
    E2 --> F{npm run dist*}
    F --> G[predist: check-resources.js]
    G -->|missing| X[exit 1: Run npm run prepare-dist]
    G -->|ok| H[electron-builder]
    H --> H1[files → app.asar]
    H --> H2[extraResources → Contents/Resources/&#123;backend,frontend,node,firefly&#125;]
    H --> H3[mac: dmg+zip, hardenedRuntime, entitlements<br/>win: nsis+portable<br/>linux: AppImage]
    H3 --> I[afterSign: scripts/notarize.js<br/>no-op unless APPLE_* set]
    I --> J[desktop/dist/Orb-&lt;version&gt;-&lt;arch&gt;-&lt;os&gt;.&lt;ext&gt;]
```

Sequence matters only in one place: `check-resources.js` requires all four resource trees, so every stage must complete. The stages do not read each other's output (build-frontend reads `desktop/ports.js`, not `resources/`), so re-running a single stage (`npm run bundle-node`, `npm run build-frontend`, …) is safe and is the normal way to iterate on one part.

Each stage is fully **host-native**: the Python interpreter, wheels, Node binary and PHP runtime downloaded all match `process.platform` / `process.arch` of the machine running the script. There is no cross-compilation and no `--arch` awareness in `prepare-dist` — see §11 for why this is a hard constraint.

---

## 4. `prepare-dist` stage by stage

### 4.1 Orchestrator — `desktop/scripts/prepare-dist.js`

`run(name, script)` executes each stage with `execFileSync(process.execPath, [scriptPath], { stdio: "inherit", cwd: desktop/ })`. `process.execPath` is the Node that launched `prepare-dist`, so the stages run under the same Node as the npm invocation (Node 24 in CI). Any non-zero exit throws and aborts the remaining stages; there is no retry, no skip-if-present, and no parallelism. Order:

1. `bundle-python.js` — "Bundle Python backend"
2. `build-frontend.js` — "Build frontend standalone"
3. `bundle-node.js` — "Bundle Node runtime"
4. `prefetch-firefly.js` — "Prefetch Firefly seed"

It prints `Run: npm run dist (or dist:mac / dist:win)` on success. Nothing in this script touches `resources/.tmp` cleanup: each stage owns its temp files under `desktop/resources/.tmp/` (and `prefetch-firefly` uses `os.tmpdir()`).

### 4.2 Stage 1 — `bundle-python.js` (portable CPython + backend)

**Inputs:** `backend/requirements.txt` (must exist, else throws `Missing …/requirements.txt`), `backend/app/`, network.
**Output:** `desktop/resources/backend/` containing `python/` and `app/`.

Constants:

| Constant | Env override | Default |
|---|---|---|
| `PYTHON_RELEASE` | `ORB_PYTHON_RELEASE` | `20250317` |
| `PYTHON_VERSION` | `ORB_PYTHON_VERSION` | `3.12.9` |

`pythonAsset()` builds the python-build-standalone download URL:

```
https://github.com/astral-sh/python-build-standalone/releases/download/<RELEASE>/cpython-<VERSION>+<RELEASE>-<triple>-install_only.tar.gz
```

| Host | `<triple>` |
|---|---|
| darwin arm64 | `aarch64-apple-darwin` |
| darwin x64 | `x86_64-apple-darwin` |
| win32 (any arch) | `x86_64-pc-windows-msvc` — **Windows always downloads x86_64**; there is no arm64 Windows Python |
| linux arm64 | `aarch64-unknown-linux-gnu` |
| linux x64 | `x86_64-unknown-linux-gnu` |

The `install_only` flavour is a relocatable, pre-built tree (`bin/python3.12`, `lib/python3.12/…`, `include/`, `share/`) with no `pip` bootstrap step needed — it ships `pip` already.

Steps in `main()`:

1. `mkdir -p desktop/resources/.tmp`; `download(url, .tmp/<archive>)`. `download()` follows 3xx redirects recursively (GitHub → `objects.githubusercontent.com`) and only opens the write stream after a `200` (see §14 for why). Timeout 300 s per request.
2. `rm -rf resources/backend`; `mkdir resources/backend/python`; `extractArchive(archive, python, stripComponents=1)`. The archive's single top-level folder is `python/`, so `--strip-components=1` places `bin/`, `lib/` directly under `resources/backend/python`. On Windows, if `tar` fails it falls back to `powershell Expand-Archive` into `.tmp/py-extract-win` and copies the entries over (comment in code admits Expand-Archive cannot open `.tar.gz`, so this fallback is effectively dead on the current asset; Windows runners ship bsdtar which handles `.tar.gz`).
3. `copyBackendSources()` — `copyDir(backend/app → resources/backend/app)` with `fs.cpSync(recursive)`; **only `backend/app` is copied**. Not copied: `backend/tests`, `backend/requirements*.txt`, `backend/.env*`, `backend/scripts`, `backend/models`. (Alembic copying was removed in `fbcafe7` when Alembic was dropped.) Because this is a plain copy, any `__pycache__` present in the checkout is copied too and later removed by `pruneBackendTree`.
4. `resolvePythonExe(pyRoot)` — first existing of `python.exe`, `bin/python3`, `bin/python`, `Scripts/python.exe`.
5. `pip install --upgrade pip` (cwd `resources/backend`).
6. `pip install -r backend/requirements.txt` with `env = llamaInstallEnv()`:
   - darwin + arm64 → `CMAKE_ARGS="-DGGML_METAL=on"` so `llama-cpp-python>=0.3.0` compiles a Metal-enabled `libllama` (requires `cmake` + Xcode CLT on the builder; CI does `brew install cmake || true`).
   - every other host → environment unchanged → llama-cpp-python builds a **CPU-only** wheel from sdist (Linux needs `cmake build-essential`; Windows needs MSVC build tools — present on `windows-latest`). No `-DGGML_CUDA` / Vulkan flags are set anywhere in the build; CUDA users must reinstall inside the bundled Python themselves (the runtime auto-detect in [Local models](12-local-models-and-inference.md) only picks a backend that the compiled wheel supports).
   - The comment "Single install with platform CMAKE_ARGS — avoids double llama build" records that an earlier variant installed requirements and then force-reinstalled llama-cpp-python a second time.
7. `pip install --force-reinstall greenlet>=3.0.0` — SQLAlchemy's asyncio extension imports `greenlet` lazily; a cached/partial wheel set once shipped without it and the packaged API crashed at first DB call. `requirements.txt` also pins `greenlet>=3.0.0`; the explicit reinstall is belt-and-braces.
8. `validateImports(python)` runs an inline script with `cwd = resources/backend` and exits 1 on failure. It imports `uvicorn, fastapi, kuzu, llama_cpp, fitz, numpy, av, greenlet`, builds a `sqlite+aiosqlite:///:memory:` async engine with `NullPool`, and imports `numpy._core.tests._natype`. The last check exists because an old `pruneBackendTree` deleted `numpy/_core/tests`, which broke `scipy → sklearn → transformers` imports and therefore Florence/Whisper in shipped builds (`desktop/scripts/hotfix-multimodal-ingest.sh`, deleted in `fbcafe7`, patched installed apps by re-downloading the numpy wheel).
9. `pruneBackendTree()` walks `resources/backend` (depth ≤ 12) and removes: any directory named `__pycache__` or `.pytest_cache`; any directory named `tests` or `test` **whose path does not contain `/site-packages/`**; any `*.pyc` / `*.pyo` file. Result: the backend's own `app/**/tests` would be pruned, but every third-party package keeps its test tree (numpy's is load-bearing). The pruning is size-only; nothing else is stripped (no `include/`, no `share/`, no `.dist-info`).
10. `rm .tmp/<archive>`. The `.tmp` directory itself is left in place.

**What is deliberately not installed at build time:** `backend/requirements-multimodal.txt` (torch, transformers ≥ 5.7, accelerate, einops, safetensors, librosa, pydub, timm, qwen-vl-utils, av). `requirements.txt` says "installed on demand by Setup / supervisor — keeps base image smaller". At runtime the supervisor's `startMultimodalServices()` runs `ensure_multimodal_services(install_deps=True, start_marlin=True)` (`backend/app/services/multimodal_services.py`), which calls `sys.executable -m pip install --upgrade <_MULTIMODAL_PIP>` **into the bundled Python's site-packages inside the installed app**. Consequences: the first multimodal use needs network and several GB of disk inside `Contents/Resources/backend/python`; on macOS this mutates a (would-be) signed bundle — one of the reasons notarization is still "Stage 6" (§9). The `_MULTIMODAL_PIP` list in code and the file `requirements-multimodal.txt` are maintained separately and can drift (the code list adds `Pillow>=12.0.0`, the file does not).

Approximate size of the result on macOS arm64: ~565 MB.

### 4.3 Stage 2 — `build-frontend.js` (Next.js standalone)

**Inputs:** `frontend/` source, `desktop/ports.js` (for `apiUrl()`), network for `npm ci`.
**Output:** `desktop/resources/frontend/`.

1. `npm ci` in `frontend/` (via `execFileSync(npm, ["ci"], { shell: true })`; `npm.cmd` on Windows).
2. `npm run build` (= `next build`) with env:
   - `NEXT_PUBLIC_API_URL=/api/v1` — inlined into the client bundle; the browser calls **same-origin** `/api/v1/*` on port 17400.
   - `API_PROXY_TARGET=http://127.0.0.1:17401` and `FILES_PROXY_TARGET=http://127.0.0.1:17401` — read by `frontend/next.config.ts` at **build time** to produce the rewrite table (`/api/v1/:path*`, `/vault-files/:path*`, `/health`, `/files/:path*` → API). Note the stale comment in `next.config.ts` ("Desktop prepare-dist sets API_PROXY_TARGET=http://127.0.0.1:8000"); the actual value comes from `ports.js` and is 17401 (or whatever `ORB_API_PORT` was at build time — **the API port is baked into the standalone rewrite config**; overriding `ORB_API_PORT` at runtime only helps because the supervisor also passes `API_PROXY_TARGET` to `run-server.js` and Next re-reads `next.config` at server start. Both must agree.)
   - `NODE_ENV=production`.
   - `next.config.ts` also sets `output: "standalone"`, `reactCompiler: true`, `images.unoptimized: true` (no sharp at runtime), `experimental.proxyClientMaxBodySize: "512mb"` (desktop uploads go through the rewrite proxy; the 10 MB default truncated multi-MB note attachments).
3. `findServerJs(frontend/.next/standalone)` — accepts `server.js` either directly under `standalone/` or under `standalone/frontend/` (Next nests output under the package dir when it detects a monorepo root via lockfile tracing). If nested, the nested dir is copied and the hoisted `standalone/node_modules` is copied alongside. If missing → throws "check next.config output: standalone".
4. `rm -rf resources/frontend`, then copies: standalone tree → `resources/frontend/`; `frontend/.next/static` → `resources/frontend/.next/static`; `frontend/public` → `resources/frontend/public` (Next's standalone output does **not** include these two, by design).
5. Sanity checks: `server.js` and `node_modules/next` must exist.
6. **Rename `node_modules` → `node_deps`** and write `run-server.js`:

```js
// resources/frontend/run-server.js (generated)
const path = require("path");
const Module = require("module");
const deps = path.join(__dirname, "node_deps");
process.env.NODE_PATH = [deps, process.env.NODE_PATH || ""].filter(Boolean).join(path.delimiter);
Module._initPaths();
require("./server.js");
```

   Why: electron-builder silently excludes any folder named `node_modules` when copying `extraResources` (its default file filter), so a bundle containing `frontend/node_modules` produced installers whose UI could not `require("next")`. Renaming to `node_deps` sidesteps the filter; `run-server.js` prepends that directory to `NODE_PATH` and calls `Module._initPaths()` so Node's global-fallback resolution finds `next`, `react`, `react-dom`, etc. Standalone `server.js` and Next's traced chunks use plain `require("next/…")`, which resolves through `NODE_PATH` once the normal `node_modules` walk fails. The supervisor always launches the UI as `node run-server.js` with `cwd = <resources>/frontend` (`supervisor.js#startFrontend`), and `check-resources.js` insists on `frontend/node_deps/next`.
7. Final check `node_deps/next` exists.

The generated tree contains `server.js`, `run-server.js`, `package.json`, `.next/` (server chunks + `static/`), `public/`, `node_deps/`. ~49 MB.

Package versions that matter for reproducibility: `next 16.2.12`, `react 19.2.6`, `babel-plugin-react-compiler 1.0.0`, `typescript ^6`, `tailwindcss ^4`. `frontend/package.json` `overrides` pin `postcss ^8.5.25` and `sharp ^0.35.3`.

### 4.4 Stage 3 — `bundle-node.js` (portable Node runtime)

**Output:** `desktop/resources/node/`. `NODE_VERSION = ORB_NODE_VERSION || "24.4.1"`.

`nodeAsset()`:

| Host | URL | Archive handling |
|---|---|---|
| darwin | `https://nodejs.org/dist/v<V>/node-v<V>-darwin-{arm64,x64}.tar.gz` | `tar -xzf` |
| win32 | `https://nodejs.org/dist/v<V>/node-v<V>-win-{arm64,x64}.zip` | `powershell Expand-Archive` |
| linux | `https://nodejs.org/dist/v<V>/node-v<V>-linux-{arm64,x64}.tar.xz` | `tar -xf` |

Steps: download to `.tmp/`; `rm -rf resources/node`; extract into `.tmp/node-extract/`; `cpSync(<innerDir> → resources/node)`; delete `include/`, `share/`, `CHANGELOG.md`, `README.md`, `LICENSE`; delete `bin/{npm,npx,corepack}` (or `./{npm,npx,corepack}.cmd` on Windows) and `lib/node_modules/{npm,corepack}`; delete the archive and extract dir.

The helper-link deletion (added in `45fcca5`) is the fix for the broken v0.1.0 macOS builds: the official tarball ships `bin/npm`, `bin/npx`, `bin/corepack` as **relative symlinks** into `lib/node_modules/…`. After `fs.cpSync` (which by default dereferences symlinks — or, depending on Node, preserves them pointing at the now-deleted extract dir), the copies inside the app pointed at absolute CI paths such as `/Users/runner/work/Orb/Orb/desktop/resources/.tmp/node-extract/…`. Dangling symlinks inside `Contents/Resources` make Gatekeeper report the app as "damaged" and confuse `xattr`/`codesign`. Only `bin/node` is needed at runtime (`paths.js#getNodeBinary` → `<resources>/node/bin/node` or `node.exe`). Result ~111 MB (the `lib/node_modules` directory is left but empty of npm/corepack).

### 4.5 Stage 4 — `prefetch-firefly.js` (Firefly III + PHP seed)

**Output:** `desktop/resources/firefly/{php,app}`. This stage reuses the runtime bootstrap code from `desktop/firefly-runtime.js` rather than having its own downloader:

1. `tempRoot = mkdtemp(os.tmpdir()/orb-firefly-seed-)`.
2. `ensurePhpRuntime(tempRoot)` — downloads the NativePHP static PHP 8.5 build for the host: `https://raw.githubusercontent.com/NativePHP/php-bin/refs/tags/<PHP_BIN_VERSION>/bin/{mac/arm64|mac/x64|linux/x64|win/x64}/php-8.5.zip` (`PHP_BIN_VERSION = ORB_PHP_BIN_VERSION || "1.2.0"`), extracts it (`unzip` → `python3 zipfile` fallback on Unix; `powershell Expand-Archive` → bsdtar fallback on Windows), normalizes mac dylib paths and `php.ini`, writes the marker `php/.orb-php-runtime` containing `nativephp:1.2.0:php-8.5`, `chmod 755`, strips quarantine. Because `bundledFireflyRoot()` also looks at `desktop/resources/firefly` in dev, a *previous* seed on the build machine can be reused as the source here (no network) if its markers match.
3. `ensureFireflyApp(tempRoot)` — downloads `https://github.com/firefly-iii/firefly-iii/releases/download/<FIREFLY_VERSION>/FireflyIII-<FIREFLY_VERSION>.tar.gz` (`FIREFLY_VERSION = ORB_FIREFLY_VERSION || "v6.6.6"`), extracts the full Laravel app (vendor included), writes `app/.orb-firefly-version` = `v6.6.6`.
4. `rm -rf resources/firefly`; copies `<tempRoot>/firefly/php` → `resources/firefly/php` and `<tempRoot>/firefly/app` → `resources/firefly/app`; `finally` removes `tempRoot`.

Only the *seed* is bundled: no `.env`, no SQLite DB, no Passport keys, no `runtime.json` — those are generated in `DATA_DIR/firefly/` on first launch (`ensureFireflyRuntime`, see [Desktop shell](04-desktop-shell.md) §11 and [Finance / Firefly](17-finance-firefly.md)). At runtime `ensurePhpRuntime`/`ensureFireflyApp` prefer the bundled seed **only if the marker strings match the running shell's constants** (`PHP_RUNTIME_ID`, `FIREFLY_VERSION`); a mismatch (e.g. you bumped `ORB_FIREFLY_VERSION` in `firefly-runtime.js` but rebuilt without re-running `prefetch-firefly`) silently falls back to a network download at user first-run. ~295 MB.

### 4.6 The `predist` gate — `check-resources.js`

npm runs `predist` (and `predist:mac`, `predist:win` — note there is **no** `predist:linux`, so `npm run dist:linux` is ungated) before the corresponding `dist*` script. The check requires all of:

```
resources/backend/app
resources/backend/python/{python.exe | bin/python3 | bin/python}
resources/frontend/server.js
resources/frontend/run-server.js
resources/frontend/node_deps/next
resources/firefly/app
resources/node/{node.exe | bin/node}
```

It then runs two checks that existence alone cannot cover — both were added after each failure shipped a broken 0.2.0 build on 2026-09-04:

1. **The bundled Python must import the backend's dependencies.** A `pip install` that dies part way leaves a valid-looking `backend/python/bin/python3` beside an empty `site-packages`; the path check passed, electron-builder produced a 272 MB `.dmg`, and the installed app hung on the splash for 120 s before "Could not start local services". The gate now imports `uvicorn`, `fastapi`, `pydantic`, `sqlalchemy`, `aiosqlite`, `kuzu`, `qdrant_client`, `meilisearch`, `llama_cpp` and `greenlet` with that interpreter (120 s timeout, `cwd = resources/backend`).
2. **Every local `require()` from the shell must be covered by `build.files`.** That list is an allow-list, so a new top-level module nobody adds to it is silently dropped and the packaged app dies immediately with `Cannot find module './credentials'` — which is exactly what happened. The gate walks the relative requires reachable from `main.js` and `preload.js` (transitively) and matches each against the `files` globs with a small glob→RegExp translation supporting `*`, `**/*` and `!` negation. `package.json` is exempt because electron-builder always bundles it.

Missing entries are listed and the process exits 1 with `Run: npm run prepare-dist`. It still does not verify `resources/firefly/php`, marker contents, or that the Python tree matches the target arch.

> CI runs `npx electron-builder` directly and therefore bypasses this gate entirely (§3). Both failure modes above would ship unnoticed from CI; the protection only applies to `npm run dist*` locally.

### 4.7 Optional — `prefetch-binaries.js`

`npm run prefetch-binaries` calls `ensureBinaries(ORB_DATA_DIR || desktop/data)` from `download-binaries.js` to pre-download Qdrant (`ORB_QDRANT_VERSION`, default `v1.18.2`) and Meilisearch (`ORB_MEILI_VERSION`, default `v1.49.0`) into `<dataDir>/bin/<triple>/`. This is **not** part of `prepare-dist`, its output is **not** under `resources/`, and electron-builder does not pick it up (`binaries/**/*` in `files` only matches `desktop/binaries/README.md`). Its only practical use is warming a dev data dir or a CI cache; end users get the same download on first launch (splash progress). GGUF models are never prefetched by any script — the Python setup API downloads them into `MODELS_DIR/gguf/`.

---

## 5. electron-builder configuration (`desktop/package.json` → `build`)

electron-builder `^26.0.12` (installed 26.15.3) with Electron `43.2.0` (exact pin — see §14 for why the caret was dropped). Field by field:

| Field | Value | Effect / notes |
|---|---|---|
| `appId` | `com.orb.app` | macOS bundle identifier (`CFBundleIdentifier`), Windows AppUserModelID, Linux desktop id. Must match the `appBundleId` hard-coded in `scripts/notarize.js`. |
| `productName` | `Orb` | `Orb.app`, installer titles, `${productName}` macro. |
| `executableName` | `Orb` | `Contents/MacOS/Orb`, `Orb.exe`, AppImage inner binary. |
| `directories.output` | `dist` | Artifacts land in `desktop/dist/` (gitignored). |
| `files` | `*.js`, `!*.test.js`, `splash.html`, `wizard.html`, `assets/**/*`, `binaries/**/*`, `build/icon.png`, `build/entitlements.mac.plist`, `scripts/notarize.js` | What goes into `app.asar`. Was an explicit per-file allow-list until 2026-09-04, when `credentials.js` shipped missing and the app crashed at startup with `Cannot find module`; a pattern plus a test-file exclusion means new modules are included by default. `predist` also verifies that every local `require()` is covered (§4.6). `package.json` and production `dependencies` (`electron-updater` and its tree) are always added by electron-builder. `scripts/notarize.js` and the plist are included so the hook can be resolved; they are harmless at runtime. `binaries/**/*` only matches the README. Anything not listed (`scripts/bundle-*.js`, `PACKAGING.md`, `resources/`, `dist/`, `node_modules/electron*`) stays out of the asar. |
| `extraResources` | four entries `{ from: "resources/<x>", to: "<x>", filter: ["**/*"] }` for `backend`, `frontend`, `node`, `firefly` | Copied **outside** the asar to `<resourcesPath>/<x>`. The filter `**/*` is what still drops any `node_modules` folder (see `app-builder-lib/out/fileMatcher.js`, which injects `!**/node_modules/**` for extraResources) — hence `node_deps` (§4.3). Bundled Python and Node **must** be outside the asar: they are executed with `spawn`, and native `.so`/`.dylib` files cannot be loaded from inside an asar. |
| `asar` | `true` | Shell JS is packed; `__dirname` inside `main.js` is `…/Resources/app.asar`. `paths.js` therefore derives `getResourcesRoot()` from `process.resourcesPath`, never from `__dirname`. |
| `artifactName` | `${productName}-${version}-${arch}-${os}.${ext}` | Produces `Orb-0.2.0-arm64-mac.dmg`, `Orb-0.2.0-x64-mac.zip`, `Orb-0.2.0-x64-win.exe`, `Orb-0.2.0-x86_64-linux.AppImage` (electron-builder renders `${arch}` as `x86_64` for AppImage). Added in `45fcca5` so CI upload globs (`*arm64*.dmg`, `*x64*.zip`) can separate per-arch outputs. **Side effect:** the `nsis` and `portable` Windows targets both expand to the same filename, so one overwrites the other in `dist/` (see §12). |
| `publish` | `[{ provider: "github", owner: "josetseph", repo: "Orb" }]` | Consumed by `electron-updater` (`app-update.yml` is generated into the bundle) and by electron-builder's publish step. CI passes `-p never`, so nothing is uploaded automatically. |
| `mac.category` | `public.app-category.productivity` | `LSApplicationCategoryType`. |
| `mac.icon` | `build/icon.png` | electron-builder converts the PNG to `.icns`. |
| `mac.target` | `["dmg", "zip"]` | No arch list here; the arch comes from the CLI (`--arm64` / `--x64`). Before `45fcca5` the config listed `arch: [arm64, x64]` per target, which made a single job try to build both arches from one host-native `resources/` tree (impossible — see §11). The `zip` target is what `electron-updater` needs for macOS updates. |
| `mac.hardenedRuntime` | `true` | Required for notarization; enforces the entitlements below when signed. Has no effect on an unsigned build. |
| `mac.gatekeeperAssess` | `false` | Skips `spctl --assess` after signing (would fail for unsigned/ad-hoc builds). |
| `mac.entitlements` / `entitlementsInherit` | `build/entitlements.mac.plist` | Same plist for the main binary and all child helpers. Contents: `com.apple.security.cs.allow-jit`, `cs.allow-unsigned-executable-memory`, `cs.disable-library-validation` (needed so the bundled Python can `dlopen` pip-installed, differently-signed `.so` files such as `libllama`, Kuzu, PyMuPDF), `network.client`, `network.server` (localhost listeners on 17400/17401/17412/17433/17470). |
| `afterSign` | `scripts/notarize.js` | Runs after codesign on every platform; the hook returns immediately unless `electronPlatformName === "darwin"` and the three `APPLE_*` vars are set (§9). |
| `win.icon` | `build/icon.png` | Converted to `.ico`. |
| `win.target` | `["nsis", "portable"]` | NSIS installer + single-file portable exe. Both x64 in CI (`--x64`). |
| `nsis.oneClick` | `false` | Classic wizard with directory page. |
| `nsis.allowToChangeInstallationDirectory` | `true` | User-selectable install dir. Per-user vs per-machine is electron-builder's default (per-user). |
| `linux.icon` | `build/icon.png` | — |
| `linux.target` | `["AppImage"]` | No `.deb`/`.rpm`. |

No `compression`, `npmRebuild`, `nodeGypRebuild`, `asarUnpack`, `protocols`, or `fileAssociations` are configured. Nothing in the shell uses native Node modules, so `npmRebuild` defaults are irrelevant. There is no `electron-builder.yml`; the config lives entirely in `package.json`.

`npm run pack` (`electron-builder --dir`) produces an unpacked app directory under `dist/<os>-<arch>-unpacked/` (or `dist/mac-arm64/Orb.app`) without an installer — the fastest way to inspect the final layout.

---

## 6. Packaged app on-disk layout

Given the config above, an installed build looks like this (paths relative to the app):

### 6.1 macOS (`Orb.app`)

```
Orb.app/Contents/
├── MacOS/Orb                              # Electron binary (executableName)
├── Frameworks/…                           # Electron Framework, helpers (Orb Helper (GPU).app, …)
├── Info.plist                             # CFBundleIdentifier com.orb.app, LSApplicationCategoryType productivity
└── Resources/                             # = process.resourcesPath
    ├── app.asar                           # main.js, supervisor.js, paths.js, ports.js, preload.js, *.html,
    │                                      # assets/, binaries/README.md, build/icon.png, scripts/notarize.js,
    │                                      # package.json, node_modules/electron-updater/…
    ├── app-update.yml                     # generated from build.publish (github/josetseph/Orb)
    ├── icon.icns
    ├── backend/
    │   ├── app/                           # copy of backend/app (FastAPI package)
    │   └── python/
    │       ├── bin/python3, python3.12, pip, uvicorn, …
    │       ├── lib/python3.12/site-packages/   # requirements.txt wheels; multimodal deps added here at runtime
    │       ├── include/                   # not pruned
    │       └── share/
    ├── frontend/
    │   ├── server.js                      # Next standalone entry
    │   ├── run-server.js                  # NODE_PATH shim → node_deps
    │   ├── package.json
    │   ├── .next/{server,static,…}
    │   ├── public/
    │   └── node_deps/                     # renamed node_modules
    ├── node/
    │   ├── bin/node                       # only binary kept
    │   └── lib/node_modules/              # npm + corepack removed
    └── firefly/
        ├── php/{php/…, .orb-php-runtime}  # NativePHP static PHP 8.5, marker "nativephp:1.2.0:php-8.5"
        └── app/{artisan, vendor, …, .orb-firefly-version}   # Firefly III v6.6.6 source tree, marker "v6.6.6"
```

### 6.2 Windows (NSIS install dir, default `%LOCALAPPDATA%\Programs\Orb\`, or the portable exe's extraction dir)

```
Orb.exe
resources\
  app.asar
  app-update.yml
  backend\app\…             backend\python\python.exe, Lib\site-packages\…
  frontend\…                (run-server.js, node_deps\)
  node\node.exe             (npm.cmd/npx.cmd/corepack.cmd removed; node_modules\npm removed)
  firefly\php\…             firefly\app\…
```

`check-resources.js`, `paths.js#getPythonBinary` and `getNodeBinary` all expect `backend\python\python.exe` and `node\node.exe` at the top level of those trees (python-build-standalone's Windows `install_only` archive has `python.exe` at its root; the Node zip has `node.exe` at its root).

### 6.3 Linux (AppImage, mounted at `$APPDIR`)

```
$APPDIR/
├── orb (AppRun → executableName)
├── resources/   (same four trees; backend/python/bin/python3, node/bin/node)
└── usr/share/icons/…
```

### 6.4 Runtime contract summary

The shell finds things exactly here (see [Desktop shell](04-desktop-shell.md) §7.1 for the full resolution order):

| Need | Path under resources root | Producer |
|---|---|---|
| Python | `backend/python/bin/python3` \| `bin/python` \| `python.exe` | `bundle-python.js` |
| Backend cwd / `PYTHONPATH` | `backend/` (contains `app/`) | `bundle-python.js` |
| Frontend cwd + entry | `frontend/run-server.js` (requires `server.js`, `node_deps/`) | `build-frontend.js` |
| Node | `node/bin/node` \| `node/node.exe` | `bundle-node.js` |
| Firefly seed | `firefly/php` (+ marker), `firefly/app` (+ marker) | `prefetch-firefly.js` |

Everything writable lives outside the bundle: `~/Library/Application Support/Orb/{paths.json,data/,models/}` (`%APPDATA%\Orb\`, `~/.config/Orb/`) — see [Data directory layout](22-data-directory-layout.md). The **one exception** is the multimodal pip install into the bundled `site-packages` (§4.2), which writes into the app bundle itself.

### 6.5 Testing the packaged layout without an installer

```bash
cd desktop
npm run prepare-dist           # or the individual stage you changed
ORB_RESOURCES=./resources npm start
```

`paths.js#getResourcesRoot()` returns `ORB_RESOURCES` (resolved) when not packaged; since `resources/backend/python` exists, `isPackagedLayout()` is true and the supervisor uses the bundled Python, `node run-server.js`, and the Firefly seed — the same code paths as the installed app, minus asar and codesigning. Even without the env var, `getResourcesRoot()` auto-detects `desktop/resources` when `resources/backend/python` exists, so after a `prepare-dist` a plain `npm start` **already** runs the packaged layout; set `ORB_FRONTEND_DEV=1` to force `next dev` for the UI while keeping the bundled backend, or delete/rename `desktop/resources` to return to full dev mode. `ORB_PACKAGED=1` additionally makes `isPackaged()` true for scripts run under plain `node` (outside Electron).

---

## 7. Environment variables consumed by the build and CI

| Variable | Read by | Default | Purpose |
|---|---|---|---|
| `ORB_PYTHON_RELEASE` | `bundle-python.js`; set in workflow `env` | `20250317` | python-build-standalone release tag (URL path segment and `+<release>` suffix in asset name). |
| `ORB_PYTHON_VERSION` | `bundle-python.js`; workflow `env` | `3.12.9` | CPython version in the asset name. Must exist in that release. |
| `ORB_NODE_VERSION` | `bundle-node.js`; workflow `env` | `24.4.1` | Portable Node version downloaded from nodejs.org. Independent of the Node running the build (`setup-node` `24`), but kept in the same major deliberately. |
| `ORB_QDRANT_VERSION` | `download-binaries.js` (via `prefetch-binaries.js` and at runtime) | `v1.18.2` | Qdrant release tag. Not used by `prepare-dist`. |
| `ORB_MEILI_VERSION` | `download-binaries.js` | `v1.49.0` | Meilisearch release tag. Not used by `prepare-dist`. |
| `ORB_FIREFLY_VERSION` | `firefly-runtime.js` (via `prefetch-firefly.js` and at runtime) | `v6.6.6` | Firefly III release tag; also written to `app/.orb-firefly-version`. Changing it requires re-running `prefetch-firefly` **and** shipping a shell with the same constant, else the seed is ignored. |
| `ORB_PHP_BIN_VERSION` | `firefly-runtime.js` | `1.2.0` | NativePHP `php-bin` tag; part of `PHP_RUNTIME_ID = nativephp:<v>:php-8.5` written to `php/.orb-php-runtime`. |
| `ORB_DATA_DIR` | `prefetch-binaries.js` | `desktop/data` | Target for optional Qdrant/Meili prefetch. |
| `ORB_API_PORT` (and `LIVEOS_API_PORT`) | `ports.js` → `build-frontend.js` | `17401` | Baked into the standalone rewrite table as `API_PROXY_TARGET`/`FILES_PROXY_TARGET`. |
| `ORB_UI_PORT`, `ORB_FIREFLY_PORT`, `ORB_QDRANT_PORT`, `ORB_MEILI_PORT` | `ports.js` | `17400`, `17412`, `17433`, `17470` | Not baked at build time; runtime only. Listed for completeness. |
| `NEXT_PUBLIC_API_URL` | set by `build-frontend.js` to `/api/v1` | — | Inlined into the client bundle. |
| `API_PROXY_TARGET`, `FILES_PROXY_TARGET` | set by `build-frontend.js`; read by `next.config.ts` | (`http://localhost:8700` dev / `http://backend:8000` prod, `http://localhost:9000` / `http://rustfs:9000` when unset) | Rewrite destinations. The unset defaults are the Docker-compose hostnames. |
| `NODE_ENV` | set to `production` by `build-frontend.js` | — | Also selects the non-development defaults in `next.config.ts`. |
| `CMAKE_ARGS` | set by `bundle-python.js` (`llamaInstallEnv`) | `-DGGML_METAL=on` on darwin/arm64 only | Passed to pip so `llama-cpp-python` builds with Metal. Any pre-existing `CMAKE_ARGS` in the caller's env is **overwritten** on darwin/arm64 and passed through unchanged elsewhere. |
| `CSC_IDENTITY_AUTO_DISCOVERY` | electron-builder; set to `"false"` in every CI job | (true) | Prevents electron-builder from searching the keychain for a Developer ID and failing/prompting; yields unsigned (macOS: ad-hoc-less) builds. |
| `CSC_LINK`, `CSC_KEY_PASSWORD` | electron-builder | unset | Certificate (file path / base64 / URL) and password for macOS Developer ID or Windows Authenticode signing. Not set anywhere in the repo or workflow. |
| `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | `scripts/notarize.js` | unset | All three required for notarization; otherwise the hook logs "Skipping notarization (APPLE_* env not set)". |
| `GH_TOKEN` / `GITHUB_TOKEN` | electron-builder publish | not used | Would be needed for `-p always`; CI uses `-p never`. |
| `ORB_ENABLE_UPDATER` (`LIVEOS_ENABLE_UPDATER`) | `main.js#setupAutoUpdater` (runtime) | unset | Opt-in gate for `electron-updater`; see §9.3. |
| `ORB_RESOURCES` (`LIVEOS_RESOURCES`), `ORB_PACKAGED` (`LIVEOS_PACKAGED`), `ORB_FRONTEND_DEV`, `ORB_PYTHON`, `ORB_NODE`, `ORB_ROOT` | `paths.js` (runtime) | unset | Packaged-layout testing overrides (§6.5). |

The workflow sets only the three `ORB_PYTHON_RELEASE` / `ORB_PYTHON_VERSION` / `ORB_NODE_VERSION` values (identical to the script defaults, so they act as a visible pin) plus `CSC_IDENTITY_AUTO_DISCOVERY=false` per build step. No repository secrets are referenced by the workflow.

---

## 8. Versioning and the release process

### 8.1 Version locations (bump checklist)

| Location | Current | Used for |
|---|---|---|
| `desktop/package.json` → `version` | `0.2.0` | electron-builder `${version}` macro (artifact names, `CFBundleShortVersionString`, NSIS version, `app-update.yml`), `app.getVersion()`. **This is the release version.** |
| `desktop/package-lock.json` → root `version` (two places) | `0.2.0` | Kept in sync by `npm install`/`npm version`; `npm ci` fails if `package.json` and lockfile disagree on the root package name/version. |
| `frontend/package.json` → `version` | `0.2.0` | Cosmetic; not surfaced in the UI. Bumped together with desktop for consistency (`e14dc67`). |
| `frontend/package-lock.json` → root `version` | `0.2.0` | Same as above. |
| `backend/app/main.py` → `FastAPI(title="Orb API", version="0.1.0")` | `0.1.0` | Reported at `/docs` and `/openapi.json` `info.version`. **Not bumped in `e14dc67` — currently lags the app version.** |
| `README.md` | mentions `v0.2.0+` | Human release notes / "avoid v0.1.0" warning. |
| git tag | `desktop-v0.2.0` | Triggers CI; the numeric part is not read by the build — the tag and `package.json` are kept in agreement by convention only. |

Recommended sequence: `cd desktop && npm version 0.x.y --no-git-tag-version` (updates both package files), the same in `frontend/`, edit `backend/app/main.py`, update README notes, commit, then tag `desktop-v0.x.y` on that commit and push the tag.

### 8.2 Release workflow — `.github/workflows/desktop-release.yml`

Triggers: `push` of tags matching `desktop-v*`, and manual `workflow_dispatch`. Global `env`: `ORB_PYTHON_RELEASE=20250317`, `ORB_PYTHON_VERSION=3.12.9`, `ORB_NODE_VERSION=24.4.1`.

Four independent jobs (no `needs`, no matrix strategy — each is spelled out), all `timeout-minutes: 120`:

| Job | Runner | Extra tooling | Build command | Uploaded artifact (name → paths) |
|---|---|---|---|---|
| `build-mac-arm64` | `macos-14` (Apple Silicon) | `brew install cmake \|\| true` | `npx electron-builder --mac dmg zip --arm64 -p never` | `orb-mac-arm64` → `desktop/dist/*arm64*.dmg`, `*arm64*.zip` |
| `build-mac-x64` | `macos-15-intel` (comment: `macos-13` is retired; this is GitHub's remaining x64 macOS runner) | `brew install cmake \|\| true` | `npx electron-builder --mac dmg zip --x64 -p never` | `orb-mac-x64` → `*x64*.dmg`, `*x64*.zip` |
| `build-windows` | `windows-latest` | none (MSVC + bsdtar + PowerShell present) | `npx electron-builder --win --x64 -p never` | `orb-windows-x64` → `*.exe` |
| `build-linux` | `ubuntu-latest` | `apt-get install -y cmake build-essential` | `npx electron-builder --linux AppImage --x64 -p never` | `orb-linux-x64` → `*.AppImage` |

Common steps in each job: `actions/checkout@v5` → `actions/setup-node@v5` (`node-version: "24"`, `cache: npm`, `cache-dependency-path: desktop/package-lock.json`) → `npm ci` in `desktop/` → `npm run prepare-dist` in `desktop/` → electron-builder with `CSC_IDENTITY_AUTO_DISCOVERY: "false"` → `actions/upload-artifact@v6` with `if-no-files-found: error`.

Notes:

- `-p never` disables electron-builder's GitHub publish even though a tag is being built. The workflow contains **no release-creation step**; the maintainer downloads the four workflow artifacts and attaches them to a manually created GitHub Release. This is why release asset lists (e.g. `Orb-0.2.0-arm64-mac.dmg`, `…-arm64-mac.zip`, `…-x64-mac.dmg`, `…-x64-mac.zip`, `…-x64-win.exe`, `…-x86_64-linux.AppImage`) are curated by hand.
- Running `npx electron-builder` directly (not `npm run dist:*`) skips the `predist` gate; the `prepare-dist` step failing is what protects the job.
- `--arm64` / `--x64` do not cross-build resources: they only tell electron-builder which Electron binary to fetch. The `resources/` tree was built by `prepare-dist` on the same runner and is host-native. The per-arch runner split is therefore mandatory, not an optimization.
- The per-arch upload globs (`*arm64*`, `*x64*`) plus `if-no-files-found: error` were added in `45fcca5` after a job uploaded an empty artifact silently.
- Setup-node's npm cache only covers `desktop/package-lock.json`; `frontend/` `npm ci` and pip downloads are uncached, which is the bulk of the 30–60 min per job. The most expensive step is compiling `llama-cpp-python` from source (no prebuilt wheel is used).
- Release tag history: `desktop-v0.1.0` → `2c3518a`, `desktop-v0.1.1` → `45fcca5`, `desktop-v0.2.0` → `02ac9d3` (annotated tag). The `desktop-v0.2.0` run first failed on the bump commit `e14dc67` (Firefly archive wiped mid-extract, fixed by `02ac9d3`), then the tag was re-pointed and re-run successfully.

### 8.3 Local release build

Identical to CI minus the runner: on the target OS/arch, `cd desktop && npm ci && npm run prepare-dist && npm run dist:mac` (or `dist:win`, `dist:linux`). The `dist` script without a suffix builds the current platform's default targets. macOS needs Xcode CLT and `cmake` (Metal llama build); Linux needs `cmake build-essential`; Windows needs Visual Studio Build Tools (C++ workload) for llama-cpp-python, PowerShell, and `tar`.

---

## 9. Signing, notarization and auto-update

### 9.1 Current state: unsigned

Every job sets `CSC_IDENTITY_AUTO_DISCOVERY=false` and no `CSC_LINK` / `APPLE_*` secrets exist. Consequences:

- macOS: the `.app` is not Developer-ID signed and not notarized. `hardenedRuntime`/entitlements are configured but inert. Downloaded DMGs carry `com.apple.quarantine`; Gatekeeper shows "Orb is damaged and can't be opened" (not merely "unidentified developer") because the bundle is unsigned. Documented workaround in `README.md`:

  ```bash
  xattr -cr /Applications/Orb.app
  open /Applications/Orb.app
  ```

  `xattr -cr` recursively clears all extended attributes (quarantine included) on the ~1.4 GB bundle.
- Windows: unsigned NSIS/portable exe → SmartScreen "Windows protected your PC" → "More info → Run anyway".
- Linux: AppImage needs `chmod +x`; no signing concept involved.

The v0.1.0 macOS builds were additionally broken **even after** `xattr -cr`: dangling absolute symlinks (`npm`, `npx`, `corepack`) under `Resources/node/bin` pointing into the CI runner's deleted extract dir made macOS treat the bundle as damaged. Fixed in `45fcca5` by deleting those links in `bundle-node.js`; `README.md` tells users to avoid v0.1.0 Mac builds for that reason.

### 9.2 Enabling signing (the "Stage 6" plan from `PACKAGING.md`)

- **macOS Developer ID**: provide `CSC_LINK` (`.p12` path/base64) and `CSC_KEY_PASSWORD`; remove/flip `CSC_IDENTITY_AUTO_DISCOVERY`. electron-builder signs with hardened runtime + `build/entitlements.mac.plist`. Every Mach-O inside `extraResources` (bundled `python3`, `node`, `php`, all `.so`/`.dylib` in site-packages) will also need to be signed for notarization to pass — electron-builder signs nested binaries it finds, but the runtime pip install of multimodal deps (§4.2) later drops **unsigned** `.so` files into the bundle. `disable-library-validation` lets the signed Python load them, but this is precisely the mutation notarized apps should not perform; moving the multimodal site-packages target out of the bundle (e.g. into `DATA_DIR`) is a prerequisite for a clean signed story.
- **Notarization**: set `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`. `scripts/notarize.js` (afterSign) then calls `@electron/notarize`'s `notarize({ appBundleId: "com.orb.app", appPath: <appOutDir>/<productFilename>.app, appleId, appleIdPassword, teamId })` — the notarytool flow. It runs once per arch build. Stapling is handled by electron-builder for DMG/ZIP after the hook succeeds.
- **Windows Authenticode**: `CSC_LINK` + `CSC_KEY_PASSWORD` on the Windows job (electron-builder signs `Orb.exe`, the NSIS installer and the portable exe). Alternatively an Azure Trusted Signing / `signtoolOptions` configuration; nothing is set up.
- Secrets would be added as GitHub Actions secrets and threaded into the `env:` of the build steps; the workflow currently references none.

### 9.3 Auto-update gating

`main.js#setupAutoUpdater()` returns early unless `app.isPackaged` **and** `ORB_ENABLE_UPDATER=1` (or `LIVEOS_ENABLE_UPDATER=1`). When enabled it sets `autoUpdater.autoDownload = false` and calls `checkForUpdatesAndNotify()`; failures are swallowed. Rationale in the code: "Opt-in only — unsigned builds must not hit GitHub Releases until Stage 6" — `electron-updater` refuses to install unsigned updates on macOS and would surface confusing errors. `build.publish` (github/josetseph/Orb) is what `electron-updater` would read from `app-update.yml`; for updates to work the GitHub Release must contain the `latest-mac.yml` / `latest.yml` / `latest-linux.yml` metadata files that electron-builder only generates when publishing (`-p always`/`onTag`) — the current manual upload of dmg/zip/exe alone is not sufficient for `electron-updater` even with the flag set.

---

## 10. Contributor Docker stack (not the product path)

`docker-compose.yml` header: *"Contributor / optional infra stack only. End users run the Orb desktop app (see desktop/) — not this compose file."* The root README repeats: do not wire Docker through the desktop shell. The desktop shell has zero Docker awareness; the stack exists for backend/frontend contributors who want Postgres or containerized dependencies.

| Service | Image / build | Host ports | Volumes | Notes |
|---|---|---|---|---|
| `postgres` | `postgres:17` | `15432:5432` | `./data/local_db/data` | DB `orb`, user `user`, password `password`; healthcheck `pg_isready`. Desktop uses SQLite instead (`DATABASE_BACKEND` default). |
| `rustfs` | `rustfs/rustfs:latest`, **profile `legacy-s3`** | `9000`, `9001` | `./data/rustfs` | S3-compatible object store for the legacy `STORAGE_BACKEND=s3` path; only started with `docker compose --profile legacy-s3 up`. Desktop uses `STORAGE_BACKEND=local`. |
| `qdrant` | `qdrant/qdrant:latest` | `6333`, `6334` | `./data/qdrant` | Unpinned tag (desktop pins `v1.18.2`). |
| `meilisearch` | `getmeili/meilisearch:v1.49` | `7700` | `./data/meilisearch` | `MEILI_MASTER_KEY=orb-dev-key`, `MEILI_ENV=development`. |
| `backend` | `./backend/Dockerfile` | `8700:8000` | `./backend:/app` (live code), `./data:/data` | `env_file: ./backend/.env` plus overrides: `QDRANT_HOST=qdrant`, `MEILI_HOST=meilisearch`, `MEILI_PORT=7700`, `MEILI_MASTER_KEY`, legacy `TYPESENSE_*` aliases, `DATABASE_BACKEND=postgres`, `STORAGE_BACKEND=local`, `FILES_URL=/files/orb-assets`, `INGESTION_PIPELINE_CONCURRENCY=1`, `MULTIMEDIA_CONCURRENCY=1`, three `DATABASE_*_URL=postgresql://user:password@postgres:5432/orb`. `depends_on` postgres healthy, qdrant started, meilisearch healthy. `extra_hosts: host.docker.internal`. |
| `frontend` | `./frontend/Dockerfile` | `3700:3000` | — | `NEXT_PUBLIC_API_URL=/api/v1`, `NEXT_PUBLIC_FILES_URL=/files/orb-assets`; rewrites fall back to `http://backend:8000` / `http://rustfs:9000` because `API_PROXY_TARGET`/`FILES_PROXY_TARGET` are unset in the container. |

A former one-shot `init` service (ran `scripts/init.sh`) was removed in `fbcafe7`; Neo4j was removed when graph storage moved to embedded Kuzu.

`backend/Dockerfile`: `python:3.11-slim` (note: desktop bundles **3.12.9**), apt `gcc g++ curl ffmpeg postgresql-client`, `pip install -r requirements.txt` with a BuildKit cache mount, `COPY . .`, `uvicorn app.main:app --host 0.0.0.0 --port 8000`. No `CMAKE_ARGS`, so llama-cpp-python is CPU-only; multimodal deps are not installed. `backend/.dockerignore` excludes `venv/`, `.venv/`, `models/`, `logs/`, caches, `tests/`, `.env*` (keeps `.env.example`), editor files. There is **no** `frontend/.dockerignore`, so `frontend/node_modules` and `.next` from the host are sent in the build context (they are overwritten in the `deps`/`builder` stages, but inflate context upload).

`frontend/Dockerfile`: three-stage `node:20-alpine` (`deps` → `builder` → `runner`), `NEXT_TELEMETRY_DISABLED=1`, copies `.next/standalone` + `.next/static` + `public`, runs as user `nextjs`, `PORT=3000`, `HOSTNAME=0.0.0.0`, `CMD node server.js`. Contrast with desktop: no `node_deps` rename is needed in Docker because nothing filters `node_modules` there; Docker builds with Node 20 while desktop bundles Node 24.

Port map for humans switching between the two worlds: desktop `17400/17401/17412/17433/17470` (UI/API/Firefly/Qdrant/Meili) vs compose `3700/8700/—/6333/7700` (+ `15432` Postgres, `9000/9001` RustFS). `frontend/package.json` `dev` script runs `next dev -p 3700` for the compose-era layout; the desktop supervisor overrides the port with `-- -p 17400`.

---

## 11. Invariants and locked decisions

1. **Never cross-compile native wheels.** `bundle-python.js` has no target-arch parameter; pip resolves wheels for the running interpreter, and `llama-cpp-python` is compiled from source with the host toolchain. Build macOS arm64 on Apple Silicon, macOS x64 on Intel, Windows on Windows, Linux on Linux. This is why the CI has four runners rather than one job with `--arm64 --x64`, and why `mac.target` no longer lists arches.
2. **Bundled Python and Node live in `extraResources`, never in `files`/asar.** They are spawned as processes and load native libraries; asar would break both.
3. **`node_deps`, not `node_modules`, under `resources/frontend`.** electron-builder's extraResources filter drops `node_modules`. Any change to the frontend bundle must keep `run-server.js` + `node_deps/next` intact; `check-resources.js` enforces it.
4. **Do not prune `tests/` inside `site-packages`.** `numpy._core.tests._natype` is imported at runtime by numpy 2.x; stripping it silently breaks transformers (Florence/Whisper) — enforced by `validateImports`.
5. **Delete Node helper symlinks (`npm`, `npx`, `corepack`).** Dangling/absolute symlinks in `Contents/Resources` produce "app is damaged" on macOS regardless of quarantine.
6. **Open download write streams only after HTTP 200** (all four downloaders: `bundle-python`, `bundle-node`, `download-binaries`, `firefly-runtime`). GitHub release URLs 302 to Azure; the old pattern (`createWriteStream` → on redirect `close(); unlinkSync(dest)`) raced the follow-up download and truncated archives (`02ac9d3`).
7. **Multimodal deps are runtime-installed, not bundled.** Keeps the base installer ~400–580 MB instead of several GB; the trade-off (bundle mutation, first-use network) is accepted for now.
8. **Firefly seed markers must match shell constants.** `firefly/php/.orb-php-runtime` == `PHP_RUNTIME_ID` and `firefly/app/.orb-firefly-version` == `FIREFLY_VERSION`, otherwise the seed is bypassed and the user downloads at first run.
9. **Same-origin API in the packaged UI.** `NEXT_PUBLIC_API_URL=/api/v1` is fixed at build time; the UI never talks to `17401` directly. Changing the API port means rebuilding the frontend *and* changing `ports.js`.
10. **CI never publishes** (`-p never`); releases are assembled manually. Auto-update stays opt-in (`ORB_ENABLE_UPDATER=1`) until builds are signed.
11. **Electron pinned exactly (`43.2.0`)**, not `^`. Introduced in `899ddb4` alongside the author field electron-builder requires for Linux (`author` → maintainer metadata in AppImage/deb).
12. **Docker is not a product path.** Nothing in `desktop/` may reference compose services or ports 8000/3000/8700/3700.

---

## 12. Failure modes, edge cases and troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing packaged resources. Run: npm run prepare-dist` | `predist` gate: one of the seven required paths absent | Run the missing stage (`npm run bundle-python`, `build-frontend`, `bundle-node`, `prefetch-firefly`). |
| `Missing …/backend/requirements.txt` | Running from a partial checkout | — |
| `Download failed 404: …python-build-standalone…` | `ORB_PYTHON_VERSION` not present in `ORB_PYTHON_RELEASE` | Pick a matching pair from the astral-sh releases page; update both script default and workflow `env`. |
| `Command failed: … pip install -r requirements.txt` (llama-cpp-python CMake error) | Missing `cmake`/compiler; on macOS also missing Xcode CLT | `brew install cmake`, `xcode-select --install`; Linux `apt install cmake build-essential`; Windows VS Build Tools C++. |
| `FAIL greenlet` / `FAIL numpy._core.tests` in `validateImports` | Partial wheel install or over-eager pruning | Re-run `bundle-python` (it wipes `resources/backend` first). Never add `site-packages` test dirs to the prune list. |
| `Missing .next/standalone/server.js` | `output: "standalone"` removed from `next.config.ts`, or Next changed nesting | Restore config; `findServerJs` already handles `standalone/frontend/server.js`. |
| `Frontend bundle missing node_modules/next` | Next hoisted deps differently (monorepo detection) | Check `.next/standalone` shape; extend `findServerJs`/copy logic. |
| Packaged UI fails with `Cannot find module 'next'` | `node_deps` missing or supervisor launched `server.js` directly | Ensure `run-server.js` is the entry and `node_deps/` shipped. |
| macOS "Orb is damaged" | Unsigned + quarantine (`xattr -cr /Applications/Orb.app`), or (v0.1.0) dangling Node symlinks | Rebuild with current `bundle-node.js`; users: clear quarantine. |
| Windows `prepare-dist` fails extracting PHP zip with a Python-not-found error | Pre-`38d6038` `firefly-runtime.js` used `python -m zipfile` on Windows | Current code uses `powershell Expand-Archive` → bsdtar fallback (`extractZipWindows`); no system Python needed. |
| Firefly archive "extracted to an empty tree" / tar error mid-CI | Pre-`02ac9d3` download race after 302 | Fixed; if it recurs, check that `downloadFile` still defers `createWriteStream`. |
| Empty artifact upload / job green with nothing to ship | Glob mismatch after renaming artifacts | `if-no-files-found: error` now fails the job; keep `artifactName` and upload globs in sync. |
| Only one Windows `.exe` in `dist/` although two targets are configured | `artifactName` makes `nsis` and `portable` resolve to the same filename `Orb-<v>-x64-win.exe`; the later target overwrites the earlier | Give `nsis`/`portable` their own `artifactName` (e.g. `${productName}-Setup-${version}-${arch}.${ext}` vs `${productName}-Portable-…`) or drop one target. Since `45fcca5` releases ship a single `…-x64-win.exe`; v0.1.0 (before the pattern) shipped both `Orb.Setup.0.1.0.exe` and `Orb.0.1.0.exe`. |
| `npm run dist:linux` runs without checking resources | No `predist:linux` script | Add one mirroring `predist:mac`/`predist:win`. |
| `macos-13` runner not found | Retired by GitHub | Already switched to `macos-15-intel` (`899ddb4`). If that is retired too, x64 macOS builds need self-hosted Intel hardware or a Rosetta-based flow (which would still not give a native x64 llama build). |
| First multimodal use hangs/fails offline | Runtime `pip install torch transformers …` into the bundle | Requires network; see [Multimedia enrichment](11-multimedia-enrichment.md). |
| Windows arm64 host builds an x86_64 Python | `pythonAsset()` hard-codes `x86_64-pc-windows-msvc` | Intentional (no arm64 target for v1); runs under emulation. |

Gotchas an assistant would otherwise get wrong:

- `desktop/resources/` contents on a dev machine are stale builds, not source. Do not "fix" code there; edit `backend/app` / `frontend/src` and re-run the stage. Conversely, if `desktop/resources/backend/python` exists, `npm start` **uses the bundled backend**, so backend source edits appear to have no effect until `npm run bundle-python` (or the directory is removed).
- `bundle-python.js` copies `backend/app` with a plain recursive copy: **local `__pycache__` and stray files under `backend/app` end up in the bundle** (pycache is pruned; anything else is not).
- The next.config comment about `8000` is stale; the real proxy target is `ports.js#apiUrl()` (17401).
- `PACKAGING.md` says CI produces "unsigned artifacts, 120m timeout, cmake on macOS" and describes the pipeline correctly, but its "Quick start" omits `npm ci` vs `npm install` and does not mention the Linux job/`predist:linux` gap.
- `prefetch-binaries` output is not bundled and is unrelated to `prepare-dist`.
- `files` includes `binaries/**/*` but `desktop/binaries/` only holds a README; the real Qdrant/Meili binaries are downloaded to `DATA_DIR/bin/<triple>/` at runtime.
- The `os` macro yields `mac`/`win`/`linux`, while `arch` yields `arm64`/`x64` except `x86_64` for AppImage. CI upload globs rely on this.
- `backend/app/main.py` `version="0.1.0"` is out of step with `0.2.0` elsewhere.

---

## 13. Extension points / how to modify safely

- **Bump Python**: change `ORB_PYTHON_RELEASE`/`ORB_PYTHON_VERSION` defaults in `bundle-python.js` **and** the workflow `env`; confirm every pinned wheel in `requirements.txt` exists for that CPython (kuzu, PyMuPDF, av, numpy). Re-run `validateImports` mentally: add any new must-import module there.
- **Bump Node**: `ORB_NODE_VERSION` in `bundle-node.js` + workflow `env`; keep the `setup-node` major aligned; verify the tarball layout still has `bin/node` and the helper symlink names haven't changed.
- **Bump Firefly / PHP**: `FIREFLY_VERSION` / `PHP_BIN_VERSION` in `desktop/firefly-runtime.js`; rebuild the seed; the runtime marker check makes an out-of-date seed harmless but slow. See [Finance / Firefly](17-finance-firefly.md) for migration implications.
- **Add a Python dependency**: add to `backend/requirements.txt` (build-time, bundled) or to both `requirements-multimodal.txt` and `_MULTIMODAL_PIP` in `multimodal_services.py` (runtime-installed). If it is import-critical at API start, add it to `validateImports`.
- **Add a bundled asset tree**: create it under `desktop/resources/<name>` from a new `scripts/<stage>.js`, call it from `prepare-dist.js`, add an `extraResources` entry, add its sentinel path to `check-resources.js`, and read it via `getResourcesRoot()` in the shell.
- **Add a shell source file**: it must be added to `build.files` or it will not be in the asar (the list is an allow-list).
- **Change ports**: `desktop/ports.js` only; then rebuild the frontend (rewrite table) — see [Desktop shell](04-desktop-shell.md) §9.
- **Add a CI target (e.g. Linux arm64)**: copy `build-linux`, choose an arm64 runner, pass `--arm64`; `bundle-*.js` already map `process.arch === "arm64"` for Linux/macOS (Windows arm64 Python is not mapped). Keep the artifact glob distinct.
- **Enable signing**: add secrets → step `env`; delete `CSC_IDENTITY_AUTO_DISCOVERY: "false"`; solve the runtime pip-into-bundle problem first (§9.2).
- **Enable publishing**: switch `-p never` to `-p onTag` with `GH_TOKEN`, which also generates `latest*.yml` for `electron-updater`; only then does `ORB_ENABLE_UPDATER=1` do anything useful.

---

## 14. History / rationale

- `3f21e08` (2026-08-02) — "Ship LifeOS as a Docker-free desktop app": introduced `desktop/scripts/*`, `desktop-release.yml` (single mac job + windows), `extraResources` layout, `.gitignore` entries for `/desktop/resources` and `/desktop/dist`; removed the Ollama/sidecar model services and their Dockerfiles.
- `6162be2` — rename LifeOS/LiveOS → Orb (`appId com.orb.app`, `productName Orb`, `ORB_*` env names with `LIVEOS_*` aliases).
- `845fd47` — first tagged build failed: `ports.js` had lost its URL helpers (`apiUrl` used by `build-frontend.js`) and CI Node was `22`; moved to Node 24 (`ORB_NODE_VERSION 22.14.0 → 24.4.1`, `setup-node 24`).
- `899ddb4` — added `author` (electron-builder requires maintainer info for Linux targets), pinned Electron to `43.2.0`, refreshed lockfiles, switched the Intel job from retired `macos-13` to `macos-15-intel`.
- `2c3518a` — added the `build-linux` AppImage job (tag `desktop-v0.1.0` points here).
- `45fcca5` (tag `desktop-v0.1.1`) — Gatekeeper fix: strip `npm`/`npx`/`corepack` links and npm tree in `bundle-node.js`; `artifactName` with `${arch}-${os}`; split `build-mac` into `build-mac-arm64`/`build-mac-x64` with explicit `dmg zip --<arch>` targets and per-arch upload globs + `if-no-files-found: error`; `actions/*` bumped to v5/v6; README gained the `xattr -cr` instructions.
- `fbcafe7` (2026-08-03) — dropped Alembic copying from `bundle-python.js`, removed `scripts/hotfix-multimodal-ingest.sh` (the numpy-tests hotfix) and the compose `init` service; Docker declared contributor-only.
- `38d6038` (2026-08-04) — Windows `prepare-dist` no longer needs a system Python: PHP zip extraction via PowerShell `Expand-Archive` with bsdtar fallback.
- `e14dc67` (2026-08-07) — version `0.2.0` in desktop/frontend package files and README (backend `FastAPI(version=…)` left at `0.1.0`).
- `02ac9d3` (2026-08-07, tag `desktop-v0.2.0`) — deferred `createWriteStream` until HTTP 200 in all downloaders after Firefly archives were truncated mid-extract on the release runner; the first `desktop-v0.2.0` run had failed for this reason and the tag was re-pointed.
