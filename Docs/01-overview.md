# Orb — Overview

**What this covers:** what Orb is, who it is for, the product decisions that shape the codebase, the technology stack, and how to navigate the rest of this documentation set. Read this first; then [02-system-architecture.md](02-system-architecture.md).

**Related docs:** [System architecture](02-system-architecture.md) · [Repository layout](03-repository-layout.md) · [Decisions and constraints](26-decisions-and-constraints.md) · [Development guide](27-development-guide.md) · [Glossary](28-glossary.md) · [Development history](25-development-history.md)

---

## 1. What Orb is

Orb is a **local-first personal knowledge system** shipped as a desktop application (macOS, Windows, Linux). The user writes notes in Markdown, records voice memos, drops in photos, PDFs and videos. Orb turns that material into a **knowledge graph** of entities and relationships, indexes it for keyword and semantic search, and lets the user **chat across it** with multi-hop reasoning and inline citations. A built-in **finance** workspace (Firefly III) keeps accounts and transactions alongside the notes, scoped to the same knowledge base.

The tagline in the README is "Your knowledge, on your machine": nothing leaves the computer unless the user explicitly configures a cloud LLM provider.

Key user-facing capabilities:

| Area | Capability |
|---|---|
| Notes & vault | Real `.md` files in a per-knowledge-base vault folder (Obsidian-compatible), folders, attachments, `[[wikilinks]]` with autocomplete and disambiguation, entity highlighting in the editor, in-app voice recording |
| Multimedia ingest | PDF text + vision on embedded images and scanned pages (Florence-2), image captions/OCR, audio and video transcription (Whisper), video visual understanding (Marlin); results are appended to the note markdown |
| Knowledge graph | Embedded Kuzu property graph, LLM-extracted typed entities and weighted relationships, optional community clustering (labelled "Leiden", implemented with scikit-learn agglomerative clustering) and temporal digests, deterministic 3D layouts, a 3D graph explorer and a separate wikilink graph |
| Chat | Multi-hop research loop combining entity lookup, keyword search (Meilisearch), vector search (Qdrant), graph expansion and a local cross-encoder reranker; persistent conversations; optional "thinking" display |
| Knowledge bases | Multiple fully isolated KBs (separate vault, graph, vectors, keyword index, Firefly administration) switchable from the sidebar |
| Finance | Per-KB Firefly III administration: accounts, transactions, budgets, categories, bills, piggy banks, recurrences, rules, reports, search |
| Local models | First-run wizard picks a data dir and a models dir (NAS/OneDrive friendly); GGUF chat/embed/rerank via llama-cpp-python inside the API process; cloud providers optional |

---

## 2. Who it is for and how it is delivered

- **End users** install the Orb desktop app. They do not run Docker, Ollama, a model server or a database. The app downloads Qdrant, Meilisearch and a PHP runtime into the chosen data directory on first launch and offers model downloads from the Setup page.
- **Contributors** run the same Electron shell against the repository (`cd desktop && npm start`), which uses `backend/.venv` and `next dev`. A `docker-compose.yml` still exists for infra experiments but is explicitly not the product path.
- **AI coding assistants** are a first-class audience for this documentation: every doc is code-grounded and enumerates invariants that must not be broken.

---

## 3. Product decisions that shape the code

These are locked decisions (see [26-decisions-and-constraints.md](26-decisions-and-constraints.md) for the full list with rationale):

1. **Desktop shell, no Docker for users.** Electron supervises local binaries and processes.
2. **Every local model runs in-process** in the FastAPI worker, loaded from `MODELS_DIR`. No HTTP model sidecars, no Ollama/LM Studio/llama-server.
3. **Exclusive residency.** Only one heavy model is in memory at a time (chat *or* embed *or* rerank *or* Florence/Whisper/Marlin).
4. **Notes are files.** Bodies live as `.md` in a per-KB vault; SQLite stores metadata only. Attachments live in the vault too.
5. **Per-KB isolation everywhere**, including finance (one Firefly administration per KB).
6. **Fail-closed indexing.** A Qdrant dimension mismatch during ingest raises instead of wiping a collection.
7. **Idempotent enrichment.** Prior enrichment blocks are stripped before re-ingest.
8. **Dedicated port block 17400–17470** so the app coexists with typical dev stacks.

---

## 4. Technology stack

| Layer | Technology | Version pins (as of 0.2.0) |
|---|---|---|
| Desktop shell | Electron, electron-builder, electron-updater (opt-in) | Electron 43.2.0, builder 26 |
| UI | Next.js app router, React + React Compiler, Tailwind CSS, CodeMirror 6, three.js + react-force-graph (2D/3D), framer-motion, axios | Next 16.2.12, React 19.2.6, Tailwind 4, TypeScript 6 |
| API | FastAPI + uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2 async (aiosqlite / asyncpg), LangGraph, tenacity, httpx | FastAPI 0.128, LangGraph 1.0.6 |
| Graph | Kuzu embedded graph database | kuzu 0.11.3 |
| Vectors | Qdrant (local binary) + qdrant-client | Qdrant v1.18.2, client 1.17.1 |
| Keyword search | Meilisearch (local binary) + meilisearch python | Meilisearch v1.49.0, client 0.34.1 |
| Local inference | llama-cpp-python (GGUF; Metal/CUDA/Vulkan/CPU), torch + transformers ≥ 5.7 + qwen-vl-utils (Florence-2, Whisper, Marlin) | llama-cpp-python ≥ 0.3 |
| Cloud LLMs | openai, anthropic, google-genai, HuggingFace; `instructor` for structured output; `json-repair` | – |
| Document parsing | PyMuPDF, Pillow, python-docx, openpyxl, `av` (media probing), ffmpeg (transcoding) | – |
| Finance | Firefly III (Laravel) on a portable PHP 8.5 from NativePHP `php-bin` | Firefly v6.6.6, php-bin 1.2.0 |
| Packaging | python-build-standalone CPython, portable Node, Next standalone output | Python 3.12.9, Node 24.4.1 |
| Metadata DB | SQLite (`DATA_DIR/orb.db`); PostgreSQL optional for Docker contributors | – |

---

## 5. How the pieces fit (30-second version)

```
Electron main.js ── supervises ──▶ Qdrant · Meilisearch · Firefly III · FastAPI · Next.js
                                          ▲                     │
Next.js UI ── /api/v1 (rewrite) ──────────┘                     │ in-process
FastAPI ── per-KB KBContext ──▶ vault .md · SQLite metadata · Kuzu graph · Qdrant · Meili · Firefly
        └── models: GGUF chat/embed/rerank · Florence-2 · Whisper · Marlin (one resident at a time)
```

Write path: note saved → attachments enriched → LLM extracts entities/relationships → graph + vectors + keyword index updated → communities recomputed after idle.
Read path: question → query analysis → up to two hybrid retrieval rounds (`MAX_LOOP_ITERATIONS=3` counts the planning step) → cross-encoder rerank → answer with a `### References` block → conversation persisted.

The full picture with diagrams is in [02-system-architecture.md](02-system-architecture.md).

---

## 6. Versioning and status

- Current version **0.2.0** (desktop `package.json`, frontend `package.json`; the FastAPI `version` string still reads `0.1.0`).
- Installers are **unsigned**; macOS users may need `xattr -cr /Applications/Orb.app`. Notarization and Authenticode hooks exist but are not enabled.
- Auto-update is disabled unless `ORB_ENABLE_UPDATER=1`.
- Name lineage: **LiveOS Brain** (January–July 2026 research prototype) → **LifeOS** (desktop pivot, `3f21e08`) → **Orb** (`6162be2`, same day, 2026-08-02). Legacy `LIVEOS_*` / `LifeOS` identifiers survive only as read-compatibility aliases.

---

## 7. How to use this documentation set

| If you want to… | Read |
|---|---|
| Understand the system end to end | 01 → 02 → 03 |
| Change the desktop shell, startup, ports, packaging | 04, 05 |
| Add or change an API endpoint | 06, 07, then the domain doc |
| Work on notes, vaults, wikilinks, KBs | 08, 09, 22 |
| Work on ingestion or media enrichment | 10, 11, 14, 15 |
| Work on models, providers, prompts | 12, 13, 21 |
| Work on retrieval or chat quality | 16, 14, 15, 24 |
| Work on finance | 17 |
| Work on the UI | 18, 19, 20 |
| Configure or debug an install | 21, 22, 23 |
| Run tests or benchmarks | 24 |
| Know why something is the way it is | 25, 26 |
| Set up a dev environment and follow conventions | 27 |
| Look up a term | 28 |

The index with one-line summaries of every file is in [README.md](README.md).
