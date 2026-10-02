# Sweep round2-retrieval

Dataset hotpotqa, dev questions 0 to 20, evaluator retrieval, metric candidate_recall.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"RERANK_MODEL_ID": ["qwen3-rerank-4b-q4", "qwen3-rerank-0.6b-q4"], "RERANKER_TOP_K": [5, 20], "VECTOR_PRE_RERANK_THRESHOLD": [0.3, 0.6], "GRAPH_EXPAND_TOP_NEIGHBORS": [0, 20]}` (one-at-a-time)

## Rung: first 20 dev questions (9 variants)

| variant | candidate_recall | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.875 | n/a | 0.875 | 61 | 2304 (195/199 notes) | 0  |  |
| RERANK_MODEL_ID=qwen3-rerank-4b-q4 | 0.875 | n/a | 0.875 | 69 | 2304 (195/199 notes) | 0  |  |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 | 0.875 | n/a | 0.875 | 60 | 2304 (195/199 notes) | 0  |  |
| RERANKER_TOP_K=5 | 0.875 | n/a | 0.875 | 64 | 2304 (195/199 notes) | 0  |  |
| RERANKER_TOP_K=20 | 0.875 | n/a | 0.875 | 71 | 2304 (195/199 notes) | 0  |  |
| VECTOR_PRE_RERANK_THRESHOLD=0.3 | 0.900 | n/a | 0.900 | 128 | 2304 (195/199 notes) | 0  |  |
| VECTOR_PRE_RERANK_THRESHOLD=0.6 | 0.825 | n/a | 0.825 | 43 | 2304 (195/199 notes) | 0  |  |
| GRAPH_EXPAND_TOP_NEIGHBORS=0 | 0.875 | n/a | 0.875 | 64 | 2304 (195/199 notes) | 0  |  |
| GRAPH_EXPAND_TOP_NEIGHBORS=20 | 0.875 | n/a | 0.875 | 57 | 2304 (195/199 notes) | 0  |  |
