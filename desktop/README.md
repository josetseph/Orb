# Orb Desktop

A Tauri (Rust) shell over the Python desktop runtime. The shell spawns one child,
`python -m app.desktop_runtime`, which:

1. Starts the API at once (the window opens as soon as `/health` answers, ~2 s)
2. Behind it, downloads **Qdrant** + **Meilisearch** into `DATA_DIR/bin` on first run and
   boots them with **Firefly III** (embedded PHP); their clients reconnect on use
3. Serves the built **Vite UI** from the API (one origin, no CORS, no UI server)
4. Persists cloud keys in the **OS keychain** and stops every sidecar when the API exits

First run shows a bundled setup page (data dir, models dir, vault) that
writes `paths.json`. Every other launch opens straight onto Notes; download and
boot progress shows in the status indicator inside the app. Keep the data dir on
local disk (the default `~/Library/Application Support/Orb/data` is fine); the
runtime warns when it sits in iCloud/OneDrive/Dropbox/Google Drive. The vault is
the one folder that may be synced.

Ports: API `17401` (serves the UI), Firefly `17412`, Qdrant `17433`, Meilisearch
`17470`. Override any with `ORB_*_PORT`. See [PACKAGING.md](./PACKAGING.md).

## Layout

```
desktop/
  build.py          bundle Python + backend, build UI, seed Firefly, preflight, package
  shell/index.html  first-run setup page (bundled into the app)
  build/            icon.png, entitlements.mac.plist
  src-tauri/        the Rust shell: src/{main,runtime,commands}.rs + src/init.js,
                    tauri.conf.json, capabilities/
  resources/        build output (gitignored): backend/, frontend/, firefly/
```

The shell owns four things: spawning/watching/killing the runtime, the window (with a
navigation guard that sends foreign URLs to the system browser), native file pickers,
and native notifications. `src/init.js` is injected into every page and provides
`window.orbDesktop` (`pickDirectory`, `pickFile`, `restartBackend`, `notify`) to the
UI. Everything else the UI needs is an HTTP call to the API.

## Development

Prerequisites: Rust (`brew install rust` or rustup), `cargo install tauri-cli`,
Node 20+ (only to build the UI), Python 3.11+ with the backend venv at
`backend/.venv` (or point `ORB_PYTHON` at any interpreter with the backend deps).

```bash
# Terminal 1 — UI with live reload
cd frontend && npm install && npm run dev        # http://127.0.0.1:3700, proxies /api/v1 to 17401

# Terminal 2 — the shell (debug builds always run the repo backend, never resources/)
cd desktop/src-tauri && ORB_URL=http://127.0.0.1:3700 cargo tauri dev
```

Without `ORB_URL` the shell loads the API URL, which serves `frontend/dist` if you
have run `npm run build`. Useful overrides: `ORB_PATHS_FILE` (a scratch profile),
`ORB_SKIP_WIZARD=1`, `ORB_API_PORT` and friends, `ORB_USE_RESOURCES=1` (make a debug
build use `desktop/resources/` like a packaged app).

Logs: `DATA_DIR/logs/backend.log` (runtime + API), `qdrant.log`, `meilisearch.log`,
`firefly.log`, `multimodal.log`.

## Packaging

```bash
python3 desktop/build.py prepare   # ~10–20 min first time: Python wheels, UI, Firefly seed
python3 desktop/build.py dist      # preflight, then `cargo tauri build` (macOS: .app via Tauri, DMG via hdiutil)
```

Bundles land under `desktop/src-tauri/target/release/bundle/`. Details in
[PACKAGING.md](./PACKAGING.md).
