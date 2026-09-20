# Orb Desktop Packaging

Build unsigned macOS (`.dmg`), Windows (`.exe`, NSIS), and Linux (`.deb`, `.rpm`)
installers that bundle the Tauri shell, embedded Python (which serves the built UI),
and a seeded Firefly III + PHP runtime.

## Quick start (local build)

```bash
# From repo root — needs network for the Python/Firefly/PHP downloads + pip
python3 desktop/build.py prepare   # ~10–20 min: Python wheels, UI build, Firefly seed
python3 desktop/build.py dist      # preflight + cargo tauri build (macOS: .app only, then the DMG via hdiutil)
```

Bundles land in `desktop/src-tauri/target/release/bundle/{dmg,nsis,deb,rpm}/`.
On macOS `build.py` asks Tauri for the `.app` only and writes the DMG itself with a single
`hdiutil create` from a staging folder (`Orb.app` + an `Applications` shortcut): Tauri's own
DMG script mounts a temp image and drives Finder by AppleScript, and its unmount fails
intermittently with "Resource busy".

## Stages (`desktop/build.py`)

| Stage | Output | Notes |
|---|---|---|
| `python` | `resources/backend/` | python-build-standalone + `pip install -r backend/requirements.txt` (Metal `llama-cpp-python` on Apple Silicon) + `backend/app`. An interpreter whose imports still pass is reused; `ORB_REBUILD_PYTHON=1` forces a rebuild. |
| `frontend` | `resources/frontend/` | `npm ci && npm run build` in `frontend/`, copies `dist/`. Node ships nothing. |
| `firefly` | `resources/firefly/` | `python -m app.desktop_runtime prefetch-firefly` with the bundled Python — the same code that installs Firefly at runtime. Reused when present; `ORB_REBUILD_FIREFLY=1` refetches. |
| `check` | — | Trees exist and the bundled Python imports every critical module. |
| `dist` | bundles | `check`, then `cargo tauri build --config '{"bundle":{"resources":…}}'`. The resource map is passed here rather than kept in `tauri.conf.json` so dev builds never copy the multi-GB trees. On macOS it passes `--bundles app`, then stages `Orb.app` (`ditto`) + an `Applications` symlink and runs one `hdiutil create` (HFS+, UDZO, zlib-9) → `bundle/dmg/Orb_<ver>_<arch>.dmg`. |

## Runtime layout

Everything under `resources/` lands in the app's resource dir (`Orb.app/Contents/Resources`
on macOS). The shell finds `backend/python/bin/python3` (or `python.exe`) there and runs
`python -m app.desktop_runtime` with `FRONTEND_DIR` and `ORB_RESOURCES_ROOT` set.

User data always lives outside the bundle under Application Support / `%APPDATA%\Orb\`
(`paths.json`, `data/` with SQLite, vault, Qdrant/Meili binaries and data, Firefly's
writable state, logs; `models/` with GGUFs). Not bundled: GGUF models, Qdrant and
Meilisearch (first-run download into `DATA_DIR/bin`), the Firefly SQLite database.

Keep the data dir on local disk. The runtime prints `[desktop] WARNING: data dir … is inside a
cloud-synced folder` when the path contains `CloudStorage`, `Mobile Documents`, `Dropbox` or
`Google Drive`: evicted Files-On-Demand files block reads and sync clients corrupt the databases
under a running engine. Only the notes vault may live in a synced folder.

## Networking

| Service | Port |
|---|---|
| API (serves the UI) | **17401** |
| Firefly III | **17412** |
| Qdrant | **17433** |
| Meilisearch | **17470** |

Override with `ORB_API_PORT`, `ORB_FIREFLY_PORT`, `ORB_QDRANT_PORT`, `ORB_MEILI_PORT`
(both the shell and the runtime read them). The UI runs from `http://127.0.0.1:17401`,
so its access to the shell's native pickers and notifications is granted by
`src-tauri/capabilities/remote-ui.json`, which lists that origin explicitly.

## Platform notes

- Build **macOS** installers on macOS (Metal `llama-cpp-python` on arm64; needs `cmake`).
- Build **Windows** installers on Windows (CPU wheels; x64 only).
- Build **Linux** installers on Linux (x64 only; needs `libwebkit2gtk-4.1-dev
  libappindicator3-dev librsvg2-dev patchelf`).
- Do not cross-compile native Python wheels.

## CI

Push a tag matching `desktop-v*` to trigger `.github/workflows/desktop-release.yml`
(one matrix job per platform, unsigned artifacts, then a `release` job that drafts the GitHub
Release from the four artifacts with `gh release create --draft --generate-notes`; publish it by
hand after smoke-testing each installer).

## Signing, notarization, auto-update (when ready)

`cargo tauri build` signs and notarizes the macOS `.app` when `APPLE_SIGNING_IDENTITY`,
`APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID` are set, and Windows bundles via
`bundle.windows.certificateThumbprint` / signtool. Hardened-runtime entitlements come
from `build/entitlements.mac.plist`. The DMG written by `build.py`'s `hdiutil` step carries no
signature of its own.

Auto-update is not wired yet. When it is: `tauri-plugin-updater`, a minisign keypair
(`TAURI_SIGNING_PRIVATE_KEY`), a `latest.json` on the GitHub release, and the same
opt-in gate as before (`ORB_ENABLE_UPDATER=1`; unsigned builds never check).
