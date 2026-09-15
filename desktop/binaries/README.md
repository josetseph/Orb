# Desktop binaries (Docker-free)

**End users do nothing.** On first launch the Electron supervisor downloads **Qdrant** and **Meilisearch** into `DATA_DIR/bin/<platform>/` (splash shows progress).

## Prefetch (optional — packaging / CI)

```bash
cd desktop
npm run prefetch-binaries
```

| Engine | Default | Env |
|--------|---------|-----|
| Qdrant | `v1.18.2` | `ORB_QDRANT_VERSION` |
| Meilisearch | `v1.49.0` | `ORB_MEILI_VERSION` |

## Local LLM (no Ollama / llama-server)

Local chat + embeddings use **in-process** [`llama-cpp-python`](https://github.com/abetlen/llama-cpp-python). Setup → **Download models & start local LLM** fetches GGUF weights into `MODELS_DIR/gguf/` and loads them in the FastAPI process.

Acceleration is auto-detected (Metal on macOS, CUDA when `nvidia-smi` is present, else CPU). Overrides:

- `ORB_LLAMA_BACKEND=metal|cuda|vulkan|cpu`
- `ORB_LLAMA_N_GPU_LAYERS=-1` (all layers on GPU)
- `ORB_LLAMA_N_CTX=16384` (chat KV; 32k + `swa_full` OOMs on ~24GB Metal)
- `ORB_LLAMA_MAX_TOKENS=10240`
- `ORB_LLAMA_SWA_FULL=true` (required for stable Gemma 4 — compact SWA → ordinal loops)
- `ORB_LLAMA_REPEAT_PENALTY=1.12`
- `ORB_EMBED_N_CTX=8192`
- `ORB_RERANK_N_CTX=8192`

Only one heavy model is resident at a time (chat ↔ embed ↔ rerank ↔ Qwen3-ASR/Marlin). Chat aborts + retries on Gemma 4 ordinal/"or the" repetition cascades.

Install llama-cpp-python with the matching backend, e.g. Metal:

```bash
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```
