# Orb desktop: Electron → Tauri (Rust)

> Status 2026-09-18: steps 0–5b are implemented in the working tree (see doc 04 / 05).
> Left: docs sweep of older mentions, updater + signing (step 7).

Goal: replace the Electron shell with the smallest Tauri app that does the same job,
using OS-native pieces wherever one exists. Everything the shell does not have to own
moves into the Python runtime, which already exists and already ships.

## What the shell has to do (and nothing else)

After steps 1 and 2 of the migration the Electron shell is already down to this:

| Job | Today (Electron) | Tauri |
|---|---|---|
| Spawn one child: `python -m app.desktop_runtime` | supervisor.js | `std::process::Command` (own process group) |
| Show boot progress | splash.html reads IPC `status` | no splash: the UI opens as soon as the API answers; the runtime's status file is surfaced inside the app (status indicator) |
| First-run wizard (data dir, models dir, vault, AI mode) → `paths.json` | wizard.html + 3 IPC handlers | same page (`setup.html`), same 3 commands, only when `paths.json` is missing |
| Open the UI once `/health` answers | main.js `loadWithRetry` | hidden window; navigate to `http://127.0.0.1:17401` and show it the moment `/health` answers |
| Native folder / file pickers for the UI | preload → dialog | `tauri-plugin-dialog` |
| Keep note content from navigating the window elsewhere | `will-navigate` guard | `on_navigation` + `tauri-plugin-opener` |
| macOS: stay alive with no windows, reopen on Dock click | `activate` handler | `RunEvent::Reopen` |
| Kill the child on quit (SIGTERM → SIGKILL) | stopAllAsync | kill process group on `RunEvent::Exit` |
| Restart the backend on request | `backend:restart` | `restart_backend` command |
| Auto-update (opt-in, signed builds only) | electron-updater | `tauri-plugin-updater` |
| Installers + signing | electron-builder | `tauri build` (dmg / nsis / AppImage) |

## What moves out of the shell

These are shell responsibilities only because Electron happened to be there. Each one
has a native home in the Python process, and moving them means the Rust shell needs
fewer commands and less privileged IPC.

| Today in the shell | Where it goes | Why |
|---|---|---|
| `credentials.js` (safeStorage blob + push to API on boot) | Python `keyring` in `CredentialStore` — macOS Keychain, Windows Credential Manager, Secret Service on Linux | The API already holds the keys; persisting them from there removes 353 lines of JS, the boot-time push, and five IPC handlers. The UI only ever calls the endpoint-credential pair through the bridge; provider keys already go through `/api/v1/credentials`. |
| `reveal-in-folder` (allow-list of vault/data/models roots, then Finder) | `POST /api/v1/desktop/reveal` → `open -R` / `explorer /select,` / `xdg-open` | The backend already knows every workspace's vault path; main.js has to ask the API for them today. |
| `get-api-base-url` | delete; the UI is same-origin, `window.location.origin` is the answer | Dead since step 1. |
| Port constants (`ports.js`) | already in `desktop_runtime.py`; the shell only needs the API port | One source of truth. |

Cost to note: Electron's safeStorage ciphertext cannot be read by Python. Users on the
Electron build re-enter their endpoint keys once after upgrading. Provider keys are
already re-entered per session on unsupported platforms, so this is the only migration.

## Instant open (no boot screen)

Nothing may stand between launch and the notes editor. Three backend changes make
that true, and they land before the shell:

1. **The runtime starts the API first.** `desktop_runtime.py` runs uvicorn immediately
   and boots Qdrant, Meilisearch and Firefly in a background thread. The health check
   answers within Python's import time (~1–2 s), including on first run while
   binaries are still downloading.
2. **Search services reconnect on use.** `QdrantService` and `MeilisearchService`
   currently disable themselves for the life of the process when the server is not up
   at construction. They get a retry-on-use (a timestamp and a re-init), so a KB opened
   before Qdrant is listening becomes searchable the moment it is.
3. **API startup does not block on heavy work.** Embedding-infrastructure sync and vault
   watchers move to a background task after startup; `init_db` (SQLite) stays inline.
   The maintenance-status endpoint the UI already polls gains the runtime's boot status
   (`DATA_DIR/boot-status.json`), so "Downloading Qdrant… 40%" shows in the corner of
   the app instead of on a splash.

Ingestion of a note saved while Qdrant is still coming up fails today and can be
re-run from the UI; queuing it automatically is a follow-up if it turns out to matter.

First run is the one exception: the window shows the setup page (pick directories,
AI mode), and on Continue the shell writes `paths.json`, spawns the runtime and
navigates to the app as soon as `/health` answers. Downloads continue in the
background and are visible in the status indicator.

## Notifications

`tauri-plugin-notification` is cross-platform (macOS, Windows, Linux) and needs no
per-OS code. The UI already polls note processing state; when a note flips to
"Ingestion complete" or "failed" while the window is not focused, it invokes the
plugin's `notify` command. One allowed command, ~10 lines in the UI. Done last, and
dropped without ceremony if a platform misbehaves.

## Decisions

1. **The API keeps serving the UI; the Tauri window navigates to it.** One origin means
   the relative `/api/v1` and `/vault-files/…` URLs keep working and no CORS is needed.
   Bundling the UI into the Tauri asset protocol was considered and rejected: it splits
   origins and forces every vault-file URL through a rewrite for no user-visible gain.
   Revisit only when the web UI itself is replaced (Goal B).
2. **One bundled shell page, `setup.html`, shown only on first run.** Local page, full
   IPC. Every other launch creates the window hidden and shows it on the API URL the
   moment `/health` answers; no splash, no second window.
3. **Remote IPC is limited to two dialog commands.** The UI loaded from `127.0.0.1`
   needs `pick_directory`, `pick_file` and `restart_backend`. Tauri gates that behind
   `app.security.dangerousRemoteDomainIpcAccess`; it is scoped to the loopback host and
   only those commands. Everything else the UI needs is an HTTP call to the API.
4. **The shell auto-respawns the runtime** when it exits unexpectedly (bounded retries,
   backoff). The existing "Backend offline → restart" button stays and calls the same
   respawn.
5. **Node leaves `desktop/` entirely.** The build scripts (`bundle-python`,
   `build-frontend`, `prefetch-firefly`, `check-resources`, `source-stamp`,
   `prepare-dist`) become one Python file, `desktop/build.py`, run with the system
   Python. Node remains only where it is unavoidable: building the React app in
   `frontend/` (`build.py` shells out to `npm ci && npm run build`). The Tauri CLI is
   installed with cargo, not npm. `desktop/package.json` goes away.
6. **Native by default:** system webview (WKWebView / WebView2 / WebKitGTK), native
   file dialogs, OS keychain, OS reveal, Tauri's default native menu bar, native
   installers, native notifications. No tray, no custom title bar: neither is needed.

## Layout after the change

```
desktop/
  build.py               prepare-dist (bundle python, build frontend, prefetch firefly,
                         preflight checks, source stamps) — one file, system Python
  setup.html             first-run page only (~150 lines, reuses wizard.html markup)
  build/icon.png, build/entitlements.mac.plist
  src-tauri/
    Cargo.toml
    tauri.conf.json      identifier, resources map, bundle targets, remote-IPC scope
    capabilities/        boot window: all commands; main (remote) window: dialog + restart
    Info.plist           NSAllowsLocalNetworking for http://127.0.0.1
    src/main.rs          builder, plugins, boot flow, navigation guard, quit/reopen
    src/runtime.rs       paths.json, resources root, spawn/kill/respawn, health wait
    src/commands.rs      pick_directory, pick_file, restart_backend, boot_status,
                         default_paths, app_info, save_wizard
```

Deleted: `main.js`, `preload.js`, `supervisor.js`, `paths.js`, `ports.js`,
`credentials.js` (+ tests), `splash.html`, `wizard.html`, `scripts/*.js`,
`package.json`, `package-lock.json`, `node_modules`, electron / electron-builder /
electron-updater dependencies. Roughly 2,000 lines of JS
go; the Rust side is expected to land at 300–400 lines.

## Steps (each one ships on its own)

0. **Toolchain.** Rust 1.98 is installed via Homebrew; `cargo install tauri-cli`. Linux
   CI needs `libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf`.
1. **Instant-open backend changes** (section above): API-first runtime, reconnect-on-use
   search services, non-blocking startup, boot status in the maintenance endpoint.
1b. **Backend takes over credentials and reveal.** Add `keyring` to requirements;
   `CredentialStore` persists endpoint and provider keys to the keychain and reloads
   them at boot. Add the reveal endpoint with the same allow-list. Update
   `frontend/src/lib/desktop.ts` so endpoint credentials, reveal and the API base no
   longer touch the bridge. This step runs fine under the current Electron shell.
2. **Rust shell, dev mode.** Scaffold `src-tauri`, `boot.html`, the runtime spawner and
   the seven commands. `cargo tauri dev` against the repo backend and the Vite dev
   server, then against a `prepare-dist` tree. Verify: wizard on a clean profile,
   boot progress, health hand-off, pickers from the UI, quit kills the whole tree,
   Dock reopen on macOS, external links open in the browser.
3. **Frontend bridge → Tauri.** `desktop.ts` detects `window.__TAURI__` and calls
   `invoke`. Remove `preload.js` / Electron files and dependencies.
4. **Packaging.** `tauri.conf.json` resources map (`backend/`, `frontend/`, `firefly/`),
   entitlements carried over, icons generated from `build/icon.png`, `check-resources`
   loses the Electron allow-list check, `npm run dist` calls `tauri build`. Build a
   local unsigned DMG and run it. Update the release workflow: Rust toolchain, Linux
   webkit deps, `tauri build` per target, upload `src-tauri/target/**/bundle/**`.
5. **Remove what no longer exists.** Docker (`docker-compose.yml`, both Dockerfiles,
   `frontend/nginx.conf`), the Postgres branch of `app/core/database.py` and its
   settings, unused `asyncpg` / `boto3` / `aioboto3` / `aiobotocore` / `botocore`
   requirements, the `TYPESENSE_*` env aliases. The `typesense_collection` column name
   stays: renaming a column in existing user databases buys nothing.
5b. **Notifications** (section above).
6. **Docs.** Rewrite `Docs/04-desktop-shell.md` and the shell parts of
   `Docs/05-packaging-build-and-release.md`, `desktop/README.md`, `PACKAGING.md`, root
   README. Grep `Docs/` for "Electron" and fix each mention.
7. **Updater and signing** (unchanged policy: opt-in via `ORB_ENABLE_UPDATER=1`, never on
   unsigned builds). Tauri's updater needs a minisign keypair and a `latest.json` on the
   GitHub release; Apple notarization uses the same `APPLE_*` variables as today.

## Risks and how they are handled

- **WebKitGTK on Linux** renders the 3D graph slower than Chromium. Linux is already
  x64-only and secondary; acceptable, and unchanged by Goal B later.
- **Bundle size** stays dominated by CPython + torch + PHP; Tauri removes ~200 MB of
  Chromium. Resource copying of a multi-GB tree is slower than asar; measured in step 4.
- **Windows process tree.** Kill via `taskkill /t`, as today. A Job object is the upgrade
  if orphans ever show up; not built until they do.
- **Remote IPC flag** is scoped to `127.0.0.1` and three commands; documented in
  `Docs/04`.
- **Keychain migration** is a one-time re-entry of endpoint keys; called out in release
  notes.
- **Splash before Python.** `boot.html` renders instantly from the bundle; the Python
  runtime's status file drives the text exactly as it does now.

## Not in scope here

Goal B (native-feel UI, Obsidian-style editor) is untouched by this work and not blocked
by it: the shell will load whatever the API serves.
