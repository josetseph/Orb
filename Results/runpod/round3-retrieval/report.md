# Sweep round3-retrieval

Dataset hotpotqa, dev questions 0 to 20, evaluator retrieval, metric context_recall.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"RERANK_MODEL_ID": ["qwen3-rerank-4b-q4", "qwen3-rerank-0.6b-q4"], "RERANKER_TOP_K": [5, 20], "VECTOR_PRE_RERANK_THRESHOLD": [0.3, 0.6], "GRAPH_EXPAND_TOP_NEIGHBORS": [0, 20]}` (one-at-a-time)

## Rung: first 20 dev questions (9 variants)

| variant | context_recall | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.950 | n/a | 0.950 | 1.000 | 46 | 1918 (195/199 notes) | 0  |  |
| RERANK_MODEL_ID=qwen3-rerank-4b-q4 | 0.975 | n/a | 0.975 | 1.000 | 38 | 1918 (195/199 notes) | 0  |  |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 | 0.950 | n/a | 0.950 | 1.000 | 36 | 1918 (195/199 notes) | 0  |  |
| RERANKER_TOP_K=5 | 0.950 | n/a | 0.950 | 1.000 | 28 | 1918 (195/199 notes) | 0  |  |
| RERANKER_TOP_K=20 | 0.975 | n/a | 0.975 | 1.000 | 39 | 1918 (195/199 notes) | 0  |  |
| VECTOR_PRE_RERANK_THRESHOLD=0.3 | 0.975 | n/a | 0.975 | 1.000 | 91 | 1918 (195/199 notes) | 0  |  |
| VECTOR_PRE_RERANK_THRESHOLD=0.6 | 0.900 | n/a | 0.900 | 1.000 | 22 | 1918 (195/199 notes) | 0  |  |
| GRAPH_EXPAND_TOP_NEIGHBORS=0 | 0.950 | n/a | 0.950 | 1.000 | 18 | 1918 (195/199 notes) | 0  |  |
| GRAPH_EXPAND_TOP_NEIGHBORS=20 | 0.950 | n/a | 0.950 | 1.000 | 10 | 1918 (195/199 notes) | 0  |  |
