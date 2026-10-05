# Sweep round4-cheap-retrieval

Dataset hotpotqa, dev questions 20 to 70, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"RERANK_MODEL_ID": ["qwen3-rerank-0.6b-q4"], "GRAPH_EXPAND_TOP_NEIGHBORS": [0]}` (one-at-a-time)

## Rung: first 50 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.672 | 56% | 0.920 | 0.990 | 98 | 5059 (487/492 notes) | 4 {'query analysis': 4} |  |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4 | 0.622 | 50% | 0.930 | 0.990 | 94 | 5059 (487/492 notes) | 5 {'query analysis': 5} | +1/-4 EM, answer_f1 CI -0.145 to +0.037, within noise |
| GRAPH_EXPAND_TOP_NEIGHBORS=0 | 0.595 | 46% | 0.900 | 0.990 | 91 | 5059 (487/492 notes) | 6 {'research step': 1, 'query analysis': 5} | +1/-6 EM, answer_f1 CI -0.166 to +0.006, within noise |
| RERANK_MODEL_ID=qwen3-rerank-0.6b-q4,GRAPH_EXPAND_TOP_NEIGHBORS=0,EMBED_MODEL_ID=qwen3-embed-0.6b-q8 | 0.629 | 50% | 0.890 | 0.990 | 99 | 4888 (487/492 notes) | 8 {'query analysis': 6, 'research step': 2} | +3/-6 EM, answer_f1 CI -0.152 to +0.065, within noise |
