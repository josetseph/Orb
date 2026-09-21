# Data Directory Layout

**What this covers.** Everything Orb writes outside the application bundle: the exact on-disk tree of `DATA_DIR` (SQLite `orb.db`, `runtime_config.json`, `meili_master_key`, per-KB Kuzu files, Qdrant and Meilisearch storage, vaults, logs, downloaded engine binaries, the embedded Firefly III PHP runtime and app), the tree of `MODELS_DIR` (GGUFs, `manifest.json` and its schema, the Qwen3-ASR/Marlin snapshots, the vision projector), the Application Support root and `paths.json`, the local download staging/cache directories, the dev fallbacks (`<repo>/data`, `backend/models`), which files are safe to delete and what each admin/reset endpoint removes, and the legacy LifeOS/LiveOS locations (no longer honoured).

**Related docs.** [Desktop shell](04-desktop-shell.md) (who creates binaries, Firefly, logs, `paths.json` on the shell/runtime side) · [Backend core and configuration](06-backend-core-and-configuration.md) (`core/paths.py`) · [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) · [Notes and vault files](09-notes-wikilinks-and-vault-files.md) · [Local models and inference](12-local-models-and-inference.md) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Graph storage](14-graph-storage-kuzu.md) · [Search indexes](15-search-indexes-qdrant-meilisearch.md) · [Finance (Firefly)](17-finance-firefly.md) · [Logging](23-logging-and-observability.md) · [Configuration reference](21-configuration-reference.md) · [Packaging](05-packaging-build-and-release.md) · [Development guide](27-development-guide.md).

## 1. Roots and how they are resolved

Orb has three writable roots plus one bootstrap file. Resolution order is implemented in `backend/app/core/paths.py` (backend and desktop runtime) and `desktop/src-tauri/src/runtime.rs` (shell); they agree on the defaults.

| Root | Backend resolution (`core/paths.py`) | Shell default (`src-tauri/src/runtime.rs`) | Typical macOS value |
|---|---|---|---|
| **App Support root** | `_default_app_support()`: `Orb` under the OS base | `app_support_root()` — same rule | `~/Library/Application Support/Orb/` |
| **`paths.json`** | `ORB_PATHS_FILE` env, else `<AppSupport>/paths.json` | `paths_file()` = `ORB_PATHS_FILE` or `<AppSupport>/paths.json` | `~/Library/Application Support/Orb/paths.json` |
| **`DATA_DIR`** | `ORB_DATA_DIR` / `DATA_DIR` env → `paths.json.data_dir` → `<repo>/data` | `paths.json.data_dir` → `<AppSupport>/data` (setup default `data_dir()`) | `~/Library/Application Support/Orb/data/` |
| **`MODELS_DIR`** | `ORB_MODELS_DIR` / `MODELS_DIR` env → `paths.json.models_dir` → `<repo>/backend/models` | `paths.json.models_dir` → `<AppSupport>/models` (`default_models_dir()`) | `~/Library/Application Support/Orb/models/` |
| **Default vault** | `paths.json.default_vault_path` → `ORB_DEFAULT_VAULT` → (registry) `DATA_DIR/vaults/default` | setup page "Notes folder" | user-chosen, e.g. `~/Documents/Orb Notes/` |
| **Download staging** | `local_download_staging_dir()`: `ORB_HF_STAGING` / `ORB_DOWNLOAD_STAGING` env → per-OS cache dir | — | `~/Library/Caches/Orb/model-downloads/` |

OS bases: macOS `~/Library/Application Support`; Windows `%APPDATA%` (fallback `~/AppData/Roaming`); Linux `~/.config`. Staging: macOS `~/Library/Caches/Orb`, Windows `%LOCALAPPDATA%\Orb`, Linux `~/.cache/orb`.

`DATA_DIR` must sit on local disk. `desktop_runtime.main()` prints `[desktop] WARNING: data dir <path> is inside a cloud-synced folder; move it to local disk (Settings -> Storage)` when any path component is `CloudStorage`, `Mobile Documents`, `Dropbox` or `Google Drive`: evicted Files-On-Demand placeholders block reads, and sync clients writing under a running engine corrupt SQLite, Kuzu and Qdrant. The default (`<AppSupport>/data`) is local. Only a vault may live in a synced folder, pinned "Always keep on this device".

Under the desktop app nothing injects `ORB_DATA_DIR`/`ORB_MODELS_DIR`: the runtime and the API resolve `paths.json` themselves through `core/paths.py` (the shell only reads it for the log directory), so the `paths.json` branch is the normal desktop path and env overrides are for dev. `settings.DATA_DIR` / `settings.MODELS_DIR` are snapshots taken at import; code that must see a Setup-time change calls `resolve_data_dir()` / `resolve_models_dir()` directly (registry, runtime config, manifest, logs via `resolve_logs_dir()` after `sync_settings_paths` + `reconfigure_logging`).

`ensure_data_layout()` (called on every registry connect, by `core/database.py` at import, and by `sync_settings_paths`) creates `kuzu/ qdrant/ meilisearch/ logs/ vaults/ bin/` under `DATA_DIR`. `desktop_runtime.py` additionally `mkdir`s `qdrant/`, `meilisearch/`, `bin/<triple>/`, `bin/.tmp/`, `firefly/…`, `logs/`.

## 2. `DATA_DIR` tree

```
DATA_DIR/                                   (e.g. ~/Library/Application Support/Orb/data)
├── orb.db                                  SQLite: notes, note_links, knowledge_bases, chat_conversations, chat_messages
├── runtime_config.json                     {"provider","model","base_url", llama.cpp knobs} (MUTABLE_KEYS only)
├── meili_master_key                        random base64url key (mode 0600) or "orb-dev-key" for pre-hardening installs
├── .stores-migrated-v1-<kb_id>             marker per KB: main._migrate_stores ran (see §9.1)
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
│       ├── attachments/<note folder>/      uploads: <stem>-<8hex>.<ext>, grouped by the owning note's folder — the ONLY place non-.md files live; notes link to them vault-root-relative (attachments/…)
│       ├── <Folder>/.keep                  empty-folder marker written by vault/mkdir
│       ├── .orb/migrated-v4                marker: vault_sync.migrate_vault_files ran for this vault (see §9.1; v1/v2 markers from earlier runs may sit beside it)
│       └── **/*.md                         note bodies (vault-relative path = notes.rel_path)
│   (the default KB vault is usually OUTSIDE DATA_DIR — wherever paths.json.default_vault_path points)
├── logs/
│   ├── api.log            errors.log        ingestion.log     multimedia.log   chat.log      (backend, RotatingFileHandler 10 MB × 5)
│   ├── database.log       graph.log         llm.log           retrieval.log    finance.log
│   ├── backend.log        qdrant.log        meilisearch.log   firefly.log      multimodal.log (process stdio; each rolls to `.log.1` past 10 MB at spawn; backend.log = runtime + API, written by the shell)
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

Not present under `DATA_DIR` (by design): model weights (`MODELS_DIR`), the app bundle, the Python runtime, and **cloud API keys** — those live in the OS keychain (`keyring`, service `Orb`), never on disk under Orb's control, so even a `DATA_DIR` a user has (against §1) put in a synced folder leaks nothing. The exceptions are the Meili master key and Firefly's `runtime.json`/`.env`, which are per-install secrets written owner-only.

## 3. Path-by-path reference

Legend for "safe to delete?": **Yes** = regenerated or purely cache; **Yes (loses X)** = safe for the app but destroys that data; **No** = app will break or data loss with no regeneration.

| Path | Owner subsystem | Created when | Safe to delete? |
|---|---|---|---|
| `paths.json` (App Support) | first-run setup page (`save_setup` in `src-tauri/src/commands.rs`, atomic write) / `POST /api/v1/setup/paths` (`save_paths_file`, non-atomic) | first run or Setup page | Yes (loses chosen dirs) — next launch shows the setup page again; data is still on disk but you must re-enter the same `data_dir`/`models_dir` to find it |
| `DATA_DIR/orb.db` | `core/database.py` (`create_all`), `kb_registry._connect` (DDL), `vault_watcher` engine | first backend start | **No** — loses all note metadata (ids, titles, dates, processing flags), KB registry rows (non-default KBs become unreachable even though their folders remain), wikilink edges, chat history. Vault `.md` files survive; a fresh DB re-adopts them as new notes via `sync_vault_notes` (default KB only) |
| `DATA_DIR/runtime_config.json` | `core/runtime_config.py` | first Settings save | Yes — falls back to defaults (provider/model revert) |
| `DATA_DIR/meili_master_key` | `desktop_runtime.resolve_meili_master_key` | first desktop launch | Yes **only together with `meilisearch/`** — deleting the key alone on an install with existing Meili data reverts to `orb-dev-key`, which will not open indexes created under the random key |
| `DATA_DIR/kuzu/kuzu_graph[.wal]` | `GraphService` (default KB) | first `.graph` access (any graph/ingest/chat route or admin reset) | Yes (loses the default KB's entity graph) — recreated empty with schema; re-ingest all notes to rebuild. Delete only while the backend is stopped |
| `DATA_DIR/kuzu/<slug>/` | `kb_registry.create_kb` (dir) / `GraphService` (file) | KB creation / first graph access | Yes (loses that KB's graph) — same caveats; the registry row keeps pointing at it and Kuzu recreates the file |
| `DATA_DIR/qdrant/` | Qdrant process (spawned by the runtime) | first desktop launch | Yes (loses all vectors for all KBs) — `QdrantService._ensure_collections` recreates empty collections at boot; re-ingest to repopulate. Backend must be restarted so per-KB services re-run `_ensure_collections` |
| `DATA_DIR/meilisearch/` | Meilisearch process | first desktop launch | Yes (loses keyword indexes for all KBs) — recreated on boot; delete `meili_master_key` at the same time so a fresh random key is generated (see above) |
| `DATA_DIR/vaults/<slug>/` | `ensure_vault` via `create_kb`/legacy migration/default fallback | KB creation | **No** unless you mean to delete the notes — this *is* the user's content for that KB. Deleting the KB via the API removes it; deleting by hand leaves an orphan registry row |
| `<vault>/attachments/` (+ `<note folder>/` subfolders) | `ensure_vault`, uploads (`store_upload`), the v3 sweep (moves stray files in) | vault creation / first upload into a folder | No (loses every attachment; markdown links dangle). Everything non-`.md` lives here — `vault_ops.move_vault_file` refuses moves across the `attachments/` boundary |
| `<vault>/**/.keep` | `POST /api/v1/vault/mkdir` | folder creation | Yes — only keeps empty folders alive |
| `DATA_DIR/logs/*.log` | backend `core/log.py` (rotating); shell (`backend.log`, `runtime.rs::spawn`) and `desktop_runtime._spawn` (`qdrant.log`, `meilisearch.log`, `firefly.log`, `multimodal.log`) append; both keep one `.1` generation past 10 MB | first start | Yes — recreated |
| `DATA_DIR/boot-status.json` | `desktop_runtime.status()` (atomic rewrite) | every desktop launch | Yes — rewritten on next launch; `/admin/maintenance-status` surfaces it while not `Ready` |
| `DATA_DIR/bin/<triple>/{qdrant,meilisearch}` | `desktop_runtime.ensure_binaries` | first launch (status indicator "Downloading…") | Yes — re-downloaded from GitHub releases on next launch (needs network) |
| `DATA_DIR/bin/.tmp/` | `ensure_binaries` | during downloads | Yes — scratch |
| `DATA_DIR/firefly/php/` | `desktop_runtime.ensure_php_runtime` | first launch | Yes — re-downloaded/re-seeded from bundled resources or GitHub (static-php-cli `PHP_BIN_VERSION`) |
| `DATA_DIR/firefly/app/` (except `storage/`) | `desktop_runtime.ensure_firefly_app` | first launch / version change (`.orb-firefly-version`) | Yes — re-seeded; `storage/` is stashed and restored across upgrades |
| `DATA_DIR/firefly/app/storage/database/firefly.sqlite`, `storage/upload/`, `oauth-*.key` | Firefly III (migrations, `db:seed`, Passport) | first launch | **No** — all finance data for every KB. The KB↔group mapping in `orb.db` (`firefly_group_id`) would then point at missing groups |
| `DATA_DIR/firefly/app/.env`, `DATA_DIR/firefly/runtime.json` | `desktop_runtime.ensure_firefly_runtime` | first launch | Partially — `.env` is regenerated from `runtime.json`; deleting `runtime.json` loses the API token and `APP_KEY` (encrypted Firefly columns become unreadable) → treat as **No** unless `firefly.sqlite` is deleted too |
| `DATA_DIR/firefly/.tmp/`, `.app-state-stash/` | `desktop_runtime.py` | during install/upgrade | Yes when no upgrade is in progress |
| `MODELS_DIR/manifest.json` | `local_models.save_manifest` | first GGUF download / model selection | Yes (loses selection) — `gguf_paths_if_present` falls back to the pinned default filenames under `gguf/`; Setup must re-select; `runtime` section is rewritten on next load |
| `MODELS_DIR/gguf/*.gguf` | `local_models.ensure_gguf` | Setup "Download models" | Yes — re-downloaded (multi-GB) |
| `MODELS_DIR/qwen3-asr-1.7b/` (MLX) or `qwen3-asr-1.7b-hf/` (transformers), `marlin-2b/` | `multimodal_models.ensure_hf_snapshot` | Setup / `start-multimodal-services` | Yes — re-downloaded (Marlin may be skipped if gated) |
| `MODELS_DIR/gguf/mmproj-<chat stem>-f16.gguf` | `local_models.ensure_mmproj` (the `orb-mmproj` boot thread, or Setup) | first boot with a catalog chat model selected | Yes — re-fetched at next boot; without it the local model is text-only (`describe_image` raises "no vision projector") |
| `~/Library/Caches/Orb/model-downloads/` | `local_download_staging_dir` | first download onto a network volume | Yes — pure staging |
| `<repo>/data/` | dev fallback `DATA_DIR` | running the backend without `paths.json`/env | Yes in dev (it is the dev instance's data) |
| `<repo>/backend/models/` | dev fallback `MODELS_DIR` | as above | Yes in dev |

## 4. `MODELS_DIR` tree and `manifest.json`

```
MODELS_DIR/                                 (e.g. ~/Library/Application Support/Orb/models)
├── manifest.json                           model bookkeeping (schema below)
├── gguf/
│   ├── google_gemma-4-E4B-it-Q4_K_M.gguf   chat (default CHAT_MODEL_ID; Setup may pick another catalog entry)
│   ├── Qwen3-Embedding-0.6B-Q8_0.gguf      embeddings (EMBED_MODEL_ID default; 4B/8B variants selectable)
│   ├── Qwen3-Reranker-0.6B.Q4_K_M.gguf     cross-encoder reranker (RERANK_MODEL_ID default)
│   ├── <other catalog GGUFs>.gguf          any additional chat models downloaded (per-KB pins can only use these)
│   ├── mmproj-<chat stem>-f16.gguf         vision projector paired to a chat GGUF by name only (find_mmproj: mmproj-<stem>-*.gguf beside it)
│   └── <name>.gguf.partial                 in-flight download (only when MODELS_DIR is local; else staged elsewhere)
├── qwen3-asr-1.7b/  or  qwen3-asr-1.7b-hf/  Qwen3-ASR snapshot — MLX layout on Apple Silicon, transformers layout elsewhere (asr_engine picks; MODEL_ASR_LOCAL pins)
└── marlin-2b/                              HF snapshot of lunahr/Marlin-2B-ungated (MODEL_MARLIN_LOCAL); may be absent if gated/skipped
```

- GGUF filenames are the last path segment of the catalog id `org/repo/file.gguf` (`ensure_gguf` → `MODELS_DIR/gguf/<file>`); a download counts as complete only when the size exceeds `_min_expected_gguf_bytes` for that id. Downloads go to `<file>.partial` and are placed atomically; when `MODELS_DIR` is on a network volume (`looks_like_network_volume`: `/Volumes/*` except the system disk on macOS, `/mnt`, `/media`, `/run/user` on Linux) the `.partial` lives in the staging dir (§6) and is moved in afterwards.
- HF snapshot directories are what `huggingface_hub.snapshot_download(local_dir=…)` produces (config + weights + tokenizer/processor files, plus a `.cache/` subfolder that Orb removes when staging). `is_hf_snapshot_ready(dir)` = has `config.json`/`model_index.json`/`preprocessor_config.json` **and** weights (`*.safetensors|bin|pt|pth|gguf|onnx`, or > 50 MB total). `GET /api/v1/setup/status.multimodal_ready` is true only when both the ASR and the Marlin snapshot are ready.

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
├── data/                                   default DATA_DIR (setup default; user may choose elsewhere)
└── models/                                 default MODELS_DIR (setup default; often moved to an external disk)
```

`paths.json`:

```json
{
  "data_dir": "/abs/path",
  "models_dir": "/abs/path",
  "default_vault_path": "/abs/path"       // optional; default KB vault
}
```

- Written by the first-run setup page (`save_setup` in `src-tauri/src/commands.rs`, atomic tmp → rename; validates absolute paths) and by `POST /api/v1/setup/paths` (`core/paths.save_paths_file`, plain `write_text`, `expanduser().resolve()`, preserves an omitted `default_vault_path`).
- Read by the shell at every launch (`first_run()` when missing or invalid, unless `ORB_SKIP_WIZARD`) and by the backend via `load_paths_file()` (cached in `_PATHS_CACHE` until `save_paths_file`).
- Tauri's WebView data (`com.josetseph.orb` under the OS app-data dir: WebKit/WebView2 caches, `localStorage` for the UI origin) lives beside this folder; those files are WebView-managed and safe to delete (the UI loses `orb_current_kb` and similar conveniences).

## 6. Caches and staging directories

| Path | Purpose | Lifecycle |
|---|---|---|
| `~/Library/Caches/Orb/model-downloads/` (macOS), `%LOCALAPPDATA%\Orb\model-downloads`, `~/.cache/orb/model-downloads` | `local_download_staging_dir()`: local-SSD staging for GGUF `.partial` files and HF snapshot temp dirs (`<label>-XXXX/` via `mkdtemp`) when `MODELS_DIR` is on a network volume; rationale: HF/GGUF downloads directly onto SMB/NAS are unreliable | files are moved/copied into `MODELS_DIR` then removed; a crash can leave `.partial`/temp dirs behind — safe to delete |
| `DATA_DIR/bin/.tmp/` | engine archive download + extraction scratch | removed per download; safe to delete |
| `DATA_DIR/firefly/.tmp/` | PHP runtime and Firefly release archives + extraction | safe to delete when not installing |
| `DATA_DIR/firefly/.app-state-stash/` | copy of `storage/{database,upload,oauth-*.key}` while the Firefly app dir is rebuilt on upgrade | exists only mid-upgrade; if present after a crash it may be the *only* copy of finance data — restore before deleting |
| Hugging Face hub cache (`~/.cache/huggingface/`) | not used for model files (Orb passes `local_dir`), but `huggingface_hub` may still write token/metadata there | external |
| WebView cache for `com.josetseph.orb` (OS-managed location) | WebKit / WebView2 cache | WebView-managed |

## 7. Dev fallbacks and repo-relative artefacts

When neither env vars nor `paths.json` exist (e.g. `uvicorn app.main:app` in `backend/` on a fresh clone):

| Item | Dev location | Note |
|---|---|---|
| `DATA_DIR` | `<repo>/data/` (gitignored via `/data`) | full layout is created here: `orb.db`, `kuzu/`, `vaults/default/`, `logs/`… |
| `MODELS_DIR` | `<repo>/backend/models/` (gitignored `backend/models/`) | `manifest.json`, `gguf/`, snapshots |
| `runtime_config.json` | `<repo>/data/runtime_config.json` (explicit fallback in `runtime_config._data_path`) | |
| Legacy Kuzu | `<repo>/data/kuzu_graph[.wal]` — **no longer moved** into `<DATA_DIR>/kuzu/` (`GraphService._migrate_legacy_db_path` removed 2026-09-19); copy by hand | |
| Engine binaries | none in the repo; always `DATA_DIR/bin/<triple>/` | downloaded by `desktop_runtime.ensure_binaries` |
| Firefly seed | `<repo>/desktop/resources/firefly/` (gitignored `build.py` output) | used by `desktop_runtime._bundled_firefly_root()` when `ORB_RESOURCES_ROOT` is set (packaged, or `ORB_USE_RESOURCES=1` in dev) |
| Backend logs (legacy) | `backend/logs/` is gitignored but no longer written; logs go to `DATA_DIR/logs` | |
| Cloud API keys | OS keychain (`keyring`, service `Orb`, entries per provider/endpoint plus `__index__`) — never a file under `DATA_DIR` | written by `services/credentials.CredentialStore` via `/api/v1/credentials` |

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
| `POST /api/v1/setup/paths` | (may re-point default KB `vault_path`) | — | — | — | — | — | rewrites `paths.json`; re-targets `settings.*` and logs |
| Settings "runtime LLM" save (`api/settings.py`) | — | — | — | — | — | — | `runtime_config.json` |
| Setup model selection | — | — | — | Qdrant collections may be recreated if empty and dims changed | — | — | `manifest.json.selection` |
| Setup re-run (delete `paths.json`) | — | — | — | — | — | — | new `paths.json`; existing dirs untouched |

Nothing in the app deletes `orb.db`, `meili_master_key`, `qdrant/`, `meilisearch/` storage roots, `bin/`, `firefly/php`, `firefly/app`, or `MODELS_DIR` contents; those are manual operations (§3 for safety).

## 9. Legacy locations and migrations (LifeOS / LiveOS / Electron era)

| Legacy artefact | Current handling |
|---|---|
| WebView data under `com.orb.app` (bundle identifier before 1.0.0) | orphaned: 1.0.0 uses `com.josetseph.orb`, so the UI's `localStorage` conveniences (`orb_current_kb`, graph controls) start fresh once; the old folder is safe to delete. Notes, indexes and `paths.json` are unaffected. |
| App Support `LifeOS/` or `LiveOS/` with `paths.json` | **no longer probed** (removed 2026-09-19). Move the folder to `Orb/` by hand if such an install still exists. |
| Env vars `LIVEOS_*` | **no longer read** (removed 2026-09-19); only the `ORB_*` names exist. |
| `DATA_DIR` env (bare) / `MODELS_DIR` env (bare) | third-priority aliases (container era). |
| `<repo>/data/kb_registry.json` | the one-shot import into `knowledge_bases` was removed 2026-09-19; the file is ignored. |
| `<repo>/data/kuzu_graph` (Kuzu file at data root) | no longer moved (removed 2026-09-19); copy it to `<DATA_DIR>/kuzu/kuzu_graph` by hand. |
| `…/kuzu/<slug>` stored as a **directory** path | healed to `…/kuzu/<slug>/kuzu_graph` by `normalize_kuzu_path` at registry load only (`KBRegistry._load`), persisted to the row. |
| `notes.content` column | dropped by `_sqlite_repairs` at boot once every row is empty; kept (with a warning) while any legacy body remains. |
| `knowledge_bases.typesense_collection` | column name retained; holds the Meilisearch index name (`orb_nodes` default; raw `sqlite3` in `kb_registry`, no ORM class). The `TYPESENSE_*` env aliases are gone. |
| Meili master key `orb-dev-key` | used automatically when `meilisearch/` already has data but no `meili_master_key` file exists. |
| `localStorage` keys `lifeos_current_kb` / `liveos_current_kb` | migrated to `orb_current_kb` on first read. |
| `DATABASE_BACKEND=postgres` + `DATABASE_*_URL` | removed; SQLite `orb.db` is the only database. |
| `credentials.enc` (Electron `safeStorage` file in the App Support folder) | no longer read or written; keys now live in the OS keychain via `keyring`. Delete it by hand if present. |
| Alembic migrations (`backend/alembic/versions/d4f891a2b5c3_add_kb_id_to_notes.py`) | removed; schema is `create_all` + ad-hoc `ALTER TABLE … ADD COLUMN` (`kb_registry._ensure_optional_columns`: Firefly and LLM override columns) + `CREATE INDEX IF NOT EXISTS ix_notes_kb_rel_path`. |
| RustFS / S3 uploads | replaced by `local_storage` → `<vault>/attachments/`; `vault_rel_from_url` still maps any `…/attachments/<name>` URL for old bodies. |

### 9.1 One-time migrations and their gates (2026-09-19)

Every repair that used to run on each read now runs once and leaves a marker (or is naturally idempotent SQL). None has an undo; deleting the marker re-runs the step.

| Migration | Where it runs | Gate / marker | What it does |
|---|---|---|---|
| Vault sweep (v4) | `vault_sync.migrate_vault_files(vault)` from `sync_vault_notes` (thread) — notes listing / setup | `<vault>/.orb/migrated-v4` (touched after the loop; v1/v2 vaults rerun the idempotent sweep once — every step is a no-op on a clean vault) | 1. Moves every non-hidden non-`.md` file outside `attachments/` to `attachments/<its folder>/` (`unique_rel_path` on collision; `.keep`, `.DS_Store` and dotfiles are skipped by `_iter_rel`) and rewrites links to it with `rewrite_refs_in_text`. 2. Rewrites note `.md` files in place: collapses `attachments/attachments/` in `/vault-files/<kb>/…` and bare targets, rewrites every `/vault-files/<any kb>/<rel>` in `](…)` targets and `orb:extract src="…"` to the vault-relative `<rel>` with segments re-encoded (`unquote` → `quote(seg, safe="")`), wraps pre-marker enrichment blocks in `<!-- orb:extract -->` markers. Writes go through `mark_self_write`; logs `Vault migration v3 (<vault>): N files moved, M files rewritten` to `ingestion.log`. |
| `rel_path` backslash repair | `core/database.init_db` → `_sqlite_repairs` | none — idempotent `UPDATE notes SET rel_path = replace(rel_path,'\','/') WHERE rel_path LIKE '%\%'` on every start | Rows written as `str(Path)` on Windows; `note_files` now stores `.as_posix()`. |
| `notes.attachment_modes` column drop | `core/database._sqlite_repairs` | `PRAGMA table_info(notes)` — runs while the column is present | `ALTER TABLE notes DROP COLUMN attachment_modes` (it held the answers to a per-attachment prompt that was removed in `ba995da`; the ORM no longer maps it). |
| `notes.content` column drop | `core/database._sqlite_repairs` | `PRAGMA table_info(notes)` — runs while the column exists | `ALTER TABLE notes DROP COLUMN content` once no row holds a body; otherwise logs a warning and keeps it. |
| Kuzu path repair | `kb_registry._load` | none — idempotent; persisted with `UPDATE knowledge_bases SET kuzu_path` when `normalize_kuzu_path` changes it | Directory-shaped `kuzu_path` → `…/kuzu_graph` file path. Removed from `get_kb`/`graph`/`_build_context`/`_cleanup_stores`. |
| `llm_provider` coercion | `kb_registry._load` | none — idempotent `UPDATE knowledge_bases SET llm_provider='local' WHERE llm_provider IN ('ollama','lm_studio')` | Deprecated provider names in KB rows. |
| Attachments boundary (invariant enforced after the sweep, not a migration) | `local_storage.store_upload`, `vault_ops.move_vault_file`, the vault tree UI | none | Uploads land in `attachments/<note folder>/`; `move_vault_file` raises `ValueError("Cannot move across the attachments/ boundary")` (→ 400) for attachment → note folder, note → `attachments/`, and folder moves crossing it; the tree offers file drops only on `attachments` / `attachments/<sub>`. Note moves never move attachments, because links are vault-root-relative. |
| Store scrub | `main._migrate_stores()` at the end of `_background_startup`, per KB | `DATA_DIR/.stores-migrated-v1-<kb_id>`, touched **only after** all three steps succeeded (an unreachable Qdrant/Kuzu leaves it absent, so the KB is retried next boot) | Kuzu: `SEMANTIC_REL {rel_type:'relates_to'}` → `'related_to'` (closed vocabulary default rename); Qdrant: `QdrantService.strip_facts_prefixes()` removes legacy `FACTS: …. ` prefixes from `node_cores` descriptions; Kuzu: `kind='note'` nodes with NULL/`''`/`Unknown`/`Untitled` names are backfilled from `orb.db` (`notes.title`, else the `rel_path` stem). |


## 10. Backup, move and recovery guidance

- **Minimal backup of user content**: every vault folder (default vault path from `GET /api/v1/setup/status.active_vault_path` or `GET /api/v1/kb[].vault_path`) + `DATA_DIR/orb.db` + `DATA_DIR/firefly/app/storage/` + `DATA_DIR/firefly/runtime.json` + `paths.json`. Graph/vector/keyword stores and models are reproducible (re-ingest / re-download).
- **Full cold backup**: stop Orb, copy `DATA_DIR`, `MODELS_DIR`, `paths.json`, and any external vault folders. Qdrant/Meili/Kuzu files are not safe to copy while running.
- **Moving `DATA_DIR`**: quit Orb, move the folder, edit `paths.json.data_dir` (or delete `paths.json` and re-run setup pointing at the new location). `knowledge_bases.vault_path`/`kuzu_path` are **absolute**, so vaults/Kuzu under the old `DATA_DIR/vaults` and `DATA_DIR/kuzu` must be re-pointed (no tool for this; edit the rows, or use `POST /api/v1/setup/paths` for the default vault only). Bodies use relative `rel_path`, so moving a vault folder as a whole preserves notes.
- **Moving `MODELS_DIR`**: move the folder and update `paths.json.models_dir`; `manifest.json.selection.*_path` and `runtime.*` hold absolute paths and will fail `gguf_paths_if_present` until Setup re-selects (the fallback by default filename still works when the defaults were chosen).
- **Recovering from a lost `orb.db`**: start Orb; the default KB re-adopts its vault's `.md` files (new ids, `created_at` = now, titles from filenames). Non-default KBs must be re-created via `POST /api/v1/kb` with the same `vault_path` (a new slug/id means new Kuzu/Qdrant/Meili names; the old stores are orphaned). Chat history and Firefly mappings are lost; Firefly groups still exist inside `firefly.sqlite` but are no longer attached.
- **Recovering indexes**: `POST /api/v1/admin/reset-ingestion-data?kb=` then `POST /api/v1/admin/reingest-all` (or `reingest-vault`) per KB.

## 11. Gotchas

- `paths.json` is *not* inside `DATA_DIR`; wiping `DATA_DIR` leaves the app pointing at an empty folder that it silently recreates (no setup page) — it looks like data loss but the vault chosen at setup outside `DATA_DIR` is intact.
- The default KB's vault is usually **outside** `DATA_DIR/vaults/`; only non-default/legacy KBs live there. Do not assume all notes are under `DATA_DIR`.
- `kuzu_graph` is a **file**; `kuzu/<slug>/` is a folder containing that file. Tools that expect a directory database (older Kuzu) will misread it.
- Qdrant and Meilisearch are single servers for all KBs; per-KB isolation is by collection/index *name*. Deleting `qdrant/` or `meilisearch/` affects every KB.
- `meili_master_key` and `meilisearch/` must be deleted **together**; deleting only the key on an old install falls back to `orb-dev-key`, deleting only the data keeps a random key that then opens an empty store (fine).
- Sidecar stdio logs (`qdrant.log`, `meilisearch.log`, `firefly.log`, `multimodal.log`) roll to `.log.1` (one generation) when over 10 MB at the next spawn (`desktop_runtime._rotate_log`); `backend.log` is opened by the Tauri shell (`runtime.rs::spawn`) with the same 10 MB / `.1` rule; backend component logs are (10 MB × 5). `errors.log` aggregates ERROR+ from all components.
- `runtime_config.json` holds only `provider`, `model`, `base_url` and the fourteen local-runtime keys (the llama.cpp knobs plus `large_attachment_tokens`); unknown keys are dropped on load and save.
- `firefly/runtime.json` and `firefly/app/.env` contain secrets (API token, `APP_KEY`, password) and are written owner-only; `paths.json` and `orb.db` contain absolute paths and Firefly group ids but no secrets.
- `MODELS_DIR` on SMB/NAS triggers staging; the staging dir is created eagerly (`mkdir`) even when unused.
- `ensure_data_layout` is called at import of `core/database.py`, so simply importing the backend creates the `DATA_DIR` skeleton.
- `DATA_DIR/vaults/<slug>` is created even when `POST /api/v1/kb` was given an explicit `vault_path`? **No** — only when no path is supplied (route requires one), or during legacy JSON migration; an empty `vaults/` folder is therefore normal.
- `manifest.json.gguf` entries are never pruned when files are deleted; treat it as informational.


### On-demand Python packages

`DATA_DIR/python-user/` is the interpreter's user-site (`PYTHONUSERBASE`, set by `desktop_runtime.py`, which re-execs once so the directory is on `sys.path`). The multimodal stack (torch, transformers, mlx, …) is installed there with `pip install --user` on first use, so the app bundle is never written to. Delete the folder to force a reinstall.
