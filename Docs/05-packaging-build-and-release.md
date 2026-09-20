# 05 — Packaging, build and release

One script, `desktop/build.py`, run with the system Python, produces everything the
installer needs; `cargo tauri build` packages it. Node is used only to build the
React UI and ships nothing.

## 1. Pipeline

```
python3 desktop/build.py prepare
   ├─ python    python-build-standalone + pip deps + backend/app  → desktop/resources/backend/
   ├─ frontend  npm ci && npm run build (Vite)                    → desktop/resources/frontend/
   └─ firefly   python -m app.desktop_runtime prefetch-firefly    → desktop/resources/firefly/
python3 desktop/build.py dist
   ├─ check     trees exist · bundled Python imports pass
   ├─ Windows/Linux: cargo tauri build --config '{"bundle":{"resources":{…}}}'
   └─ macOS:  cargo tauri build --bundles app …  then  hdiutil create → bundle/dmg/Orb_<ver>_<arch>.dmg
```

Reuse rules: the Python bundle is kept when its imports still pass
(`ORB_REBUILD_PYTHON=1` to force); the Firefly seed is kept when its version markers
are present (`ORB_REBUILD_FIREFLY=1` to refetch).

The resource map is passed to `cargo tauri build` on the command line rather than
stored in `tauri.conf.json`, because tauri-build would otherwise copy the multi-GB
trees into `target/debug` on every development build.

On macOS `dist` asks Tauri for the `.app` only and writes the DMG itself: `ditto` the app into
a staging folder next to an `Applications` symlink, then one `hdiutil create -volname Orb
-fs HFS+ -format UDZO -imagekey zlib-level=9` (HFS+ on purpose — an APFS image compressed to
roughly twice the size). Tauri's `bundle_dmg.sh` (mount a temp image, drive Finder by
AppleScript, unmount) is not used: its unmount failed intermittently with "Resource busy"
while Spotlight/Finder held the fresh volume. The `.app` is signed/notarized by Tauri when
credentials are present; the `hdiutil` step does no signing of its own.

## 2. Packaged layout

| Platform | Bundle | Resources |
|---|---|---|
| macOS | `bundle/dmg/Orb_<ver>_<arch>.dmg` (built by `build.py` with `hdiutil create`, not Tauri's DMG script) (+ `bundle/macos/Orb.app`) | `Orb.app/Contents/Resources/{backend,frontend,firefly}` |
| Windows | `bundle/nsis/Orb_<ver>_x64-setup.exe` (per-user install) | `<install dir>/{backend,frontend,firefly}` |
| Linux | `bundle/deb/*.deb`, `bundle/rpm/*.rpm` (no AppImage: linuxdeploy cannot resolve the bundled Python tree's shared libraries) | `usr/lib/orb/{backend,frontend,firefly}` |

`backend/` holds the portable CPython, site-packages and `app/`; `frontend/` the Vite
build the API serves; `firefly/` the PHP runtime + Firefly app seed copied into
`DATA_DIR/firefly/` on first launch. User data never lives in the bundle (doc 22).

The shell locates resources through Tauri's resource dir and runs
`<resources>/backend/python/bin/python3 -m app.desktop_runtime` (`python.exe` on
Windows) with `FRONTEND_DIR` and `ORB_RESOURCES_ROOT` set (doc 04).

## 3. Environment variables (build and CI)

| Variable | Purpose |
|---|---|
| `ORB_PYTHON_RELEASE`, `ORB_PYTHON_VERSION` | python-build-standalone release / version (20250317 / 3.12.9) |
| `ORB_REBUILD_PYTHON`, `ORB_REBUILD_FIREFLY` | Force a stage instead of reusing its output |
| `ORB_FIREFLY_VERSION`, `ORB_PHP_BIN_VERSION` | Firefly III release / NativePHP php-bin tag (v6.6.6 / 1.2.0) |
| `ORB_QDRANT_VERSION`, `ORB_MEILI_VERSION` | Sidecar releases fetched at first launch |
| `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | macOS signing + notarization (unset → unsigned) |

## 4. Release workflow

`.github/workflows/desktop-release.yml` runs on tags matching `desktop-v*` (or by
hand): one matrix job per platform (macOS arm64, macOS x64, Windows x64, Linux x64)
that installs Node, Python, Rust and the Tauri CLI, runs `build.py prepare` then
`build.py dist`, and uploads the bundles as artifacts (`orb-<name>`). Native Python wheels are never
cross-compiled: each platform builds on its own runner.

A second job, `release`, runs after the matrix on tag pushes only
(`startsWith(github.ref, 'refs/tags/desktop-v')`): it downloads the four artifacts with
`merge-multiple`, then `find bundles -type f | xargs gh release create "$TAG" --draft --generate-notes --title "Orb <version>"` (files only — the Linux artifact keeps `deb/` and `rpm/` subfolders).
The release stays a draft until someone has smoke-tested each installer and publishes it by hand.

The release workflow builds only. Tests, lint and `cargo check` run in `.github/workflows/ci.yml` on every push to `main` and every pull request (three jobs: `backend`, `frontend`, `desktop`; details in [Testing](24-testing.md) §7).

Version locations to bump together (all `1.0.0` today): `desktop/src-tauri/tauri.conf.json` and
`desktop/src-tauri/Cargo.toml` (`version`), `frontend/package.json`,
`backend/app/main.py` (`FastAPI(version=…)`). The tag is `desktop-v<version>`.

## 5. Signing and auto-update

Builds are unsigned until the Apple / Windows certificates are configured; Tauri signs
and notarizes the `.app` (and Windows bundles) when the variables above are present, using the entitlements in
`desktop/build/entitlements.mac.plist`. The DMG that `build.py` writes with `hdiutil` gets no
signature of its own. Auto-update is not wired: when it is, it uses
`tauri-plugin-updater` with a minisign keypair and a `latest.json` on the GitHub
release, gated by `ORB_ENABLE_UPDATER=1` and never active on unsigned builds.
