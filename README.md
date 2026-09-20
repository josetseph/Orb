<div align="center">

  <img src="desktop/assets/logo.png" width="128" alt="Orb logo" />

  <h1>Orb</h1>

  <p><b>Your knowledge, on your machine.</b></p>

  <p>
    Notes, voice, images, and documents become a searchable knowledge graph.<br/>
    Chat across it. No Docker. No cloud required.
  </p>

<p>
  <a href="https://github.com/josetseph/Orb/actions/workflows/desktop-release.yml"><img src="https://img.shields.io/github/actions/workflow/status/josetseph/Orb/desktop-release.yml?branch=main&label=desktop%20build" alt="Desktop build status" /></a>
  <a href="https://github.com/josetseph/Orb/blob/main/LICENSE"><img src="https://img.shields.io/github/license/josetseph/Orb?color=blue" alt="License: MIT" /></a>
  <a href="https://github.com/josetseph/Orb/releases/latest"><img src="https://img.shields.io/github/v/release/josetseph/Orb?label=latest&color=red" alt="Latest release" /></a>
  <a href="https://github.com/josetseph/Orb/stargazers"><img src="https://img.shields.io/github/stars/josetseph/Orb?color=yellow" alt="GitHub stars" /></a>
</p>

  <p>
    <a href="https://github.com/josetseph/Orb/releases/latest"><b>Download</b></a>
    &nbsp;·&nbsp;
    <a href="#installation">Install guide</a>
    &nbsp;·&nbsp;
    <a href="#build-from-source">Build from source</a>
    &nbsp;·&nbsp;
    <a href="#privacy">Privacy</a>
  </p>

  <img src="Platform%20Images/home_view.png" width="720" alt="Orb home" />

</div>

---

Write notes the way you already do — text, voice, photos, PDFs. Orb extracts entities and relationships into a knowledge graph, indexes them for search, and answers multi-hop questions in chat. Everything runs locally through the desktop app: your vault, your models, your machine.

> [!NOTE]
> End users install the **Orb** desktop app. You do not need Docker, Ollama, or a separate model server. Local chat, embedding, reranking, Qwen3-ASR, and Marlin all load **in-process** in the API.

---

## Features

### Notes & vault

- Per–knowledge-base markdown vaults (note bodies live as real `.md` files, not in SQLite)
- Attachments stay in the vault — images, audio, PDFs, documents
- Entity highlighting and autocomplete from the graph as you write
- In-app voice recording and file attach on save

### Multimedia ingest

On save, Orb enriches the note before graph indexing:

- **PDF** — native text, plus the ingestion model reads embedded images and sparse page renders
- **Images** — described by the model you chose for ingestion (a local GGUF through its vision projector, or a cloud endpoint), including a verbatim transcription of visible text
- **Audio / video** — Qwen3-ASR transcription (MLX on Apple Silicon, transformers elsewhere); video also runs Marlin for visual understanding
- Enrichment is written into the vault markdown, then ingested into the graph

### Chat & retrieval

- Multi-hop research loop over the knowledge graph (not a single vector lookup)
- Hybrid retrieval: entity lookup, keyword (Meilisearch), and vectors (Qdrant)
- Cross-encoder reranking with a local GGUF
- Inline source citations and optional model thinking

### Knowledge graph

- Embedded Kuzu property graph with Leiden communities
- 3D graph explorer and node detail panels
- Multiple isolated knowledge bases (separate vault, vectors, keyword index, and graph)

### Finance

- Per-KB Firefly III administrations — accounts and transactions stay scoped to a vault

### Local models

- First-run wizard chooses **data dir** (local disk), **models dir** (NAS friendly) and the notes **vault** (the one folder that may live in OneDrive / iCloud)
- GGUF chat, embed, and rerank via `llama-cpp-python` in the API process
- Only one heavy model resident at a time (chat **or** embed **or** rerank **or** transcription/Marlin)
- Cloud providers (Gemini, OpenAI, Anthropic, …) remain available if you want them

---

## Screenshots

<table>
  <tr>
    <td><img src="Platform%20Images/chat_view.png" alt="Chat interface"/></td>
    <td><img src="Platform%20Images/notes_page_edit_view.png" alt="Notes editor"/></td>
  </tr>
  <tr>
    <td align="center"><em>Chat</em></td>
    <td align="center"><em>Notes editor</em></td>
  </tr>
</table>

![3D Knowledge Graph](Platform%20Images/graph_view.png)

<table>
  <tr>
    <td><img src="Platform%20Images/knowledge_base_selector_view.png" alt="Knowledge base manager"/></td>
    <td><img src="Platform%20Images/llm_model_settings_view.png" alt="Runtime model settings"/></td>
  </tr>
  <tr>
    <td align="center"><em>Knowledge bases</em></td>
    <td align="center"><em>Model settings</em></td>
  </tr>
</table>

---

## Download

<div align="center">

<a href="https://github.com/josetseph/Orb/releases/latest"><img src="https://img.shields.io/badge/download-Orb-2EA043?style=flat&logo=apple&logoColor=white" alt="Download Orb" /></a>

</div>

Installers ship as macOS `.dmg`, Windows `.exe` and Linux `.deb` / `.rpm` from [GitHub Releases](https://github.com/josetseph/Orb/releases) (tags `desktop-v*`).

---

## Installation

### macOS

1. Download the latest `.dmg` from [Releases](https://github.com/josetseph/Orb/releases/latest) (`arm64` for Apple Silicon, `x64` for Intel)
2. Open it and drag **Orb** into Applications
3. Launch Orb
4. Complete the first-run wizard — pick a **data directory**, a **models directory** and your notes **vault**

Keep the data directory on your local disk — the default `~/Library/Application Support/Orb/data` is right. Do not put it in iCloud Drive, OneDrive, Dropbox or Google Drive: "online-only" files and sync writes under a running database cause hangs and corruption (Orb logs a warning when it detects such a path). Your notes vault is the only folder that belongs in a synced location; pin it "Always keep on this device".

On first launch the app downloads Qdrant and Meilisearch into your data dir, and you can pull GGUF models from Setup.

If macOS says the app is **damaged** (common for unsigned downloads), clear quarantine then reopen:

```bash
xattr -cr /Applications/Orb.app
open /Applications/Orb.app
```

### Windows

1. Download the latest `.exe` installer from [Releases](https://github.com/josetseph/Orb/releases/latest)
2. Run the installer and open Orb
3. Complete the first-run wizard (data dir on local disk — not OneDrive — plus models dir and vault)

> [!TIP]
> Unsigned builds may need an extra click through Gatekeeper / SmartScreen until notarization and Authenticode are enabled.
> Prefer **v1.0.0+** — the first Tauri-based build: smaller, faster to start, one model picker in Settings. Avoid v0.1.0 Mac builds (could show “damaged” from broken Node helper symlinks in CI).

---

## Build from source

Prefer to run or package Orb yourself? The Tauri shell and build script live under [`desktop/`](desktop/).

### Prerequisites

- Rust (`brew install rust` or rustup) and `cargo install tauri-cli`
- Node.js 20+ (only to build the UI)
- Python 3.11+ (dev uses the repo `backend/.venv` when present)
- macOS: Xcode CLT + `cmake` for Metal `llama-cpp-python` when packaging
- ffmpeg (audio transcoding)

### Develop

```bash
git clone https://github.com/josetseph/Orb.git
cd Orb/frontend && npm install && npm run dev            # UI with live reload on 3700
cd Orb/desktop/src-tauri && ORB_URL=http://127.0.0.1:3700 cargo tauri dev   # the shell
```

The shell spawns the Python runtime, which starts the API and then Qdrant, Meilisearch and Firefly behind it. Ports: API `17401` (serves the UI when packaged), Firefly `17412`, Qdrant `17433`, Meilisearch `17470`.

More detail: [`desktop/README.md`](desktop/README.md).

### Package installers

```bash
python3 desktop/build.py prepare   # bundle Python + UI + Firefly seed (~10–20 min)
python3 desktop/build.py dist      # tauri build → nsis / deb + rpm; macOS: .app via Tauri, .dmg via hdiutil
```

Full packaging notes: [`desktop/PACKAGING.md`](desktop/PACKAGING.md).


---

## Privacy

Orb is local-first. Notes, vault files, vectors, and models live under the directories you chose (or Application Support / `%APPDATA%\Orb`). Nothing is uploaded unless you explicitly configure a cloud LLM provider.

---

## License

Orb is released under the [MIT License](LICENSE).
