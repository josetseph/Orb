# Notes, Wikilinks and Vault Files

**What this covers.** The contract between a `notes` row in SQLite and the `.md` file that holds its body inside a Knowledge Base vault: `rel_path`, title ↔ filename synchronisation, sanitisation and collision handling, `created_at` semantics, folders and `.keep`. It documents `note_files.note_body` / `persist_note_body` (and the legacy `notes.content` fallback), every operation in `vault_ops.py` and `vault.py` (create, update, move, rename-for-title, move-to-folder, mkdir, delete attachment with link stripping, note delete with orphan cleanup, batch delete, folder listing, `safe_vault_join` traversal protection, markdown/wikilink reference rewriting on move), the `/vault-files/{kb}/{path}` URL scheme and how it maps to disk, the upload flow and `attachments/` naming, attachment-discovery regexes shared by the editor and the ingestion agent, the enrichment-block markers, the `[[wikilink]]` syntax and resolver (normalisation, exact-path-first disambiguation, folder proximity), `note_links` maintenance, the notes-graph payloads, the autocomplete/hover-preview contract, and the note processing status fields and who writes them.

**Related docs.** [Knowledge bases and vaults](08-knowledge-bases-and-vaults.md) (KB registry, vault provisioning, vault sync & watcher) · [API reference](07-api-reference.md) (full request/response shapes for every route named here) · [Ingestion pipeline](10-ingestion-pipeline.md) (what happens after ingest is triggered; enrichment blocks) · [Multimedia enrichment](11-multimedia-enrichment.md) · [Frontend notes editor](19-frontend-notes-editor.md) (CodeMirror extensions that consume these contracts) · [Graph storage](14-graph-storage-kuzu.md) · [Search indexes](15-search-indexes-qdrant-meilisearch.md) · [Data directory layout](22-data-directory-layout.md) · [Glossary](28-glossary.md).

## 1. Responsibilities and boundaries

**Owns**

- The `notes` row ↔ vault `.md` mapping (`rel_path`, `title`, timestamps, processing flags) and the rule that bodies are file-backed.
- All in-app mutations of vault files: creating/writing note bodies, renaming for title changes, moving notes/attachments, deleting attachments and notes, creating folders, saving uploads.
- Reference integrity inside markdown when files move or disappear: `](…)` target rewriting, `[[wikilink]]` repointing, link stripping.
- Wikilink parsing/resolution and the `note_links` table (notes-only graph, distinct from the Kuzu entity graph).
- The `/vault-files/{kb}/{path}` URL scheme (server) and its client-side helpers.

**Does not own**

- KB registry, vault provisioning, vault scan and watcher: [08](08-knowledge-bases-and-vaults.md).
- What ingestion does with the body (LLM extraction, Kuzu/Qdrant/Meili writes) and how enrichment blocks are generated: [10](10-ingestion-pipeline.md), [11](11-multimedia-enrichment.md). This doc only defines the markers and discovery regexes those subsystems must honour.
- The CodeMirror editor internals: [19](19-frontend-notes-editor.md). Only the data contract (what the editor inserts and expects back) is specified here.

## 2. Files

| Path | Purpose | Key exports |
|---|---|---|
| `backend/app/models/note.py` | ORM for `notes` (metadata only) | `Note` |
| `backend/app/models/wikilink.py` | ORM for `note_links` (directed wikilink edges) | `NoteLink` |
| `backend/app/schemas/note.py` | Pydantic bodies for note/vault routes | `CreateNoteInput`, `MoveNoteInput`, `MoveVaultFileInput`, `DeleteVaultFileInput`, `BatchDeleteNotesInput`, `MkdirInput` |
| `backend/app/services/note_files.py` | Body read/write against the vault; legacy `content` fallback | `note_body`, `persist_note_body`, `normalize_vault_file_refs` |
| `backend/app/services/vault.py` | Leaf helpers: sanitising, unique paths, raw file IO, self-write marks, attachment save, wikilink regex | `sanitize_title`, `unique_md_path`, `read_note_file`, `write_note_file`, `delete_note_file`, `save_attachment`, `_faststart_mp4`, `extract_wikilinks`, `WIKILINK_RE`, `title_from_filename`, `mark_self_write`, `is_recent_self_write`, `ensure_vault`, `clear_vault_contents` |
| `backend/app/services/vault_ops.py` | Move/rename/delete with reference rewriting; path safety | `safe_vault_join`, `unique_rel_path`, `rewrite_refs_in_text`, `rewrite_wikilinks_in_text`, `strip_refs_in_text`, `strip_refs_across_notes`, `delete_vault_file`, `move_vault_file`, `rename_note_file_for_title`, `move_note_to_folder`, `_norm`, `_WIKILINK_TARGET_RE` |
| `backend/app/services/wikilinks.py` | Link normalisation, `WikilinkResolver`, `note_links` refresh, graph payloads | `_normalize_link`, `_folder_of`, `_folder_proximity`, `WikilinkResolver`, `refresh_note_links`, `refresh_note_links_sync`, `rebuild_kb_note_links`, `notes_graph_payload`, `note_neighborhood_payload` |
| `backend/app/services/local_storage.py` | Upload → `attachments/`; URL ↔ rel-path mapping | `store_upload`, `remove_upload`, `vault_rel_from_url` |
| `backend/app/services/vault_sync.py` | Folder / attachment / media listings (see 08 for the scan) | `list_vault_folders`, `list_attachment_files`, `list_vault_media_files`, `iter_vault_md_files` |
| `backend/app/api/notes.py` | Note CRUD, ingest trigger, status, batch delete | `create_note`, `update_note`, `get_notes`, `get_note`, `move_note`, `delete_note`, `batch_delete_notes`, `_delete_note_impl`, `_note_response`, `_attachment_rels_from_note_body`, `_parse_date_str` |
| `backend/app/api/vault.py` | Vault file routes | `move_vault_path`, `delete_vault_path`, `mkdir_vault_folder`, `list_folders`, `vault_local_path` |
| `backend/app/api/files.py` | Upload / delete attachment | `upload_file`, `delete_file`, `_transcode_to_m4a` |
| `backend/app/api_desktop.py` | Notes graph routes, `reingest-vault`, `/vault-files/{kb_id}/{file_path}` | `notes_graph`, `notes_graph_neighbors`, `rebuild_notes_graph`, `reingest_vault`, `serve_vault_file` |
| `backend/app/workflows/agents/ingestion_agent.py` | Attachment discovery + enrichment-block regex (consumer of this contract) | `_ATTACH_RE`, `_IMAGE_MD_RE`, `_ENRICHMENT_BLOCK_RE`, `_strip_prior_multimedia_enrichment` |
| `backend/app/services/multimedia.py` | `/vault-files/…` → local path for enrichment | `MultimediaService._resolve_vault_local_path` |
| `frontend/src/app/notes/_lib/wikilinks.ts` | Client mirror of the resolver + autocomplete insert rules | `normalizeLink`, `folderOf`, `noteVaultPath`, `noteDisplayName`, `wikilinkInsertTarget`, `suggestWikilinkNotes`, `parseWikilinkCreateTarget`, `WikilinkResolver`, `resolveNoteByWikilink` |
| `frontend/src/app/notes/_lib/parse-note-attachments.ts` | Client attachment regex (attachments strip) | `parseNoteAttachments` |
| `frontend/src/app/notes/_hooks/useWikilinkPreview.ts` | Hover card + click-to-open/create | `useWikilinkPreview` |
| `frontend/src/app/notes/_hooks/useNoteAutosave.ts` | 1.5 s debounced `PUT`; rel_path carry-over after rename | `useNoteAutosave` |
| `frontend/src/app/notes/_hooks/useNoteMedia.ts` | Upload → markdown insert (`![..](..)` / `[📎 ..](..)` / `[🎤 ..](..)`), attachment delete | `useNoteMedia` |
| `frontend/src/components/markdown-editor/wikilinkExtension.ts` | CodeMirror `[[` completion source, decorations, click handler | `wikilinkQueryAt`, `wikilinkCompletionSource`, `createWikilinkDecorations`, `wikilinkClickHandler` |
| `frontend/src/components/markdown-editor/mediaEmbedExtension.ts` | Inline media embeds for attachment links | (regex accepting `📎`/`🖇`/`🎤`) |
| `frontend/src/lib/utils.ts` | `/vault-files` URL helpers | `resolveFileUrl`, `encodeFileUrl`, `isImageUrl`, `isVideoUrl` |
| `frontend/src/lib/api.ts` | Route wrappers (`createNote`, `updateNote`, `moveNote`, `moveVaultFile`, `deleteVaultFile`, `mkdirVaultFolder`, `listVaultFolders`, `resolveVaultLocalPath`, `deleteNote`, `batchDeleteNotes`, `getNotesGraph`, …) | `api` |
| `frontend/next.config.ts` | Dev rewrite `/vault-files/:path*` → backend | — |

## 3. The note model and the file contract

### 3.1 `notes` table (`models/note.py`)

| Column | Type | Semantics |
|---|---|---|
| `id` | String PK, uuid4 | Stable note id; also the Kuzu note node id (`Node {id, kind:'note'}`) and the Qdrant/Meili node id for the note's own node. |
| `kb_id` | String NOT NULL, default `"default"`, indexed | Owning KB (`KBContext.kb_id`). |
| `rel_path` | String NULL | Vault-relative path of the `.md`, forward slashes, e.g. `Life/Daily Log/2024-07-07.md`. `NULL` only for pre-vault legacy rows; every write path fills it. Composite index `ix_notes_kb_rel_path (kb_id, rel_path)` (created by `create_all` and re-ensured by `init_db` for older SQLite files). |
| `title` | String NULL | User-facing title. `NULL`/blank allowed; the UI then shows the filename stem. |
| `content` | Text NULL, default `""` | **Deprecated fallback.** Kept empty by `persist_note_body`; only read when the vault file is missing/empty (§4). |
| `created_at` | tz-aware DateTime | User-editable "note date" (see 3.5), default now. Sort key for `GET /api/v1/notes` (desc). |
| `updated_at` | tz-aware DateTime, `onupdate` now | Bumped on `PUT`, on vault adoption, and by the watcher. |
| `processed` / `failed` | Boolean | Ingestion outcome flags (§13). |
| `processing_stage` | String NULL | Human-readable stage/status string (§13). |
| `processing_model` | String NULL | Model currently used by ingestion, else NULL (§13). |

Wire form (`_note_response`): `{id, content, title, rel_path, created_at, updated_at, processed, failed, processing_stage, processing_model, kb_id}` where `content` is read from disk on every serialisation.

### 3.2 Where the body lives

`body = read(vault_path / rel_path)`. `notes.content` is written as `""` by every in-app save. The file is the single source of truth; the row is metadata. Corollaries:

- `GET /api/v1/notes?search=` searches `title` and `rel_path` only (bodies are not in SQLite). Full-text over bodies is Meilisearch's job after ingestion.
- Deleting the file externally makes `content` come back empty (and the watcher flags it), but the row survives.
- Backups of `orb.db` alone do **not** contain notes.

### 3.3 Filenames, titles and sanitisation

`sanitize_title(title)`:

1. `strip()`; empty → `Untitled`.
2. Remove `< > : " / \ | ? *` and control chars `\x00-\x1f`.
3. `rstrip(". ")` (Windows forbids trailing dots/spaces).
4. Truncate to 180 chars; empty → `Untitled`.

Note that `/` is removed, not turned into a folder — a title `Q3/Plan` becomes file `Q3Plan.md`. Folder placement is a separate `folder` argument.

`unique_md_path(vault, title, folder=None) -> Path`:

- `folder` normalised (`\`→`/`, stripped of `/`); any `..` segment resets it to the vault root (silently).
- Candidate `<folder>/<sanitised>.md`; on collision `<sanitised> 2.md`, ` 3`, … (checks disk existence, not SQLite).

`unique_rel_path(vault, desired_rel)` (vault_ops) is the same idea for arbitrary files: appends ` 2`, ` 3` before the suffix.

`title_from_filename(rel) = Path(rel).stem` — folders never contribute to titles.

### 3.4 Title ↔ filename synchronisation rules

| Direction | When | Rule |
|---|---|---|
| title → filename | `PUT /api/v1/notes/{id}` with `title` present (autosave always sends it) | `rename_note_file_for_title`: if `sanitize_title(title).lower() != current stem.lower()`, move the file to `<same folder>/<sanitised>.md` (uniquified) and rewrite all refs/wikilinks. Case-only changes do **not** rename (APFS case-insensitivity would trip the uniquifier into ` 2`); the display title keeps the new casing. |
| title → filename | `POST /api/v1/notes` | `persist_note_body` chooses `unique_md_path(vault, title or "Untitled", folder)`. |
| filename → title | `sync_vault_notes`, watcher, `move_vault_file` | **Only if the title is blank.** Comments: *"Display title is user-owned — don't clobber from filename"*, *"users often rename the title without renaming the .md file"*. |
| title → filename | ingestion `_update_note_title` (LLM-generated title) | Updates `notes.title` only; the file is **not** renamed. The next autosave with that title will rename it (via the rule above), since the frontend sends the current title. |

So filename and title can legitimately diverge: files created by older builds (`Untitled 3.md` with title `Budget`), case-only differences, titles containing removed characters, and uniquified suffixes (` 2`). Everything that displays or links by name prefers `title` (`noteDisplayName` on the client, `n.title or n.rel_path or n.id` in graph payloads).

### 3.5 `created_at` handling

`CreateNoteInput.created_at: str | None`. `_parse_date_str` tries `dateutil.isoparse`, then `dateparser.parse` (natural language), coercing naive results to UTC; if both fail it returns **now** (never an error). On `PUT`, `created_at` is only updated when the body provides a non-empty string (the frontend's autosave sends `undefined`; the date picker sends a value). The ingestion `NoteInput.created_at` is fed from this column so temporal extraction uses the user's note date, not the file mtime.

### 3.6 Folders

- A folder is any directory inside the vault; the API represents it as a vault-relative string (`Life/Daily Log`); `""` is the vault root.
- `POST /api/v1/vault/mkdir` creates it plus an empty `.keep` file (hidden, so empty folders persist and show in `list_vault_folders`).
- `attachments/` is reserved: `.md` files under **any** folder named `attachments` are not treated as notes (`iter_vault_md_files`), and the watcher ignores `attachments/` at the root.
- Hidden segments (`.obsidian`, `.git`, `.trash`) are ignored by every scan and listing and by the watcher.
- Notes are moved between folders with `POST /api/v1/notes/{id}/move {folder}` (same filename) or the generic `POST /api/v1/vault/move {from_rel,to_rel}`.

### 3.7 Frontmatter

There is none. Orb writes the body verbatim; no YAML frontmatter is added, parsed or stripped anywhere in the backend. Metadata (title, date, flags) lives only in SQLite. If a user's Obsidian files contain frontmatter it is treated as body text (and will be sent to the LLM during ingestion).

## 4. `note_files`: reading and writing bodies

### 4.1 `note_body(note, kb) -> str`

```
if note.rel_path and kb.vault_path:
    text = read_note_file(vault, rel_path)      # "" if the file does not exist
    if text: return normalize_vault_file_refs(text)
return normalize_vault_file_refs(note.content or "")   # legacy fallback
```

- The fallback to `notes.content` fires when there is no `rel_path`, no vault, the file is missing, **or the file is empty**. An intentionally empty note therefore shows whatever stale legacy `content` the row still has (normally `""`).
- `read_note_file` goes through `safe_vault_join`, so a corrupted `rel_path` containing `..` raises `ValueError` (→ 500 on the listing route; it is not caught per note).
- `normalize_vault_file_refs` collapses `(/vault-files/<kb>/)attachments/attachments/` → `attachments/` on read and write, repairing links doubled by an older buggy rewrite. This runs on **every** read, so bodies returned by the API can differ from the file until the next save rewrites it.

### 4.2 `persist_note_body(note, kb, content, title=None, folder=None)`

1. `mkdir -p vault`.
2. `content = normalize_vault_file_refs(content)`.
3. `display_title = title if title is not None else note.title`.
4. If `note.rel_path` is empty → `note.rel_path = unique_md_path(vault, display_title or "Untitled", folder)`. (`folder` is only honoured for the *first* write; later saves never move the file.)
5. Title bookkeeping: if `title` was passed, `note.title = display_title or None`; else if `display_title` differs from `note.title` it is assigned (no-op in practice).
6. `write_note_file(vault, rel_path, content)` → `safe_vault_join`, `mkdir -p parent`, `mark_self_write`, `write_text(utf-8)` (**not atomic**: a crash mid-write truncates the file; there is no temp+rename).
7. `note.content = ""` — the SQLite copy is always emptied.

Callers: `create_note`, `ingest_note` (legacy combined route), `update_note`, `IngestionWorkflow._persist_note_body` (enriched body after multimedia), `strip_refs_across_notes`, `move_vault_file` (for rewritten bodies). None of them passes `folder` except `create_note`.

## 5. `vault.py` primitives

| Function | Contract |
|---|---|
| `read_note_file(vault, rel) -> str` | `safe_vault_join`; `""` when missing; UTF-8 (a non-UTF-8 file raises `UnicodeDecodeError`, uncaught). |
| `write_note_file(vault, rel, content)` | see 4.2 step 6. Always marks a self-write so the watcher ignores the resulting fs event for 12 s. |
| `delete_note_file(vault, rel)` | `safe_vault_join`; unlink if exists; `None` rel is a no-op. Does **not** mark a self-write. |
| `mark_self_write(vault, rel)` / `is_recent_self_write(path)` | module dict keyed by resolved absolute path, TTL `_SELF_WRITE_TTL_SEC = 12.0`; cleanup of entries older than 36 s on each mark. |
| `sanitize_title`, `unique_md_path`, `title_from_filename` | §3.3. |
| `extract_wikilinks(content) -> list[(target, alias\|None)]` | Iterates `WIKILINK_RE` (§10.1); targets stripped; empty targets dropped; duplicates **kept** (dedup happens in `_apply_note_links`). |
| `save_attachment(vault, src_name, data, folder=None) -> rel` | §8.2. |
| `_faststart_mp4(path)` | For `.mp4/.m4v/.mov`: `ffmpeg -y -i in -c copy -movflags +faststart tmp` (120 s timeout) then replace; silent no-op if ffmpeg is absent or fails. Makes uploaded videos seekable/streamable in the `<video>` tag before full download. |
| `ensure_vault`, `clear_vault_contents` | see [08 §6](08-knowledge-bases-and-vaults.md). |

## 6. `vault_ops.py` operations

### 6.1 Path safety

- `_norm(rel)`: `\`→`/`, strip leading `/`. (Does **not** strip `..`; callers check `".." in rel.split("/")` explicitly and raise `ValueError("Invalid path")`.)
- `safe_vault_join(vault, rel) -> Path`: `(vault / _norm(rel)).resolve()` must be inside `vault.resolve()` (`relative_to`), else `ValueError("Path escapes vault")`. Because it resolves symlinks, a symlink inside the vault pointing outside is rejected too. Every disk access for notes, attachments, `/vault-files`, uploads-delete and mkdir goes through it; the note-delete route explicitly refuses to "fall back to raw path joins" on failure.

### 6.2 `rewrite_refs_in_text(content, old_rel, new_rel, kb_id) -> str`

Rewrites markdown link/image **targets only** — the pattern is `(\]\()(<src>)(\))`, i.e. the target must be exactly the whole parenthesised URL. Four `src → dst` pairs are tried in order: `/vault-files/<kb_id>/<old>`, its percent-encoded form (each segment `quote(seg, safe="")`), bare `<old>`, bare encoded `<old>`. Then the `attachments/attachments/` collapse. Rationale in the docstring: an earlier bare substring replace turned `…/attachments/x.mp4` into `…/attachments/attachments/x.mp4` when `old_rel` was just a filename. Limitation: links with a title (`](url "title")`) or with a query string are not rewritten.

### 6.3 `rewrite_wikilinks_in_text(content, resolver, source_rel_path, moved_note_id, bare_target, path_target)`

Uses `_WIKILINK_TARGET_RE = \[\[([^\]|#]+)((?:#[^\]|]*)?(?:\|[^\]]*)?)\]\]` — unlike `WIKILINK_RE`, this one **does** match `[[Note#Heading]]` and `[[Note#Heading|alias]]`, capturing heading+alias as group 2 so they are preserved verbatim. For each link whose target resolves (via a resolver built from the **pre-move** note list) to `moved_note_id`: targets containing `/` become `path_target`, bare targets become `bare_target`. Unchanged if the new target equals the old.

### 6.4 `strip_refs_in_text(content, rel, kb_id)` / `strip_refs_across_notes(db, kb, rel) -> changed_count`

Removes `![..](..)` and `[..](..)` whose target **contains** any of: `rel`, `/vault-files/<kb>/rel`, the basename of `rel`, or the percent-encoded variants. Substring match on the basename means deleting `attachments/a.png` also strips a link to `other/a.png` or an external `https://x/a.png` — accepted. Collapses 3+ newlines to 2. `strip_refs_across_notes` applies this to every note of the KB via `note_body`/`persist_note_body` and returns how many bodies changed (no commit; caller commits).

### 6.5 `delete_vault_file(db, kb, rel) -> {deleted, links_stripped}`

Attachment delete (route `POST /api/v1/vault/delete`). Rejects empty/`..` (`ValueError`) and `.md` (`"Use note delete for markdown files"`); `FileNotFoundError` if missing or not a regular file. `mark_self_write`, unlink, `strip_refs_across_notes`, commit.

### 6.6 `move_vault_file(db, kb, from_rel, to_rel) -> {from, to, note_id, links_rewritten}`

Generic move for notes **and** attachments (route `POST /api/v1/vault/move`; also the engine behind title renames and folder moves):

1. Validate both paths (`ValueError` on empty/`..`), `src` must be an existing regular file (`FileNotFoundError`).
2. `dst_rel = unique_rel_path(vault, to_rel)` — so a collision silently produces ` 2`; the later `FileExistsError` check is effectively unreachable outside races. Same source and destination → early return with `links_rewritten: 0`.
3. `mkdir -p`, mark both paths as self-writes, `shutil.move`.
4. Load **all** notes of the KB.
5. If `src` ends in `.md` and a row with that `rel_path` exists: snapshot `resolver = WikilinkResolver(all_notes)` **before** changing `rel_path`, set `note_row.rel_path = dst_rel`, fill the title from the new filename only if blank.
6. Compute wikilink targets for the moved note: `path_target = dst_rel without .md`; `new_stem = Path(dst_rel).stem`; `ambiguous` = any *other* note whose normalised title **or** normalised filename stem equals `normalize(new_stem)`; `bare_target = path_target if ambiguous else new_stem`.
7. For every note: `rewrite_refs_in_text` (attachment/vault-file links) then, if a note moved, `rewrite_wikilinks_in_text`; persist changed bodies (each persist touches the file and marks a self-write).
8. `commit`; `refresh(note_row)`.

Cost: O(notes) file reads + writes for changed ones, per move. Does not touch `note_links` (the next `rebuild`/`PUT` recomputes them; the vault watcher ignores these writes because of the self-write marks — so **after a move, `note_links` may be stale until the next save or rebuild**; `GET /api/v1/graph/notes` rebuilds by default).

Also note the graph/index side: the moved note's Kuzu/Qdrant/Meili node keeps its id; only `rel_path` changes, so no re-ingest is needed for a move.

### 6.7 `rename_note_file_for_title(db, kb, note, title) -> bool`

Called by `PUT /api/v1/notes/{id}` when `title` is present. Returns `False` (no-op) when: no `rel_path`, blank title, `rel_path` not `.md`, or `sanitize_title(title).lower() == current stem.lower()`. Otherwise `move_vault_file(current, <same folder>/<sanitised>.md)`. Because `move_vault_file` uniquifies, renaming `B` to `A` when `A.md` already exists yields `A 2.md` (title stays `A`).

### 6.8 `move_note_to_folder(db, kb, note, folder) -> dict`

Route `POST /api/v1/notes/{id}/move`. Keeps the filename, targets `<folder>/<filename>` (`""` = root); `ValueError` on `..` or missing `rel_path`; same path → no-op dict. Delegates to `move_vault_file`.

## 7. Note routes and their side effects

Full shapes in [07 §Notes](07-api-reference.md#notes-apinotespy-plus-reingest-vault-in-api_desktoppy). Side-effect summary:

| Route | File | SQLite | `note_links` | Graph/indexes | Ingestion |
|---|---|---|---|---|---|
| `POST /api/v1/notes` (`CreateNoteInput {title?, content, created_at?, folder?}`) | `persist_note_body` → new `.md` at `unique_md_path(title, folder)` | new row: `id=uuid4`, `content=""`, `processed=False`, `processing_stage="Saved"`, `title` (blank → NULL), `created_at` parsed | `refresh_note_links` from the body | none | none |
| `POST /api/v1/ingest` (legacy `NoteInput {content, created_at?, title?, skip_ingestion?}`) | as above, root folder, filename from `title` or `Untitled` | `processing_stage="Queued for ingestion"` (or `Saved`) | refreshed | none yet | background `process_note` unless `skip_ingestion`; `require_ai()` |
| `GET /api/v1/notes` | reads every body (in a thread) | `sync_vault_notes` first (unless `sync_vault=false`) | none | none | none |
| `GET /api/v1/notes/{id}` / `/status` | body read / none | none | none | none | none |
| `PUT /api/v1/notes/{id}` (`CreateNoteInput`) | `persist_note_body` (title passed through) → `rename_note_file_for_title` if `title` present → file may move | `title`, `created_at` (if given), `updated_at=now`, watcher stages reset to `Saved` (unprocessed notes only; `Queued*`/`Starting*` kept) | refreshed from the new body | none (**never re-ingests**) | none |
| `POST /api/v1/notes/{id}/move` (`{folder}`) | `move_note_to_folder` | `rel_path` | not refreshed (bodies of other notes rewritten) | none | none |
| `POST /api/v1/notes/{id}/ingest` | none | `processed=False, failed=False, processing_stage="Queued for ingestion", processing_model=None` | none | by the pipeline | background `process_note(NoteInput(content=note_body, created_at, title), note_id)`; `require_ai()` |
| `POST /api/v1/notes/reingest-vault` | bodies read in a thread | all notes of the KB → `Queued for vault re-ingest` | none | by the pipeline | one background task per note |
| `DELETE /api/v1/notes/{id}` / `POST /api/v1/notes/batch-delete {ids}` (≤100) | see 7.1 | see 7.1 | see 7.1 | see 7.1 | none |

Handlers filter by `Note.kb_id == kb.kb_id` (a note id from another KB is a 404), except `move_note` which loads by id and then compares `kb_id`.

### 7.1 `_delete_note_impl(note_id, db, kb)` — the single-note delete used by both routes

Order is deliberate ("Vault file + DB row are removed first so a graph failure cannot leave an orphan markdown file or a broken UI still pointing at a deleted note"):

1. Load row (idempotent: missing → `{status:"deleted", already_gone:true, orphans_removed:0}`).
2. `body = note_body(...)` (thread) and `attached_rels = _attachment_rels_from_note_body(body)` — regex `!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)` over the body, each URL mapped by `vault_rel_from_url` (or accepted if it starts with `attachments/`), deduped, `..` rejected.
3. `delete_note_file(vault, rel_path)` (thread; failures logged, not raised).
4. `DELETE FROM note_links WHERE kb_id AND (source_note_id = id OR target_note_id = id)`; `DELETE FROM notes`; **commit**.
5. Best-effort graph cleanup (each in a thread, each exception logged): find orphan entities — entities referenced by this note and by **no other note** (`MATCH (note:Node {id:$id, kind:'note'})-[:REFERENCES]->(entity) WHERE entity.kind <> 'note' AND NOT EXISTS { MATCH (other:Node {kind:'note'})-[:REFERENCES]->(entity) WHERE other.id <> $id }`); `DETACH DELETE` the note node; `qdrant.delete_node(note_id)` + `meili.delete_node(note_id)`; then for each orphan entity `DETACH DELETE` + Qdrant/Meili delete.
6. Delete every attachment the body referenced via `remove_upload(vault, rel)` (silent on failure). **Attachments shared by two notes are deleted when either note is deleted** — there is no reference counting.
7. Return `{status:"deleted", id, orphans_removed}`.

Batch delete loops this per id, collecting `{id, error}` on exceptions.

## 8. Attachments: upload flow, `attachments/` naming, `/vault-files/{kb}/{path}`

### 8.1 Upload — `POST /api/v1/upload?kb=` (multipart `file`)

1. Whole file read into memory (no server-side size limit; the Next dev proxy allows 512 MB; the desktop UI talks to the backend directly).
2. Audio normalisation: if `content_type ∈ {audio/webm, audio/ogg, audio/opus, audio/x-matroska}` or extension ∈ `{webm, ogg, opus}` → `_transcode_to_m4a` (`ffmpeg -y -i in -c:a aac -b:a 128k out.m4a`, 60 s, thread). Success → bytes replaced, ext `m4a`, name hint `recording.m4a`; any failure → original bytes kept (name hint `recording.<ext>`). This is what the voice recorder relies on so Whisper and browsers get AAC.
3. `local_storage.store_upload(vault, filename_hint, bytes, kb.kb_id)` → `save_attachment` (8.2) → returns `{url: "/vault-files/<kb_id>/<rel>", key: <rel>, filename}`.
4. Response: `{filename (original), url, href (=url), rel_path (=key), local_path (=url), key, status:"success"}`.

The `<kb_id>` segment in the URL is **`kb.kb_id`** — `default` or the UUID — not the slug. Markdown therefore embeds UUIDs; `/vault-files/{kb_id}` accepts id, name or slug, and `vault_rel_from_url` ignores whatever the segment is, so links survive KB renames (but not moving a note's file to another KB's vault).

### 8.2 `save_attachment(vault, src_name, data, folder=None) -> rel`

- Folder defaults to `attachments`; `..` or empty resets to `attachments`; `mkdir -p`.
- Name: `<sanitize_title(stem)[:120]>-<8 hex of uuid4><ext lower>`; e.g. `logo-a1b2c3d4.png`, `recording-9f8e7d6c.m4a`. Collision (practically impossible) → `-2`, `-3` inserted before the extension.
- `write_bytes`, then `_faststart_mp4`.
- Returns `attachments/<name>`.

Uploads never mark a self-write (the watcher ignores non-`.md` anyway).

### 8.3 URL ↔ path mapping (`local_storage.vault_rel_from_url`)

| Input | Output |
|---|---|
| `/vault-files/<anything>/a/b.png` | `a/b.png` |
| `attachments/x.png` | unchanged |
| any string containing `/attachments/` (e.g. `http://host/foo/attachments/x.png?x=1`) | `attachments/x.png` (query stripped) |
| anything else | `None` |

Used by note delete (attachment discovery), `remove_upload`, `GET /api/v1/vault/local-path`. `MultimediaService._resolve_vault_local_path` has its own stricter parser: requires `/vault-files/<kb_id>/<rel>` (or `vault-files/…`), URL-decodes `rel`, rejects `..`, resolves the KB via `kb_registry.get_kb(kb_id)` (**id only** — a hand-written `/vault-files/<slug>/…` link is served by the browser route but is invisible to enrichment).

### 8.4 Serving — `GET /vault-files/{kb_id}/{file_path:path}` (`api_desktop.py`)

- Not under `/api/v1`; in dev, `next.config.ts` rewrites `/vault-files/:path*` to the backend so relative markdown URLs work from the Next origin.
- KB resolved by `kb_registry.get_kb(kb_id)` then `get_kb_by_name(kb_id)`; 404 `"KB not found"` if none or no vault.
- `safe_vault_join`; 404 `"File not found"` on escape or non-regular file.
- `FileResponse(full)`: Starlette derives `Content-Type` from the extension (`mimetypes`), sets `Content-Length`, `Last-Modified`, `ETag`, and honours `Range` requests (needed for `<video>`/`<audio>` seeking). No cache headers beyond that; no auth; **any** file in the vault is served, including `.md` and dotfiles.
- The desktop "Reveal in Finder" path uses `GET /api/v1/vault/local-path?rel=` instead, which returns the absolute path for the shell's allow-listed `reveal-in-folder` IPC.

### 8.5 Client helpers (`frontend/src/lib/utils.ts`)

- `resolveFileUrl(url, kbId)`: repairs doubled `attachments/attachments/`; `attachments/x` → `/vault-files/<kbId>/attachments/x` (kbId is whatever the caller has — the **slug** from `useKB`, which the server accepts); `/vault-files/…` passthrough.
- `encodeFileUrl(url)`: per-segment `encodePathSegment` (an `encodeURIComponent` that additionally escapes `(`, `)`, `[`, `]` as `%28 %29 %5B %5D`) after a safe decode, keeping the `/vault-files/<kb>/` prefix intact. `encodeURIComponent` leaves `!'()*` alone by design, and a bare `)` closes a `[label](url)` link early — so a filename with brackets used to produce a link that pointed at a truncated path and rendered as raw text. Every consumer below also accepts balanced parentheses, so links written before this are read correctly without rewriting the vault. The editor inserts encoded URLs; the backend's `rewrite_refs_in_text`/`strip_refs_in_text` handle both encoded and raw forms for this reason.

## 9. Attachment discovery regexes and markers (editor ↔ ingestion contract)

What the editor writes (`useNoteMedia.ts`):

| Kind | Inserted markdown |
|---|---|
| image upload | `![<original filename>](<encoded /vault-files url>)` — kept as an image so the editor previews it and *"ingestion also discovers ![alt](/vault-files/...) for Florence"* |
| any other file | `[📎 <original filename>](<encoded url>)` |
| voice recording | `[🎤 Voice Recording](<encoded url>)` |

Who parses them:

| Consumer | Pattern | Notes |
|---|---|---|
| `ingestion_agent` (module-level `ATTACHMENT_LINK_RE` / `IMAGE_LINK_RE`) | `_ATTACHMENT_URL = (?:https?://\|/vault-files/)(?:[^()\n]|\([^()\n]*\))+`; `ATTACHMENT_LINK_RE = \[(📎\|🎤)\s*(.*?)\]\((URL)\)`; `IMAGE_LINK_RE = !\[([^\]]*)\]\((URL)\)` | URL runs to the closing `)` — spaces/commas allowed (uploaded filenames), and **balanced parentheses** are kept, so `Report (2026).pdf` survives (one nesting level). Dedup by lower-cased, unquoted URL sans query. Type is decided by **extension** (`video .mp4 .mov .webm .mkv .avi`; `audio .m4a .mp3 .wav .ogg .aac`; `image .jpg .jpeg .png .webp .gif`; spreadsheets `.xlsx .xls .csv .tsv`; plus PDF/Word handled in the same node) — `🎤` only forces audio when the extension is unknown. Remote `http(s)` URLs are also accepted (guarded against SSRF/oversize elsewhere). `🖇` is **not** recognised by the backend. |
| `api/notes._attachment_rels_from_note_body` (delete) | `!?\[[^\]]*\]\(((?:[^()\s]\|\([^()\s]*\))+)(?:\s+"[^"]*")?\)` | any link/image whose URL maps to a vault rel; balanced parentheses allowed, but still stops at whitespace (so URLs with raw spaces are missed → those attachments are **not** deleted with the note). |
| `parse-note-attachments.ts` (attachments strip) | `URL_PART = (?:[^()\n]|\([^()\n]*\))+`; `(?:!\[([^\]]*)\]\((URL_PART)\)\|\[([📎🖇🎤][^\]]+)\]\((URL_PART)\))` | label stripped of the leading marker. |
| `mediaEmbedExtension.ts` (inline embeds) | `MEDIA_URL = (?:[^()\n]|\([^()\n]*\))+`; `(?:!\[([^\]]*)\]\((MEDIA_URL)\)\|\[([📎🖇🎤]?[^\]]*)\]\((MEDIA_URL)\))` | plain links without a marker only embed for YouTube/Vimeo. |
| `segmented-note-content.tsx`, `chat/page.tsx` | `text.startsWith("📎"/"🎤")` | render attachment buttons / inline media in read views and chat citations. |

**Enrichment blocks.** After multimodal extraction, the ingestion agent appends sections to the note body and persists them into the `.md`. The block headers are the only markers; `_ENRICHMENT_BLOCK_RE` matches the first of:

```
\n\n[PDF Extraction (<name>)]
\n\n[Image:<…>]
\n\n[Audio Transcript (<name>)]
\n\n[Video Audio Transcript (<name>)]
\n\n[Video Visual Analysis (<name>)]
\n\n[Word Extraction (<name>)]
\n\n[Spreadsheet Extraction (<name>)]
```

`_strip_prior_multimedia_enrichment` truncates the body at the first match, so on re-ingest the whole tail is regenerated once ("re-run multimedia once and rewrite a single clean enrichment section"). Consequences for this layer: anything the user types **after** an enrichment block is discarded on re-ingest; a user line that happens to start with `[Image:` after a blank line is treated as a marker. Adding a new enrichment type requires extending this regex. Details in [10](10-ingestion-pipeline.md)/[11](11-multimedia-enrichment.md).

## 10. Wikilinks

### 10.1 Syntax

`WIKILINK_RE = \[\[([^\]|#]+)(?:\|([^\]]+))?\]\]` (identical in `vault.py` and `wikilinkExtension.ts`).

| Form | Matched? | Target / alias |
|---|---|---|
| `[[Title]]` | yes | `Title` / — |
| `[[Folder/Sub/Title]]`, `[[Title.md]]` | yes | path-style target (normalised later) |
| `[[Title\|shown text]]` | yes | `Title` / `shown text` |
| `[[Title#Heading]]`, `[[Title#Heading\|alias]]` | **no** — `#` is excluded from group 1 and nothing consumes it before `]]` | not a link for `extract_wikilinks`, not decorated in the editor, not an edge in the notes graph. Only `_WIKILINK_TARGET_RE` (move rewriting) understands `#…` and preserves it. |
| `[[]]`, `[[ ]]` | no / target stripped to empty → dropped | — |
| `[Title]` (single brackets) | not a wikilink — but see below | the editor makes it **clickable** when `Title` resolves to an existing note |
| `![[embed]]` | matched as a normal link (the `!` is ignored) | Obsidian embeds are treated as links, not rendered |

**Single-bracket `[Note]` (editor only).** Typing `[` auto-closes to `[]`, so reaching for a wikilink and typing the name lands on single brackets. CodeMirror's markdown parser tags that as a shortcut reference link and paints it blue and underlined — it *looks* like a working link while being completely inert, which is a trap worth removing rather than documenting. `createWikilinkDecorations(getNotes)` therefore runs a second pass with `SHORTCUT_REF_RE = /(?<!\[)\[([^[\]\n]+)\](?![[(:\]])/g` (excludes `[[wiki]]`, `[text](url)` and `[ref]:` definitions) and decorates a match **only when `WikilinkResolver` finds a note by that name** — so `[TODO]` and `[1]` keep their ordinary styling and stay inert, and nothing is ever auto-created from a loose bracket. Matches are skipped where a real `[[wikilink]]` already claimed the range. Styled `.cm-wikilink-loose`: the same teal, dotted rather than solid, with a tooltip naming `[[…]]` as the real syntax. This is **editor-only** — `extract_wikilinks`, `note_links`, and the notes graph still require `[[…]]`, so a single-bracket link is navigable but is not a graph edge.

Aliases are ignored for resolution and for `note_links` (only the target is stored).

### 10.2 Normalisation — `_normalize_link` / `normalizeLink`

`\`→`/`, trim, strip leading/trailing `/`, lower-case, drop leading `./` repeatedly, drop a trailing `.md`. So `Life/Plan.md`, `/life/plan`, `./Life/Plan` all key as `life/plan`. Titles are normalised with the same function (so a title containing `/` competes with paths).

### 10.3 `WikilinkResolver(notes)` (backend `wikilinks.py`; identical algorithm in `wikilinks.ts`)

Index built once per note list:

- `_by_path[normalised rel_path] = note_id` (first wins).
- `_by_name[name] += candidate` for `name ∈ {basename of normalised rel_path, normalised title}`.
- `_rel_by_id[note_id] = raw rel_path` (to compute the source folder).

`resolve(target, source_rel_path) -> note_id | None`:

1. `key = normalise(target)`; empty → `None`. `source_dir = folder of normalised source_rel_path`.
2. **Exact vault path wins**: `key in _by_path` → that note. This is what keeps `[[photosynthesis]]` (root file) and `[[meow/photosynthesis]]` distinct, and matches what autocomplete inserts.
3. If `key` contains `/`: candidates whose `rel_path.endswith("/" + key)` (partial path suffix) → `_best`.
4. Relative to the linking note: `f"{source_dir}/{key}" in _by_path` → that note.
5. Otherwise by basename: `_best(_by_name[basename of key], source_dir)`.

`_best(candidates, source_dir)`: sort by `(_folder_proximity(source_dir, candidate_dir), depth = rel_path.count("/"), rel_path)` and take the first. `_folder_proximity(a, b) = (steps, -shared)` where `steps = (len(a) - shared) + (len(b) - shared)` tree hops (same folder 0, parent/child 1, sibling 2) and `shared` = common prefix length (a sibling beats an unrelated folder at equal distance). Deterministic: shallower, then alphabetical.

Notes without `rel_path` (legacy) have `rel_path=""` and are only reachable by title.

Client differences: `wikilinks.ts` returns the `Note` object rather than the id and takes a `sourceNote`; otherwise the tie-break order and normalisation are the same — **keep the two in lock-step** when changing either (commit `b35d612` introduced both together).

### 10.4 What autocomplete inserts — `wikilinkInsertTarget(note, notes)` (client)

- `path = rel_path without .md` (original casing) or the title if no path; `base = noteDisplayName(note)` (title, else path basename, else `Untitled`).
- If no other note has the same normalised display name → insert the bare `base`.
- If names collide → insert the exact `path`; and if the file stem differs from the display name (pre-rename vault) → `path|base` so the editor shows the title.

Suggestions (`suggestWikilinkNotes`, max 12): score 0 exact / 1 prefix / 2 substring on either name or path; `detail` = folder (or `vault root` when names collide); an extra `Create note` option is offered when the typed text matches nothing exactly. The completion `apply` inserts the target and auto-closes `]]` if not already present.

## 11. `note_links` maintenance and the notes graph

### 11.1 Table (`models/wikilink.py`)

`note_links`: `id` (uuid4), `kb_id` (indexed), `source_note_id` (indexed), `target_title` (raw target text as written, e.g. `meow/Photosynthesis`), `target_note_id` (nullable, indexed; `NULL` = unresolved/"missing" link), `created_at`. Unique on `(kb_id, source_note_id, target_title)`. There is no FK to `notes`; deletions are handled explicitly.

### 11.2 Writers

| Function | Where called | Behaviour |
|---|---|---|
| `refresh_note_links(db, kb_id, source_note_id, content, *, notes=None, resolver=None)` (async) | `create_note`, `ingest_note`, `update_note`, `rebuild_kb_note_links` | `DELETE` all rows for the source, then `_apply_note_links` inserts one row per **unique normalised target** (first spelling wins as `target_title`) with `target_note_id = resolver.resolve(target, source_rel)`. Loads all KB notes and builds a resolver if not supplied. |
| `refresh_note_links_sync(session, …)` | `vault_watcher._sync_vault_file` (created/modified) | same, synchronous SQLAlchemy `Session`. |
| `rebuild_kb_note_links(db, kb) -> {notes, links}` | `GET /api/v1/graph/notes` (default `rebuild=true`), `GET …/neighbors?rebuild=true`, `POST /api/v1/graph/notes/rebuild` | one notes query + one resolver for the whole KB, then `refresh_note_links` per note using `note_body` (disk read per note), commit. O(vault). |
| `_delete_note_impl` | note delete | deletes rows where the note is source **or** target (so links *to* a deleted note vanish rather than becoming `missing:` phantoms — until the next rebuild re-parses the linking note and re-creates them as unresolved). |
| `_purge_kb_sql_notes` | KB empty/delete | `DELETE WHERE kb_id`. |

Not refreshed by: `move_vault_file`/`rename_note_file_for_title`/`move_note_to_folder` (targets are rewritten in the bodies but `note_links` rows are left as-is until a save or rebuild), the ingestion pipeline (which may append enrichment text but never adds wikilinks).

A save of note A re-resolves only A's outgoing links. Creating a new note `B` that previously-missing links pointed at does **not** update other notes' `target_note_id` until they are re-saved or a rebuild runs — which is why the graph page rebuilds by default.

### 11.3 Payloads

`notes_graph_payload(db, kb_id)`:

```json
{"nodes": [{"id": "<note uuid>", "title": "<title | rel_path | id>", "type": "note", "rel_path": "Life/Plan.md"},
           {"id": "missing:<target_title>", "title": "<target_title>", "type": "missing", "rel_path": null}],
 "edges": [{"source": "<source_note_id>", "target": "<target_note_id | missing:<target_title>>", "type": "wikilink"}]}
```

Phantom nodes are deduped by `missing:<raw target_title>` (case-sensitive raw text — `[[Plan]]` and `[[plan]]` produce two phantoms). Edges are not deduped across notes (two notes linking the same missing title share one phantom, two edges).

`note_neighborhood_payload(db, kb_id, note_id)`: computes the full payload, keeps nodes that are the center or directly connected in either direction, and edges among the kept set; adds `"center_id": note_id`. Unknown id → empty lists (no 404). The frontend type is `NotesGraphPayload {nodes, edges, center_id?}`.

## 12. Client-side contract (autocomplete, hover preview, click-to-create, autosave)

- **Autosave** (`useNoteAutosave.ts`): 1.5 s after the last change to `content` or `title`, `api.updateNote(id, content, undefined, kb, title || undefined)` → `PUT` with `{content, created_at: undefined, title}`. The response note becomes the new baseline. If the user typed during the PUT the returned note is *not* applied (would wipe keystrokes) — except `rel_path`, which is carried over because a title change may have renamed the file server-side. Pending saves are flushed on unmount. Because `title` is sent on every save, the rename rule in §3.4 runs on every autosave (cheap when the stem already matches).
- **Autocomplete** (`wikilinkExtension.ts`): activates when the cursor is after an unclosed `[[` on the current line (no `]]`/`|` in between); options come from `suggestWikilinkNotes` over the current KB's loaded notes (`getNotes()` — the list from `GET /api/v1/notes`, so a note created in another window is unknown until refresh).
- **Decorations**: inactive lines render `[[target|alias]]` as a `span.cm-wikilink` widget with `data-wikilink-target` / `data-wikilink-alias`; the active line shows raw syntax with a mark. `wikilinkClickHandler` reads `data-wikilink-target`.
- **Hover / click** (`useWikilinkPreview.ts`): a `WikilinkResolver` is memoised per notes list. Hover → `{title, content, x, y, missing}` from the resolved note's already-loaded `content` (no request). Click → open the note; if unresolved, **create it**: `parseWikilinkCreateTarget(target)` splits `folder/title` (strips `.md`); bare names are created in the *linking note's folder*; `api.createNote("", now, kb, title, folder)` then refresh + select. A re-entrancy guard prevents double creation.
- **Attachments**: uploads via `api.uploadFile` (multipart) then markdown insertion per §9; deletes via `POST /api/v1/vault/delete` with the rel path derived from the URL (`/vault-files/<kb>/<rel>` → `<rel>`; bare filenames assumed under `attachments/`). The frontend never calls `DELETE /api/v1/files/{key}`.
- **KB param**: every call passes `useKB().currentKB` (slug); `/vault-files` URLs embed `kb_id` and are served regardless of the active KB.

## 13. Note processing status fields

| Field | Values / writer |
|---|---|
| `processed` | `True` only by `IngestionWorkflow._mark_note_processed`. Reset to `False` by: `POST /notes/{id}/ingest`, `reingest-vault`, `admin/reset-ingestion-data`, `admin/reingest-all`, and the **vault watcher** on external modification of a processed note. |
| `failed` | `True` only by `_mark_note_failed`; cleared by the same resets and by `_mark_note_processed`. |
| `processing_stage` | Free text shown in the UI. Writers: routes — `"Saved"` (create, autosave reset, watcher new file), `"Queued for ingestion"` (ingest), `"Queued for vault re-ingest"`; watcher — `"Changed on disk — re-ingest when ready"`, `"External delete detected — review in Orb"`; pipeline via `_update_note_processing_status(stage, model)` — per-phase strings (e.g. multimedia/extraction/indexing phases, `Starting…`), then `"Ingestion complete"` or `"Ingestion failed"`. |
| `processing_model` | Set together with the stage by the pipeline to the in-process model name currently running; `None` at rest, on queue, on completion and on failure. |

Derived `status` in `GET /api/v1/notes/{id}/status`: `completed` if `processed`, else `failed` if `failed`, else `processing` (so an un-ingested `Saved` note reports `processing` — the UI must look at `processing_stage`).

Ordering guards: `PUT` only rewrites the stage for **unprocessed** notes and never touches stages starting with `Queued`/`Starting` (a queued ingest must not be relabelled `Saved` by autosave); the watcher likewise leaves `Queued*`/`Starting*` alone. Pipeline writes go through a separate `AsyncSessionLocal` session per update (`_update_note_fields`), so they interleave with request sessions at the SQLite level.

## 14. Invariants and locked decisions

1. **Body = file; `notes.content` stays empty.** `persist_note_body` enforces it; readers fall back only when the file is empty/missing.
2. **All disk access goes through `safe_vault_join`.** No raw `vault / rel` joins in routes; never catch its `ValueError` and retry with a raw join.
3. **Title is user-owned; filename follows title, not the reverse** (except filling blank titles). Case-only title changes do not rename.
4. **Moves rewrite link *targets*, never do substring replacement** (the doubled-`attachments/` bug). New rewrite code must use anchored `](…)` or `[[…]]` patterns.
5. **Wikilink resolution: exact path > path suffix > relative-to-source > basename by folder proximity**, identically on client and server. Autocomplete must insert something that resolves to the chosen note under these rules.
6. **`[[…#heading]]` is not a link** for extraction/graph purposes (regex decision); only move-rewriting preserves headings.
7. **Delete order: file → SQLite (commit) → graph/indexes (best-effort) → attachments.** Graph failures must never leave a file or row behind.
8. **Attachments are owned by the note text that references them.** Deleting the note deletes referenced files; deleting a file strips references everywhere. No refcounting.
9. **Autosave (`PUT`) never ingests and never flips `processed`.** Only explicit ingest routes queue work.
10. **`/vault-files/<kb_id>/…` uses the KB id**, and the server accepts id/name/slug in that slot; keep both sides tolerant.
11. **Frontmatter is not interpreted.** Do not add YAML parsing without deciding how ingestion and title sync should treat it.

## 15. Failure modes and edge cases

| Case | Behaviour |
|---|---|
| Two notes titled the same in one folder | second gets `Title 2.md`; both titles `Title`; autocomplete inserts the path for both; bare `[[Title]]` resolves to the nearest/shallowest (deterministic, possibly not what the user meant). |
| Title with only forbidden chars (`???`) | file `Untitled.md` (uniquified); title stays `???`. |
| Rename to a title whose file exists | uniquified to ` 2`; all links repointed to the ` 2` path if ambiguous. |
| Note file deleted externally | body `""`; watcher marks `External delete detected`; row and links remain until user deletes in-app (which then finds no file, logs, and proceeds). |
| Vault on a case-insensitive FS, user renames `plan.md` → `Plan.md` externally | watcher sees moved → new row for `Plan.md`, old row marked deleted (see [08 §10.3](08-knowledge-bases-and-vaults.md)). |
| Non-UTF-8 `.md` in vault | `read_text` raises → the whole `GET /api/v1/notes` fails (500) because bodies are read in a batch. |
| Upload with ffmpeg missing | audio stored as `.webm`/`.ogg`; Whisper may still handle it; browsers vary. MP4 faststart skipped. |
| Attachment URL with raw spaces | served fine; found by ingestion (`[^)]+`); **missed** by delete's `[^)\s]+` (file left behind). |
| `move_vault_file` on a file with 5 000 notes in the KB | reads all 5 000 bodies synchronously on the event loop (no `to_thread`); slow on NAS. |
| Wikilink to a note in another KB | never resolves (resolver is per KB) → phantom node. |
| `PUT` with `title: null` | `persist_note_body(title=None)` keeps the existing title; no rename check. `title: ""` → stored as `NULL`, no rename (blank title short-circuits). |
| `created_at` unparsable | silently becomes now. |

## 16. Gotchas

- `unique_md_path` checks the **filesystem**, not SQLite: a stale row pointing at a deleted file does not block reuse of its filename; a new note then "adopts" the old path with a new id, and the old row shows the new body. `sync_vault_notes` will not fix this.
- `persist_note_body` honours `folder` only when the row has no `rel_path`; passing `folder` on update is ignored (use the move route).
- `strip_refs_in_text` matches by basename substring — deleting `attachments/a.png` can strip links to any `…a.png` anywhere, including external URLs.
- `rewrite_refs_in_text` does not handle `](url "title")` or `?query` targets.
- After `move_vault_file`, `note_links` is stale until a save/rebuild, and the watcher will not refresh it because the writes are self-marked.
- The notes graph deduplicates missing targets by **raw** text; `[[foo]]` and `[[Foo]]` are two phantoms but resolve identically once the note exists.
- `extract_wikilinks` treats `![[embed]]` as a link; the editor renders it as a link too, never as an embed.
- The hover card shows `note.content` from the list payload — stale if another client edited the note.
- `/vault-files` serves `.md` and dotfiles; do not rely on it as an "attachments only" endpoint.
- `DELETE /api/v1/files/{key}` returns 200 even when nothing was deleted.
- Enrichment blocks are plain text inside the user's `.md`; users editing below them lose those edits on re-ingest.
- Client `resolveFileUrl` builds URLs with the **slug**, uploads embed the **id**; both work server-side, but a naive string comparison between the two forms fails.
- `_attachment_rels_from_note_body` and the ingestion regex disagree on whitespace in URLs; keep uploaded filenames encoded (`encodeFileUrl`) to stay on the safe path.

## 17. Extension points

- **New attachment kind in the editor**: keep `[📎 name](url)` or `![alt](url)`; classification is by extension in `multimodal_node`, so extend the extension tuples there and in `mediaEmbedExtension.ts`/`segmented-note-content.tsx` for rendering. Avoid new emoji markers (backend only knows `📎`/`🎤`).
- **New enrichment section**: add its header to `_ENRICHMENT_BLOCK_RE` or re-ingest will duplicate it.
- **Heading links (`#`)**: change `WIKILINK_RE` in both `vault.py` and `wikilinkExtension.ts`, extend `extract_wikilinks` to return the fragment, and decide whether `note_links` should store it; `_WIKILINK_TARGET_RE` already preserves it on moves.
- **Atomic writes**: wrap `write_note_file` in temp-file + `os.replace`; remember `mark_self_write` must cover the final path (it is keyed by resolved path) and that the watcher will see a `moved` event for the temp file (ignored because it is not `.md` unless the temp suffix is `.md`).
- **Refcounted attachments**: replace step 6 of `_delete_note_impl` with a scan of other notes' bodies (there is no index of attachment usage today).
- **Making the resolver smarter** (fuzzy titles, aliases as targets): change `WikilinkResolver` on both sides and `wikilinkInsertTarget`, and add tests — there are currently no unit tests for `vault_ops`/`wikilinks`.

## 18. History / rationale

- `3f21e08` (2026-08-02): vault-backed notes replaced SQLite bodies (RustFS/S3 uploads replaced by `local_storage` → `attachments/`); `wikilinks.py` and `note_links` introduced.
- `f8f527f` (2026-08-06 audit): `WikilinkResolver` with folder-proximity disambiguation replaced a flat title map; `rewrite_refs_in_text` restricted to link targets ("double-applied vault path rewrites"); `note_files.normalize_vault_file_refs` added to heal existing bodies; autosave reworked to stop wiping in-flight keystrokes; per-keystroke neighbour refetches removed (hence `neighbors?rebuild=false`).
- `b35d612` (2026-08-06): Obsidian-style `[[` autocomplete; exact-path-first resolution on both client and server; click-to-create.
- `72413b9` (2026-08-06): title → filename sync (`rename_note_file_for_title`) with wikilink repointing; autocomplete labels prefer the display title so stale `Untitled N.md` filenames stop appearing.
- `8de5cda` (0.2.0): batching/parallel IO — body reads moved to threads in list/reingest/delete routes; single resolver per rebuild.
