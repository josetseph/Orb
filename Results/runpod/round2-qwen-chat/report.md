# Sweep round2-qwen-chat

Dataset hotpotqa, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "qwen35-4b-q4", "ingestion_model": "gemma4-12b-q4", "ENABLE_THINKING": false}`
Varied: `{"chat_model": ["qwen35-9b-q4", "qwen35-2b-q4"]}` (one-at-a-time)

## Rung: first 20 dev questions (3 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.603 | 50% | 0.700 | 215 | 2304 (195/199 notes) | 5 {'research step': 4, 'query analysis': 1} |  |
| chat_model=qwen35-9b-q4 | 0.738 | 55% | 0.825 | 281 | 2304 (195/199 notes) | 2 {'research step': 2} | +1/-0 EM, answer_f1 CI -0.018 to +0.313, within noise |
| chat_model=qwen35-2b-q4 | 0.261 | 15% | 0.225 | 168 | 2304 (195/199 notes) | 11 {'research step': 9, 'query analysis': 2} | +0/-7 EM, answer_f1 CI -0.570 to -0.103, real |
