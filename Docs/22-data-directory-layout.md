# Data Directory Layout

**What this covers.** Everything Orb writes outside the application bundle: the exact on-disk tree of `DATA_DIR` (SQLite `orb.db`, `runtime_config.json`, `meili_master_key`, per-KB Kuzu files, Qdrant and Meilisearch storage, vaults, logs, downloaded engine binaries, the embedded Firefly III PHP runtime and app), the tree of `MODELS_DIR` (GGUFs, `manifest.json` and its schema, the Florence/Whisper/Marlin snapshots), the Application Support root and `paths.json`, the local download staging/cache directories, the dev fallbacks (`<repo>/data`, `backend/models`), which files are safe to delete and what each admin/reset endpoint removes, and the legacy LifeOS/LiveOS locations still honoured.

**Related docs.** [Desktop shell](04-desktop-shell.md) (who creates binaries, Firefly, logs, `paths.json` on the Electron side) · [Backend core and configuration](06-backend-core-and-configuration.md) (`core/paths.py`) · [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) · [Notes and vault files](09-notes-wikilinks-and-vault-files.md) · [Local models and inference](12-local-models-and-inference.md) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Graph storage](14-graph-storage-kuzu.md) · [Search indexes](15-search-indexes-qdrant-meilisearch.md) · [Finance (Firefly)](17-finance-firefly.md) · [Logging](23-logging-and-observability.md) · [Configuration reference](21-configuration-reference.md) · [Packaging](05-packaging-build-and-release.md) · [Development guide](27-development-guide.md).

## 1. Roots and how they are resolved

Orb has three writable roots plus one bootstrap file. Resolution order is implemented in `backend/app/core/paths.py` (backend) and `desktop/paths.js` / `desktop/supervisor.js` (shell); they agree on the defaults.

| Root | Backend resolution (`core/paths.py`) | Shell default (`desktop/paths.js`) | Typical macOS value |
|---|---|---|---|
| **App Support root** | `_default_app_support()`: first of `[Orb, LifeOS, LiveOS]` under the OS base that already contains `paths.json`, else `Orb` | `appSupportRoot()` — same rule | `~/Library/Application Support/Orb/` |
| **`paths.json`** | `ORB_PATHS_FILE` / `LIVEOS_PATHS_FILE` env, else `<AppSupport>/paths.json` | `defaultPathsFile()` = `<AppSupport>/paths.json` | `~/Library/Application Support/Orb/paths.json` |
| **`DATA_DIR`** | `ORB_DATA_DIR` / `LIVEOS_DATA_DIR` / `DATA_DIR` env → `paths.json.data_dir` → `<repo>/data` | `paths.json.data_dir` → `<AppSupport>/data` (wizard default `defaultDataDir()`) | `~/Library/Application Support/Orb/data/` |
| **`MODELS_DIR`** | `ORB_MODELS_DIR` / `LIVEOS_MODELS_DIR` / `MODELS_DIR` env → `paths.json.models_dir` → `<repo>/backend/models` | `paths.json.models_dir` → `<AppSupport>/models` | `~/Library/Application Support/Orb/models/` |
| **Default vault** | `paths.json.default_vault_path` → `ORB_DEFAULT_VAULT` / `LIVEOS_DEFAULT_VAULT` → (registry) `DATA_DIR/vaults/default` | wizard "Notes folder" | user-chosen, e.g. `~/Documents/Orb Notes/` |
| **Download staging** | `local_download_staging_dir()`: `ORB_HF_STAGING` / `ORB_DOWNLOAD_STAGING` / `LIVEOS_*` env → per-OS cache dir | — | `~/Library/Caches/Orb/model-downloads/` |

OS bases: macOS `~/Library/Application Support`; Windows `%APPDATA%` (fallback `~/AppData/Roaming`); Linux `~/.config`. Staging: macOS `~/Library/Caches/Orb`, Windows `%LOCALAPPDATA%\Orb`, Linux `~/.cache/orb`.

Under the desktop app the supervisor injects `ORB_DATA_DIR`, `ORB_MODELS_DIR` and `ORB_PATHS_FILE` into the backend process, so the env branch always wins there; the `paths.json` branch serves a hand-started `uvicorn`. `settings.DATA_DIR` / `settings.MODELS_DIR` are snapshots taken at import; code that must see a Setup-time change calls `resolve_data_dir()` / `resolve_models_dir()` directly (registry, runtime config, manifest, logs via `resolve_logs_dir()` after `sync_settings_paths` + `reconfigure_logging`).

`ensure_data_layout()` (called on every registry connect, by `core/database.py` at import, and by `sync_settings_paths`) creates `kuzu/ qdrant/ meilisearch/ logs/ vaults/ bin/` under `DATA_DIR`. The shell additionally `mkdir`s `qdrant/`, `meilisearch/`, `bin/<triple>/`, `bin/.tmp/`, `firefly/…`, `logs/`.

## 2. `DATA_DIR` tree

```
DATA_DIR/                                   (e.g. ~/Library/Application Support/Orb/data)
├── orb.db                                  SQLite: notes, note_links, knowledge_bases, chat_conversations, chat_messages
├── runtime_config.json                     {"provider","model","ingestion_model","base_url","ai_setup_mode"} (MUTABLE_KEYS only)
├── meili_master_key                        random base64url key (mode 0600) or "orb-dev-key" for pre-hardening installs
├── kuzu/
│   ├── kuzu_graph                          default KB Kuzu database FILE
│   ├── kuzu_graph.wal                      its write-ahead log (may be absent)
│   └── <slug>/
│       ├── kuzu_graph                      non-default KB database file
│       └── kuzu_graph.wal
├── qdrant/                                 Qdrant storage root (QDRANT__STORAGE__STORAGE_PATH); also its cwd
│   ├── collections/<name>/…                node_cores, node_relationships, node_isolated_contexts, <slug>_node_cores, …
│   ├── aliases/, raft_state.json, …        Qdrant-internal
├── meilisearch/                            Meilisearch --db-path (data.ms layout: indexes/, tasks/, auth/, …)
├── vaults/
│   └── <slug>/                             Orb-provisioned vault (only when no explicit vault_path was given / legacy migration)
│       ├── attachments/                    uploads: <stem>-<8hex>.<ext>
│       ├── <Folder>/.keep                  empty-folder marker written by vault/mkdir
│       └── **/*.md                         note bodies (vault-relative path = notes.rel_path)
│   (the default KB vault is usually OUTSIDE DATA_DIR — wherever paths.json.default_vault_path points)
├── logs/
│   ├── api.log            errors.log        ingestion.log     multimedia.log   chat.log      (backend, RotatingFileHandler 10 MB × 5)
│   ├── database.log       graph.log         llm.log           retrieval.log    finance.log
│   ├── backend.log        frontend.log      firefly.log                                     (shell-captured stdio, append-only, unrotated)
│   └── *.log.1 … *.log.5                                                                  (rotated backups)
├── bin/
│   ├── .tmp/                               download scratch (archives, extracted trees) — transient
│   └── <triple>/                           macos-arm64 | macos-x86_64 | linux-x86_64 | linux-aarch64 | windows-amd64 | windows-arm64
│       ├── qdrant[.exe]
│       └── meilisearch[.exe]
└── firefly/                                HOME/USERPROFILE for the PHP process
    ├── runtime.json                        {password, api token, APP_KEY, userId, groupId, …} — secrets, owner-only
    ├── php/                                static-php-cli runtime (bin/php, bin/php7/lib/…, extensions)
    ├── app/                                Firefly III v6.6.6 Laravel app
    │   ├── .env                            generated (APP_KEY, APP_URL, DB_CONNECTION=sqlite, …)
    │   ├── .orb-firefly-version            marker used to decide re-seeding on upgrade
    │   └── storage/
    │       ├── database/firefly.sqlite     ALL finance data for ALL KBs (one user group per KB)
    │       ├── upload/                     Firefly attachments
    │       ├── oauth-private.key / oauth-public.key   Passport keys
    │       └── logs/, framework/…          Laravel state
    ├── .app-state-stash/                   temporary copy of storage/{database,upload,oauth-*} during an app upgrade
    └── .tmp/                               Firefly/PHP archive download + extract scratch — transient
```

Present under `DATA_DIR`: `credentials.enc` — cloud API keys encrypted with the OS keychain (Electron `safeStorage`), written `0600` via a temp-file rename by `desktop/credentials.js`. It is ciphertext only, and unreadable on another machine or when the keychain is locked (both degrade to "no keys" rather than an error). The backend never reads or writes this file; the shell decrypts and pushes the keys over localhost.

Not present under `DATA_DIR` (by design): model weights (`MODELS_DIR`), the app bundle, Node/Python runtimes, and **any plaintext secret** — `DATA_DIR` is commonly a synced folder (OneDrive/NAS), so a cleartext key here would leave the machine.

## 3. Path-by-path reference

Legend for "safe to delete?": **Yes** = regenerated or purely cache; **Yes (loses X)** = safe for the app but destroys that data; **No** = app will break or data loss with no regeneration.

| Path | Owner subsystem | Created when | Safe to delete? |
|---|---|---|---|
| `paths.json` (App Support) | wizard (`desktop/main.js`, atomic write) / `POST /api/v1/setup/paths` (`save_paths_file`, non-atomic) | first run (wizard) or Setup page | Yes (loses chosen dirs) — next launch shows the wizard again; data is still on disk but you must re-enter the same `data_dir`/`models_dir` to find it |
| `DATA_DIR/orb.db` | `core/database.py` (`create_all`), `kb_registry._connect` (DDL), `vault_watcher` engine | first backend start | **No** — loses all note metadata (ids, titles, dates, processing flags), KB registry rows (non-default KBs become unreachable even though their folders remain), wikilink edges, chat history. Vault `.md` files survive; a fresh DB re-adopts them as new notes via `sync_vault_notes` (default KB only) |
| `DATA_DIR/runtime_config.json` | `core/runtime_config.py`; wizard merges `ai_setup_mode` | first Settings/Setup save or wizard | Yes — falls back to `.env`/defaults (provider/model/ai_setup_mode revert) |
| `DATA_DIR/meili_master_key` | `desktop/supervisor.js resolveMeiliMasterKey` | first supervised launch | Yes **only together with `meilisearch/`** — deleting the key alone on an install with existing Meili data reverts to `orb-dev-key`, which will not open indexes created under the random key |
| `DATA_DIR/kuzu/kuzu_graph[.wal]` | `GraphService` (default KB) | first `.graph` access (any graph/ingest/chat route or admin reset) | Yes (loses the default KB's entity graph) — recreated empty with schema; re-ingest all notes to rebuild. Delete only while the backend is stopped |
| `DATA_DIR/kuzu/<slug>/` | `kb_registry.create_kb` (dir) / `GraphService` (file) | KB creation / first graph access | Yes (loses that KB's graph) — same caveats; the registry row keeps pointing at it and Kuzu recreates the file |
| `DATA_DIR/qdrant/` | Qdrant process (spawned by the shell) | first supervised launch | Yes (loses all vectors for all KBs) — `QdrantService._ensure_collections` recreates empty collections at boot; re-ingest to repopulate. Backend must be restarted so per-KB services re-run `_ensure_collections` |
| `DATA_DIR/meilisearch/` | Meilisearch process | first supervised launch | Yes (loses keyword indexes for all KBs) — recreated on boot; delete `meili_master_key` at the same time so a fresh random key is generated (see above) |
| `DATA_DIR/vaults/<slug>/` | `ensure_vault` via `create_kb`/legacy migration/default fallback | KB creation | **No** unless you mean to delete the notes — this *is* the user's content for that KB. Deleting the KB via the API removes it; deleting by hand leaves an orphan registry row |
| `<vault>/attachments/` | `ensure_vault`, uploads | vault creation | No (loses uploaded files; markdown links dangle) |
| `<vault>/**/.keep` | `POST /api/v1/vault/mkdir` | folder creation | Yes — only keeps empty folders alive |
| `DATA_DIR/logs/*.log` | backend `core/log.py` (rotating), shell `serviceLogPath` (append) | first start | Yes — recreated; the shell's `backend.log`/`frontend.log`/`firefly.log` grow unbounded, so periodic deletion is reasonable |
| `DATA_DIR/bin/<triple>/{qdrant,meilisearch}` | `desktop/download-binaries.js ensureBinaries` | first launch (splash "Downloading…"), or `npm run prefetch-binaries` | Yes — re-downloaded from GitHub releases on next launch (needs network); fallback lookup also checks `<repo>/desktop/binaries/<triple>/` |
| `DATA_DIR/bin/.tmp/` | `ensureBinaries` | during downloads | Yes — scratch |
| `DATA_DIR/firefly/php/` | `firefly-runtime.js ensurePhpRuntime` | first launch | Yes — re-downloaded/re-seeded from bundled resources or GitHub (static-php-cli `PHP_BIN_VERSION`) |
| `DATA_DIR/firefly/app/` (except `storage/`) | `ensureFireflyApp` | first launch / version change (`.orb-firefly-version`) | Yes — re-seeded; `storage/` is stashed and restored across upgrades |
| `DATA_DIR/firefly/app/storage/database/firefly.sqlite`, `storage/upload/`, `oauth-*.key` | Firefly III (migrations, `db:seed`, Passport) | first launch | **No** — all finance data for every KB. The KB↔group mapping in `orb.db` (`firefly_group_id`) would then point at missing groups |
| `DATA_DIR/firefly/app/.env`, `DATA_DIR/firefly/runtime.json` | `firefly-runtime.js` | first launch | Partially — `.env` is regenerated from `runtime.json`; deleting `runtime.json` loses the API token and `APP_KEY` (encrypted Firefly columns become unreadable) → treat as **No** unless `firefly.sqlite` is deleted too |
| `DATA_DIR/firefly/.tmp/`, `.app-state-stash/` | `firefly-runtime.js` | during install/upgrade | Yes when no upgrade is in progress |
| `MODELS_DIR/manifest.json` | `local_models.save_manifest` | first GGUF download / model selection | Yes (loses selection) — `gguf_paths_if_present` falls back to the pinned default filenames under `gguf/`; Setup must re-select; `runtime` section is rewritten on next load |
| `MODELS_DIR/gguf/*.gguf` | `local_models.ensure_gguf` | Setup "Download models" | Yes — re-downloaded (multi-GB) |
| `MODELS_DIR/florence-2-large/`, `whisper-large-v3-turbo/`, `marlin-2b/` | `multimodal_models.ensure_hf_snapshot` | Setup / `start-multimodal-services` | Yes — re-downloaded (Marlin may be skipped if gated) |
| `~/Library/Caches/Orb/model-downloads/` | `local_download_staging_dir` | first download onto a network volume | Yes — pure staging |
| `<repo>/data/` | dev fallback `DATA_DIR` | running the backend without `paths.json`/env | Yes in dev (it is the dev instance's data) |
| `<repo>/backend/models/` | dev fallback `MODELS_DIR` | as above | Yes in dev |
| `<repo>/data/kb_registry.json.migrated` | `KBRegistry._load` after migrating the pre-SQLite registry | once | Yes |

## 4. `MODELS_DIR` tree and `manifest.json`

```
MODELS_DIR/                                 (e.g. ~/Library/Application Support/Orb/models)
├── manifest.json                           model bookkeeping (schema below)
├── gguf/
│   ├── google_gemma-4-E4B-it-Q4_K_M.gguf   chat (default CHAT_MODEL_ID; Setup may pick another catalog entry)
│   ├── Qwen3-Embedding-0.6B-Q8_0.gguf      embeddings (EMBED_MODEL_ID default; 4B/8B variants selectable)
│   ├── Qwen3-Reranker-0.6B.Q4_K_M.gguf     cross-encoder reranker (RERANK_MODEL_ID default)
│   ├── <other catalog GGUFs>.gguf          any additional chat models downloaded (per-KB pins can only use these)
│   └── <name>.gguf.partial                 in-flight download (only when MODELS_DIR is local; else staged elsewhere)
├── florence-2-large/                       HF snapshot of microsoft/Florence-2-large (settings.MODEL_FLORENCE_LOCAL)
├── whisper-large-v3-turbo/                 HF snapshot of openai/whisper-large-v3-turbo (MODEL_WHISPER_LOCAL)
└── marlin-2b/                              HF snapshot of lunahr/Marlin-2B-ungated (MODEL_MARLIN_LOCAL); may be absent if gated/skipped
```

- GGUF filenames are the last path segment of the catalog id `org/repo/file.gguf` (`ensure_gguf` → `MODELS_DIR/gguf/<file>`); a download counts as complete only when the size exceeds `_min_expected_gguf_bytes` for that id. Downloads go to `<file>.partial` and are placed atomically; when `MODELS_DIR` is on a network volume (`looks_like_network_volume`: `/Volumes/*` except the system disk on macOS, `/mnt`, `/media`, `/run/user` on Linux) the `.partial` lives in the staging dir (§6) and is moved in afterwards.
- HF snapshot directories are what `huggingface_hub.snapshot_download(local_dir=…)` produces (config + weights + tokenizer/processor files, plus a `.cache/` subfolder that Orb removes when staging). `is_hf_snapshot_ready(dir)` = has `config.json`/`model_index.json`/`preprocessor_config.json` **and** weights (`*.safetensors|bin|pt|pth|gguf|onnx`, or > 50 MB total). `GET /api/v1/setup/status.multimodal_ready` is true only when all three are ready.
- The shell checks `dirHasGguf(models_dir)` at boot to decide whether to open `/setup` instead of `/`.

### `manifest.json` schema (`local_models.load_manifest` / `save_manifest`, pretty-printed JSON)

```json
{
  "gguf": {
    "google_gemma-4-E4B-it-Q4_K_M.gguf": {
      "source": "bartowski/google_gemma-4-E4B-it-GGUF/google_gemma-4-E4B-it-Q4_K_M.gguf",
      "path": "/…/models/gguf/google_gemma-4-E4B-it-Q4_K_M.gguf",
      "bytes": 2837000000
    }
  },
  "selection": {
    "chat_id": "<catalog id>", "embed_id": "<catalog id>", "reranker_id": "<catalog id>",
    "embedding_dims": 1024,
    "chat_path": "/abs/…gguf", "embed_path": "/abs/…gguf", "reranker_path": "/abs/…gguf"
  },
  "runtime": {
    "engine": "llama-cpp-python", "backend": "metal|cuda|vulkan|cpu",
    "chat": "/abs/…gguf", "embed": "/abs/…gguf", "exclusive": true
  }
}
```

| Section | Written by | Read by |
|---|---|---|
| `gguf.<file>` | `ensure_gguf` after each successful download | informational |
| `selection` | `save_selection(chat_id, embed_id, reranker_id, embedding_dims)` (Setup "select chat model"), and the download flow which adds `*_path` | `gguf_paths_if_present` (needs `chat_path`+`embed_path` existing and chat > 1 MB), `reranker_gguf_path`, `QdrantService.__init__` (`selection.embedding_dims` overrides `settings.EMBEDDING_DIMENSIONS` before creating collections), `model_catalog.recommend_stack` (remembers `chat_id`), `sync_embedding_infrastructure` |
| `runtime` | `LocalModelRuntime.load()` after a successful chat/embed load | diagnostics (`/setup/status`, logs) |

If `selection` is missing/broken, the code falls back to the pinned default filenames under `gguf/` (`CHAT_MODEL_ID`/`EMBED_MODEL_ID`/`RERANK_MODEL_ID`, overridable by env). The manifest is the *only* record of `embedding_dims`; changing embed models must go through `save_selection` so Qdrant collections are recreated consistently (non-empty collections at the old dim are refused — see [15](15-search-indexes-qdrant-meilisearch.md)).

## 5. Application Support root and `paths.json`

```
~/Library/Application Support/Orb/          (Windows: %APPDATA%\Orb ; Linux: ~/.config/Orb)
├── paths.json                              bootstrap: where everything else is
├── data/                                   default DATA_DIR (wizard default; user may choose elsewhere)
└── models/                                 default MODELS_DIR (wizard default; often moved to an external disk)
```

`paths.json`:

```json
{
  "data_dir": "/abs/path",
  "models_dir": "/abs/path",
  "default_vault_path": "/abs/path",      // optional; default KB vault
  "ai_setup_mode": "none|local|cloud|hybrid"   // optional
}
```

- Written by the wizard (`desktop/main.js`, atomic `paths.json.tmp` → rename; validates absolute paths) and by `POST /api/v1/setup/paths` (`core/paths.save_paths_file`, plain `write_text`, `expanduser().resolve()`, preserves omitted `default_vault_path`/`ai_setup_mode`).
- Read by the shell at every launch (`needsWizard` when missing or invalid) and by the backend via `load_paths_file()` (cached in `_PATHS_CACHE` until `clear_paths_cache()`/`save_paths_file`).
- Electron's own `userData` (`app.getPath("userData")`, Chromium caches, window state) also lives in this App Support folder, alongside `paths.json`; those files are Electron-managed and safe to delete.
- The `[Orb, LifeOS, LiveOS]` candidate list means an old install's `paths.json` under `LifeOS/` keeps being used (and written) until it is moved; there is no automatic migration of the folder itself.

## 6. Caches and staging directories

| Path | Purpose | Lifecycle |
|---|---|---|
| `~/Library/Caches/Orb/model-downloads/` (macOS), `%LOCALAPPDATA%\Orb\model-downloads`, `~/.cache/orb/model-downloads` | `local_download_staging_dir()`: local-SSD staging for GGUF `.partial` files and HF snapshot temp dirs (`<label>-XXXX/` via `mkdtemp`) when `MODELS_DIR` is on a network volume; rationale: HF/GGUF downloads directly onto SMB/NAS are unreliable | files are moved/copied into `MODELS_DIR` then removed; a crash can leave `.partial`/temp dirs behind — safe to delete |
| `DATA_DIR/bin/.tmp/` | engine archive download + extraction scratch | removed per download; safe to delete |
| `DATA_DIR/firefly/.tmp/` | PHP runtime and Firefly release archives + extraction | safe to delete when not installing |
| `DATA_DIR/firefly/.app-state-stash/` | copy of `storage/{database,upload,oauth-*.key}` while the Firefly app dir is rebuilt on upgrade | exists only mid-upgrade; if present after a crash it may be the *only* copy of finance data — restore before deleting |
| `<repo>/desktop/data/` | `prefetch-binaries.js` default `ORB_DATA_DIR` for CI warm-ups | dev only |
| `<repo>/backend/tests/benchmark/.cache/`, `results/`, `*_notes/` | benchmark artefacts (gitignored) | dev only |
| Hugging Face hub cache (`~/.cache/huggingface/`) | not used for model files (Orb passes `local_dir`), but `huggingface_hub` may still write token/metadata there | external |
| `~/Library/Caches/Orb` on the Electron side (`app.getPath("cache")`) | Chromium cache | Electron-managed |

## 7. Dev fallbacks and repo-relative artefacts

When neither env vars nor `paths.json` exist (e.g. `uvicorn app.main:app` in `backend/` on a fresh clone):

| Item | Dev location | Note |
|---|---|---|
| `DATA_DIR` | `<repo>/data/` (gitignored via `/data`) | full layout is created here: `orb.db`, `kuzu/`, `vaults/default/`, `logs/`… |
| `MODELS_DIR` | `<repo>/backend/models/` (gitignored `backend/models/`) | `manifest.json`, `gguf/`, snapshots |
| `runtime_config.json` | `<repo>/data/runtime_config.json` (explicit fallback in `runtime_config._data_path`) | |
| Legacy registry | `<repo>/data/kb_registry.json` → renamed `.json.migrated` after import | always repo-relative, even in packaged builds |
| Legacy Kuzu | `<repo>/data/kuzu_graph[.wal]` → moved to `<DATA_DIR>/kuzu/kuzu_graph` by `GraphService._migrate_legacy_db_path` when the target does not exist | |
| Engine binaries | `<repo>/desktop/binaries/<triple>/` or `<repo>/desktop/binaries/` (fallback lookup in `supervisor.resolveBinary`) | `desktop/binaries/README.md` |
| Firefly seed | `<repo>/desktop/resources/firefly/` (gitignored build output) | used by `firefly-runtime.js` as `bundledRoot` in dev |
| Backend logs (legacy) | `backend/logs/` is gitignored but no longer written; logs go to `DATA_DIR/logs` | |
| `.env` | `<repo>/backend/.env` — ports and provider defaults for contributors; API keys here are only a **seed** for non-desktop runs (end users set keys in Settings). **Never copied into `DATA_DIR`** | `runtime_config.json` intentionally excludes secrets |
| `credentials.enc` | `<DATA_DIR>/credentials.enc` — keychain-encrypted cloud API keys, `0600` | written by the shell, never by the backend |

Because `settings.DATA_DIR` is evaluated at import, importing `app.core.config` (or any service) in a test without `ORB_DATA_DIR` creates `<repo>/data/` and `orb.db` as a side effect.

## 8. What admin / reset / delete operations remove

| Operation | `orb.db` | Vault files | Kuzu | Qdrant | Meili | Firefly | Models / config |
|---|---|---|---|---|---|---|---|
| `DELETE /api/v1/notes/{id}` / `batch-delete` | note row + its `note_links` | the `.md` + every attachment the body referenced | note node + orphaned entity nodes | same nodes | same nodes | — | — |
| `POST /api/v1/vault/delete` | bodies rewritten (links stripped) | the attachment | — | — | — | — | — |
| `POST /api/v1/admin/reset-ingestion-data?kb=` | `processed=failed=False` on all notes of the KB | — | `MATCH (n:Node) DETACH DELETE n` (all nodes of that KB's graph, in background) | delete + recreate the KB's 3 collections | delete + recreate the KB's index | — | — |
| `POST /api/v1/kb/empty?kb=` | all notes + `note_links` of the KB (row kept) | **entire vault folder contents** (no external-folder guard), then `attachments/` recreated | wipe all nodes | reset | reset | destroy the KB's user group (ledger data for that KB) and detach mapping | — |
| `DELETE /api/v1/kb/{id}` | notes + `note_links` + the `knowledge_bases` row (**chat rows remain orphaned**) | `rmtree` only if under `DATA_DIR/vaults/` | unlink `kuzu/<slug>/kuzu_graph` + `.wal` (only under `DATA_DIR/kuzu`) | delete 3 collections | delete index | destroy group + detach | — |
| `POST /api/v1/kb/delete-non-default` | as above for every non-default KB | as above | as above | as above | as above | as above | — |
| `POST /api/v1/finance/reset-administration?kb=` | `firefly_group_*` columns detached/reassigned | — | — | — | — | destroys and recreates the KB's Firefly group (see [17](17-finance-firefly.md)) | — |
| `POST /api/v1/setup/paths` | (may re-point default KB `vault_path`) | — | — | — | — | — | rewrites `paths.json`; merges `ai_setup_mode` into `runtime_config.json`; re-targets `settings.*` and logs |
| Settings "runtime LLM" save (`api/settings.py`) | — | — | — | — | — | — | `runtime_config.json` |
| Setup model selection | — | — | — | Qdrant collections may be recreated if empty and dims changed | — | — | `manifest.json.selection` |
| Wizard re-run (delete `paths.json`) | — | — | — | — | — | — | new `paths.json`; existing dirs untouched |

Nothing in the app deletes `orb.db`, `meili_master_key`, `qdrant/`, `meilisearch/` storage roots, `bin/`, `firefly/php`, `firefly/app`, or `MODELS_DIR` contents; those are manual operations (§3 for safety).

## 9. Legacy locations and migrations (LifeOS / LiveOS / Docker era)

| Legacy artefact | Current handling |
|---|---|
| App Support `LifeOS/` or `LiveOS/` with `paths.json` | still selected by both shell and backend if `Orb/paths.json` does not exist; `data/`, `models/` inside it keep working. No rename is performed. |
| Env vars `LIVEOS_DATA_DIR`, `LIVEOS_MODELS_DIR`, `LIVEOS_PATHS_FILE`, `LIVEOS_DEFAULT_VAULT`, `LIVEOS_HF_STAGING`, `LIVEOS_DOWNLOAD_STAGING`, `LIVEOS_PACKAGED`, `LIVEOS_RESOURCES`, `LIVEOS_ROOT`, `LIVEOS_PYTHON`, `LIVEOS_NODE`, `LIVEOS_FRONTEND_DEV` | accepted as second-priority aliases of the `ORB_*` names (`_env_first` / `envFirst`). |
| `DATA_DIR` env (bare) / `MODELS_DIR` env (bare) | third-priority aliases (Docker-era). |
| `<repo>/data/kb_registry.json` | migrated into `knowledge_bases` once, renamed `.migrated`. |
| `<repo>/data/kuzu_graph` (Kuzu file at data root) | moved to `<DATA_DIR>/kuzu/kuzu_graph` by `GraphService`. |
| `…/kuzu/<slug>` stored as a **directory** path | healed to `…/kuzu/<slug>/kuzu_graph` by `normalize_kuzu_path` (registry load, `get_kb`, cleanup). |
| `notes.content` bodies in SQLite | still read as fallback when the vault file is missing/empty; never written. |
| `knowledge_bases.typesense_collection` | column name retained; holds the Meilisearch index name (`orb_nodes` default; ORM synonym `meili_index`). `TYPESENSE_*` env vars alias `MEILI_*`. |
| Meili master key `orb-dev-key` | used automatically when `meilisearch/` already has data but no `meili_master_key` file exists. |
| `localStorage` keys `lifeos_current_kb` / `liveos_current_kb` | migrated to `orb_current_kb` on first read. |
| `DATABASE_BACKEND=postgres` + `DATABASE_TRANSACTION_POOLER_URL` | contributor/Docker path for the async ORM only; `knowledge_bases`, the watcher and `sqlite_url()` remain on `orb.db`. |
| Alembic migrations (`backend/alembic/versions/d4f891a2b5c3_add_kb_id_to_notes.py`) | removed; schema is `create_all` + ad-hoc `ALTER TABLE … ADD COLUMN` (`_ensure_firefly_columns`, LLM override columns) + `CREATE INDEX IF NOT EXISTS ix_notes_kb_rel_path`. |
| RustFS / S3 uploads | replaced by `local_storage` → `<vault>/attachments/`; `vault_rel_from_url` still maps any `…/attachments/<name>` URL for old bodies. |

## 10. Backup, move and recovery guidance

- **Minimal backup of user content**: every vault folder (default vault path from `GET /api/v1/setup/status.active_vault_path` or `GET /api/v1/kb[].vault_path`) + `DATA_DIR/orb.db` + `DATA_DIR/firefly/app/storage/` + `DATA_DIR/firefly/runtime.json` + `paths.json`. Graph/vector/keyword stores and models are reproducible (re-ingest / re-download).
- **Full cold backup**: stop Orb, copy `DATA_DIR`, `MODELS_DIR`, `paths.json`, and any external vault folders. Qdrant/Meili/Kuzu files are not safe to copy while running.
- **Moving `DATA_DIR`**: quit Orb, move the folder, edit `paths.json.data_dir` (or delete `paths.json` and re-run the wizard pointing at the new location). `knowledge_bases.vault_path`/`kuzu_path` are **absolute**, so vaults/Kuzu under the old `DATA_DIR/vaults` and `DATA_DIR/kuzu` must be re-pointed (no tool for this; edit the rows, or use `POST /api/v1/setup/paths` for the default vault only). Bodies use relative `rel_path`, so moving a vault folder as a whole preserves notes.
- **Moving `MODELS_DIR`**: move the folder and update `paths.json.models_dir`; `manifest.json.selection.*_path` and `runtime.*` hold absolute paths and will fail `gguf_paths_if_present` until Setup re-selects (the fallback by default filename still works when the defaults were chosen).
- **Recovering from a lost `orb.db`**: start Orb; the default KB re-adopts its vault's `.md` files (new ids, `created_at` = now, titles from filenames). Non-default KBs must be re-created via `POST /api/v1/kb` with the same `vault_path` (a new slug/id means new Kuzu/Qdrant/Meili names; the old stores are orphaned). Chat history and Firefly mappings are lost; Firefly groups still exist inside `firefly.sqlite` but are no longer attached.
- **Recovering indexes**: `POST /api/v1/admin/reset-ingestion-data?kb=` then `POST /api/v1/admin/reingest-all` (or `reingest-vault`) per KB.

## 11. Gotchas

- `paths.json` is *not* inside `DATA_DIR`; wiping `DATA_DIR` leaves the app pointing at an empty folder that it silently recreates (no wizard) — it looks like data loss but the wizard-chosen vault outside `DATA_DIR` is intact.
- The default KB's vault is usually **outside** `DATA_DIR/vaults/`; only non-default/legacy KBs live there. Do not assume all notes are under `DATA_DIR`.
- `kuzu_graph` is a **file**; `kuzu/<slug>/` is a folder containing that file. Tools that expect a directory database (older Kuzu) will misread it.
- Qdrant and Meilisearch are single servers for all KBs; per-KB isolation is by collection/index *name*. Deleting `qdrant/` or `meilisearch/` affects every KB.
- `meili_master_key` and `meilisearch/` must be deleted **together**; deleting only the key on an old install falls back to `orb-dev-key`, deleting only the data keeps a random key that then opens an empty store (fine).
- Shell-captured logs (`backend.log`, `frontend.log`, `firefly.log`) are never rotated; backend component logs are (10 MB × 5). `errors.log` aggregates ERROR+ from all components.
- `runtime_config.json` holds only `provider`, `model`, `ingestion_model`, `base_url`, `ai_setup_mode`; unknown keys are dropped on load and save.
- `firefly/runtime.json` and `firefly/app/.env` contain secrets (API token, `APP_KEY`, password) and are written owner-only; `paths.json` and `orb.db` contain absolute paths and Firefly group ids but no secrets.
- `MODELS_DIR` on SMB/NAS triggers staging; the staging dir is created eagerly (`mkdir`) even when unused.
- `ensure_data_layout` is called at import of `core/database.py`, so simply importing the backend creates the `DATA_DIR` skeleton.
- `DATA_DIR/vaults/<slug>` is created even when `POST /api/v1/kb` was given an explicit `vault_path`? **No** — only when no path is supplied (route requires one), or during legacy JSON migration; an empty `vaults/` folder is therefore normal.
- `manifest.json.gguf` entries are never pruned when files are deleted; treat it as informational.
