# Orb Desktop Packaging

Build unsigned macOS (`.dmg`), Windows (`.exe`), and Linux (`.AppImage`) installers that bundle Electron, embedded Python, portable Node, a Next.js standalone UI, and a seeded Firefly III + PHP runtime.

## Quick start (local build)

```bash
# From repo root — requires network for Python/Node/Firefly/PHP downloads + pip
cd desktop
npm install
npm run prepare-dist    # ~10–20 min: Python wheels, frontend build, Node
npm run dist:mac        # or dist:win on Windows
```

Artifacts land in `desktop/dist/`.

## Dev vs packaged layout

| Mode | How |
|------|-----|
| **Dev** | `npm start` — uses repo `backend/` + `frontend/` (`next dev`) |
| **Packaged test** | Run `prepare-dist`, then `ORB_RESOURCES=./resources npm start` |
| **Installer** | `prepare-dist` + `electron-builder` |

User data (SQLite, vault, Qdrant/Meili binaries, GGUF models) always lives outside the app bundle under Application Support / `%APPDATA%\Orb\`.
Firefly's writable state also lives outside the bundle under the same app-data root.

## Networking (important)

Desktop uses a dedicated high port block so it does not collide with typical
dev stacks (`8000`, `3000`, `6333`, `7700`):

| Service | Port |
|---------|------|
| UI (Next) | **17400** |
| API (FastAPI) | **17401** |
| Firefly III | **17412** |
| Qdrant | **17433** |
| Meilisearch | **17470** |

Qwen3-ASR / Marlin / chat / embed / rerank all load **in-process** in the API (no model HTTP ports).

- Electron loads `http://127.0.0.1:17400`.
- Desktop frontend build uses **same-origin** `NEXT_PUBLIC_API_URL=/api/v1` with Next rewrites to `http://127.0.0.1:17401`.
- Override any port with `ORB_UI_PORT`, `ORB_API_PORT`, `ORB_FIREFLY_PORT`, `ORB_QDRANT_PORT`, `ORB_MEILI_PORT`.
- Docker / contributor compose keeps its own mapped ports (e.g. host `8700` → backend).

## What gets bundled

- **backend/** — portable CPython + `app/` + pip deps (`kuzu`, `llama-cpp-python`, etc.)
- **frontend/** — Next.js standalone (`server.js` on port 17400)
- **node/** — portable Node to run the frontend server (headers/docs pruned)
- **firefly/** — Firefly III release app + portable PHP seed copied into app-data on first launch

Not bundled: GGUF models, Qdrant/Meilisearch (first-run download into `DATA_DIR`), or the Firefly SQLite database.

Frontend standalone deps are stored as `frontend/node_deps/` (not `node_modules`)
because electron-builder strips folders named `node_modules` from `extraResources`.
The supervisor boots the UI with `node run-server.js`.
The supervisor copies the Firefly seed from bundled resources into `DATA_DIR/firefly/`, writes `.env`, keeps the SQLite DB under `DATA_DIR/firefly/app/storage/database/firefly.sqlite`, then serves Firefly locally on port `17412`.

## Platform notes

- Build **macOS** installers on macOS (Metal `llama-cpp-python` on arm64; needs `cmake`).
- Build **Windows** installers on Windows (CPU wheels; x64 only for v1).
- Build **Linux** installers on Linux (AppImage; x64 only for v1).
- Do not cross-compile native Python wheels.

## CI

Push a tag matching `desktop-v*` to trigger `.github/workflows/desktop-release.yml` (unsigned artifacts, 120m timeout, cmake on macOS).

## Stage 6 — Signing & auto-update (when ready)

### macOS notarization

```bash
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="XXXXXXXXXX"
# Optional: CSC_LINK + CSC_KEY_PASSWORD for Developer ID signing
```

[`scripts/notarize.js`](scripts/notarize.js) runs after sign when `APPLE_*` are set.

### Windows Authenticode

Set `CSC_LINK` (certificate file) and `CSC_KEY_PASSWORD` for electron-builder.

### Auto-update

1. Publish signed builds to GitHub Releases (`publish.owner` = `josetseph`).
2. Set `ORB_ENABLE_UPDATER=1` so `electron-updater` in [`main.js`](main.js) checks releases.

Unsigned builds **never** check for updates unless that env is set.
