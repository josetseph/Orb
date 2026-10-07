# Sweep round5-retrieval

Dataset hotpotqa, dev questions 0 to 100, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"RERANK_MODEL_ID": ["qwen3-rerank-0.6b-q4"], "GRAPH_EXPAND_TOP_NEIGHBORS": [0]}` (one-at-a-time)

## Rung: first 100 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.657 | 50% | 0.955 | 0.995 | 418 | 12914 (971/990 notes) | 16 {'query analysis': 11, 'research step': 5} |  |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 | 0.625 | 50% | 0.945 | 0.995 | 407 | 12914 (971/990 notes) | 17 {'query analysis': 14, 'research step': 3} | +8/-8 EM, answer_f1 CI -0.102 to +0.036, within noise |
| GRAPH_EXPAND_TOP_NEIGHBORS=0 | 0.632 | 49% | 0.940 | 0.975 | 429 | 12914 (971/990 notes) | 17 {'query analysis': 15, 'research step': 2} | +7/-8 EM, answer_f1 CI -0.090 to +0.045, within noise |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4,GRAPH_EXPAND_TOP_NEIGHBORS=0,EMBED_MODEL_ID=qwen3-embed-0.6b-q8 | 0.573 | 44% | 0.910 | 1.000 | 429 | 12676 (970/990 notes) | 22 {'query analysis': 16, 'research step': 6} | +6/-12 EM, answer_f1 CI -0.166 to -0.004, within noise |
