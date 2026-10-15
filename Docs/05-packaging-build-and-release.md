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
| `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Signs the in-app update archives (repo secret; password empty). Unset → `dist` skips them |
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

Cutting a release is three commands:

```bash
python3 desktop/build.py bump 1.1.0        # rewrites every file that carries the version
git commit -am 'Bump Orb to 1.1.0.'
git tag -a desktop-v1.1.0 -m 'Orb desktop 1.1.0' && git push origin main desktop-v1.1.0
```

`bump` edits `desktop/src-tauri/tauri.conf.json`, `Cargo.toml` and `Cargo.lock`,
`frontend/package.json` and `package-lock.json`, and `backend/app/main.py`
(`FastAPI(version=…)`), refusing to run if any of them does not hold the current
version where expected (`VERSION_SITES` in `build.py`). The tag must point at a
commit that already contains the workflow you want to run. About 40 minutes later
the draft release is waiting with its five installers; replace the generated notes
and publish.

## 5. Signing and auto-update

Builds are unsigned until the Apple / Windows certificates are configured; Tauri signs
and notarizes the `.app` (and Windows bundles) when the variables above are present, using the entitlements in
`desktop/build/entitlements.mac.plist`. The DMG that `build.py` writes with `hdiutil` gets no
signature of its own.

In-app updates use `tauri-plugin-updater` and work on unsigned builds: the updater
checks a minisign signature, not the code signature. The public key is
`plugins.updater.pubkey` in `tauri.conf.json`; the private key lives outside the repo
(`~/.tauri/orb-updater.key` on the maintainer's machine, backed up) and in the
`TAURI_SIGNING_PRIVATE_KEY` repo secret. Losing it means shipped apps can never
update again without a manual reinstall.

- With the key set, `build.py dist` adds `createUpdaterArtifacts`: macOS gets
  `Orb_<v>_<arch>.app.tar.gz` + `.sig` (renamed from `Orb.app.tar.gz` so both Macs fit in
  one release), Windows signs the NSIS installer (`.exe.sig`).
- The release job's "Update feed" step writes `latest.json` (`darwin-aarch64`,
  `darwin-x86_64`, `windows-x86_64`) and fails if a signature is missing.
- The app reads `releases/latest/download/latest.json`, so only a *published* release
  is offered. Linux (deb/rpm) is not an updater format: the check fails there and
  About links to the releases page.
- Homebrew installs update in place too; the cask declares `auto_updates true` so
  `brew upgrade` does not fight the app.
