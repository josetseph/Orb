# 04 — Desktop shell and runtime

Orb ships as a Tauri (Rust) shell over a Python desktop runtime. The shell is
deliberately small: it spawns one process, owns one window, and provides the few
things only a native shell can (file pickers, notifications, "open in browser").
Everything else is the Python runtime's job.

## 1. Responsibilities and boundaries

| Concern | Owner |
|---|---|
| First-run setup (data dir, models dir, vault → `paths.json`) | shell (`desktop/shell/index.html` + `save_setup` command) |
| Spawning, watching, restarting, killing the runtime | shell (`src-tauri/src/runtime.rs`) |
| The window, navigation guard, `window.orbDesktop` bridge | shell (`runtime.rs`, `src/init.js`) |
| Native pickers, notifications, external links | shell (Tauri dialog / notification / opener plugins) |
| Stale-port sweep, Qdrant + Meilisearch download and boot, Firefly install/migrate/boot, multimodal prep | runtime (`backend/app/desktop_runtime.py`) |
| Serving the UI, credentials (OS keychain), reveal-in-folder | API (`backend/app/main.py`, `services/credentials.py`, `api_desktop.py`) |
| Sidecar teardown | runtime: on API exit, and by itself when the shell process disappears |

## 2. Files

```
desktop/
  build.py                 packaging pipeline (see doc 05; on macOS the DMG comes from hdiutil, not Tauri)
  shell/index.html         first-run page; blank on every other launch until the window navigates
  build/                   icon.png, entitlements.mac.plist
  src-tauri/
    tauri.conf.json        identifier com.josetseph.orb, no static windows, withGlobalTauri
    capabilities/shell.json      bundled page: core + dialog + opener
    capabilities/remote-ui.json  http://127.0.0.1:17401 (the UI): dialog open, opener, notifications
    src/main.rs            builder, plugins, run-event handling (Exit → stop; macOS Reopen)
    src/runtime.rs         paths, layout (packaged vs repo), spawn/watch/kill, window, health poll
    src/commands.rs        app_state, save_setup, print_page, restart_backend, check_update, install_update
    src/init.js            injected into every page: orbDesktop bridge + single-window guard
backend/app/desktop_runtime.py   the process the shell spawns
```

## 3. Boot sequence

1. `main.rs` → `runtime::boot`. The window is created hidden on the bundled page.
2. If `paths.json` is missing or unreadable, the window is shown: the page calls
   `app_state`, sees `first_run`, and renders the setup form. `save_setup` validates
   absolute paths and that each folder can be created and written to (`usable_dir`,
   a probe file; the macOS picker opens on the read-only Orb installer disk right
   after install), writes `paths.json` atomically, then falls through to step 3.
   On later launches `check_saved_folders` first re-checks the data dir (probe), the
   models dir and the vault's `attachments` folder (created as the backend would;
   no probe file in a synced vault), so an unusable folder is reported by name.
   Any start failure dialog offers **Change folders…**, which sets `setup_requested`
   and navigates to the bundled page; `app_state` then reports `first_run` with the
   saved folders pre-filled. A Dock reopen after a failure re-checks instead of
   opening the (dead) UI URL.
3. `runtime::start` spawns `python -m app.desktop_runtime` (own process group,
   stdout/stderr → `DATA_DIR/logs/backend.log`) and a watcher thread.
4. The runtime frees its ports, fixes sidecar addresses in the environment, starts a
   background thread that fetches/boots Qdrant, Meilisearch and Firefly, and runs
   uvicorn immediately. `/health` answers in ~2 s.
5. The watcher polls `/health` over a raw TCP GET; on 200 it navigates the window to
   `http://127.0.0.1:17401` and shows it. There is no splash: boot progress of the
   sidecars is written to `DATA_DIR/boot-status.json`, which the API exposes in
   `/api/v1/admin/maintenance-status` and the UI's status indicator displays.
6. The watcher then waits on the child. An unexpected exit respawns it (at most three
   times a minute); beyond that an error dialog shows the log tail.

Quit (`RunEvent::Exit`) sends SIGTERM to the runtime's process group, escalating to
SIGKILL after 4 s. The runtime also watches its parent pid and shuts itself and the
sidecars down when the shell disappears for any reason, so a crashed or killed shell
leaves nothing behind.

## 4. The runtime (`desktop_runtime.py`)

- **Ports**: `ORB_{API,FIREFLY,QDRANT,MEILI}_PORT`, defaults 17401 / 17412 / 17433 / 17470.
- **Paths**: `ORB_DATA_DIR` / `ORB_MODELS_DIR` / `paths.json` via `app.core.paths`.
- **Cloud-sync guard**: `main()` prints `[desktop] WARNING: data dir <path> is inside a cloud-synced folder; move it to local disk (Settings -> Storage)` when any component of the data dir is `CloudStorage`, `Mobile Documents`, `Dropbox` or `Google Drive` — evicted Files-On-Demand placeholders block reads and sync clients corrupt SQLite/Kuzu/Qdrant under a running engine. Keep `DATA_DIR` on local disk (the default `~/Library/Application Support/Orb/data` is); only the markdown vault belongs in a synced folder, pinned "Always keep on this device".
- **Env defaults** (overridable): `LLM_PROVIDER=local`,
  `EMBEDDING_PROVIDER=local`, `LLAMA_*`, `EMBED_N_CTX`, `RERANK_N_CTX`.
- **Sidecars**: Qdrant and Meilisearch binaries are downloaded on first run into
  `DATA_DIR/bin/<platform>/` (optional `ORB_SHA256_<ASSET>` pins). The Meili master
  key lives in `DATA_DIR/meili_master_key`. Firefly: portable PHP + app seeded from
  `<resources>/firefly` (or downloaded), `.env` written with every value quoted (`_env_quote`: single quotes, or double quotes with `\`, `"` and `$` escaped when the value holds an apostrophe — the default macOS data dir path contains a space and phpdotenv rejects an unquoted one), `artisan migrate`, `db:seed`,
  Passport keys/client, desktop user + API token in `DATA_DIR/firefly/runtime.json`.
- **Extraction server**: not a boot sidecar — the API itself downloads the pinned
  llama.cpp build into `DATA_DIR/bin/llama.cpp-b11617/` and starts `llama-server` on
  the first local ingestion, stopping it after 300 s idle; `stop_sidecars()` stops it
  first on shutdown, and a server orphaned by a crashed backend is killed at the next
  start (`DATA_DIR/llama-server.pid`). See [12](12-local-models-and-inference.md) §7.5.
- **Reconnect on use**: `QdrantService` and `MeilisearchService` retry their
  connection when called, so a KB opened before a sidecar is listening becomes
  searchable the moment it is.
- **Status**: `status()` prints `[desktop] …` lines to the log and rewrites
  `boot-status.json` atomically; the final line is `Ready`.
- **Build-time reuse**: `python -m app.desktop_runtime prefetch-firefly DEST` produces
  the Firefly seed that ships in the bundle.

## 5. Window and bridge

- The window may only navigate to the app origin (or the bundled page): `trusted()`
  accepts the `tauri`/`asset` schemes, the `tauri.localhost` host, and any URL whose
  origin equals `Url::parse(app_url()).origin()` (an origin comparison, not a string
  prefix). Any other URL, and anything that asks for a new window (`window.open`,
  `target=_blank`), is opened in the system browser instead — note content renders
  in this window. WebKit runs iframe loads through the same handler (URL only, no
  frame info), so `embed_player()` also admits the editor's video embeds:
  `https://www.youtube-nocookie.com/embed/…` and `https://player.vimeo.com/video/…`.
- macOS: `enable_text_checking()` registers `WebContinuousSpellCheckingEnabled` and `WebGrammarCheckingEnabled` as NSUserDefaults *defaults* at setup (before the window exists), so the note editor gets spelling and grammar underlines; the user's choice in the context menu's Spelling and Grammar submenu overrides them.
- `init.js` provides `window.orbDesktop` with `isDesktop`, `pickDirectory`, `pickFile`
  (Tauri dialog plugin), `restartBackend`, `printPage`, `checkUpdate` / `installUpdate`
  (commands) and `notify` (notification plugin,
  requests permission the first time). `frontend/src/lib/desktop.ts` is the typed
  consumer; `notifyIfUnfocused` fires only when the window is not focused.
- Updates: the Settings button runs `check_update` once per launch (`availableUpdate()` in
  `desktop.ts`) and shows a dot on Settings; Settings → About shows the version and an
  "Update to x.y.z" button. `install_update` downloads (emitting `update-progress`
  0-100), stops the Python runtime, installs and relaunches. Nothing installs without
  that click. Release side: [Packaging](05-packaging-build-and-release.md) §5.
- The UI is a remote origin to Tauri. `capabilities/remote-ui.json` grants it exactly
  those plugin commands and nothing else; `save_setup` additionally refuses calls
  from any page that is not the bundled one.
- Nothing in the shell panics in an event callback: a panic inside a macOS callback cannot unwind and aborts the whole app (a 2026-10-18 crash: `abort()` under tao's `handle_nonuser_event`). `ensure_window` returns `Option` and logs a failed window build to `logs/shell.log` instead of `expect`ing; the runtime's mutexes recover from poisoning (`PoisonError::into_inner`); a panic hook writes any panic, with location and backtrace, to `logs/shell.log`. A second crash (2026-10-21) logged only "panic in a function that cannot unwind" with no Rust panic before it: an Objective-C exception from AppKit or WebKit unwinding into the event loop's Rust frames. `runtime::log_objc_exceptions` (installed at setup, macOS) hooks `objc_setExceptionPreprocessor` and writes every thrown exception's name, reason and `callStackSymbols` to `shell.log` before the abort (including ones WebKit catches itself); `objective_c_exceptions_are_recorded` throws a real `NSRangeException` to check it. Release builds keep line tables in `orb.dSYM` (`split-debuginfo = "packed"`); `build.py` saves each as `target/dsyms/orb-<version>-<uuid>.dSYM` and CI uploads it as a `dsym-<platform>` run artifact (not published), so crash-report addresses decode with `atos`.
- macOS keeps running with no windows so a Dock click reopens instantly
  (`RunEvent::Reopen`).

## 6. Environment variables read by the shell

| Variable | Effect |
|---|---|
| `ORB_API_PORT` | API port (default 17401); the window URL follows it |
| `ORB_URL` | Load this URL instead of the API (Vite dev server) |
| `ORB_PATHS_FILE` | Alternate `paths.json` (scratch profiles) |
| `ORB_SKIP_WIZARD` | Never show setup |
| `ORB_ROOT`, `ORB_PYTHON` | Dev: repo root and interpreter for the runtime |
| `ORB_USE_RESOURCES` | Dev: use `desktop/resources/` like a packaged build |

Packaged builds pass `FRONTEND_DIR` and `ORB_RESOURCES_ROOT` to the runtime; debug
builds always run the repo checkout so a stale prepared tree can never shadow it.

## 7. Development

See `desktop/README.md`: `npm run dev` in `frontend/` and
`ORB_URL=http://127.0.0.1:3700 cargo tauri dev` in `desktop/src-tauri/`.
