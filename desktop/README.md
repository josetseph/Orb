# Orb Desktop

Electron shell that supervises a **Docker-free** local stack:

1. Wizard → `paths.json` (data dir, models dir, optional vault + AI mode)
2. Auto-downloads **Qdrant** + **Meilisearch** into `DATA_DIR/bin`
3. Bundled **Python API** + **Next.js UI** (when built with `prepare-dist`)
4. Local LLM: in-process `llama-cpp-python` + GGUF download via Setup

Desktop ports (avoid clash with typical `8000` / `3000` stacks): UI `17400`, API `17401`, Qdrant `17433`, Meilisearch `17470`. See [PACKAGING.md](./PACKAGING.md).

Product overview and installers: [root README](../README.md).

## Development

```bash
cd desktop && npm install && npm run dev
```

`npm run dev` runs the repo's `backend/` and `frontend/` directly, with `next dev`
for live reload. It points `ORB_RESOURCES` at a path that does not exist so the
packaged trees under `desktop/resources/` are ignored.

**Use `npm run dev`, not `npm start`.** `npm start` prefers `desktop/resources/`
whenever that folder exists, so it serves the last *built* backend and UI — edits
to `backend/app` or `frontend/src` simply do not appear, with no error to explain
why. `npm start` is for testing the packaged layout after `prepare-dist`.

`npm run dev` borrows the interpreter from `resources/backend/python` (it has the
dependencies installed). Without one, set `ORB_PYTHON` to any Python that has
`backend/requirements.txt` installed.

## Installers (.dmg / .exe)

See [PACKAGING.md](./PACKAGING.md).

```bash
cd desktop
npm install
npm run prepare-dist   # bundle Python + Node + frontend (~10–20 min)
npm run dist:mac       # or dist:win on Windows
```

Test packaged layout without building an installer:

```bash
npm run prepare-dist
ORB_RESOURCES=./resources npm start
```
