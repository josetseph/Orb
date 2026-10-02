# Sweep round2-index

Dataset hotpotqa, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"EMBED_MODEL_ID": ["qwen3-embed-4b-q4", "qwen3-embed-0.6b-q8"], "communities": [true]}` (one-at-a-time)

## Rung: first 20 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.764 | 55% | 0.900 | 83 | 2304 (195/199 notes) | 0  |  |
| EMBED_MODEL_ID=qwen3-embed-4b-q4 | 0.712 | 45% | 0.900 | 116 | 2116 (195/199 notes) | 0  | +1/-3 EM, answer_f1 CI -0.180 to +0.052, within noise |
| EMBED_MODEL_ID=qwen3-embed-0.6b-q8 | 0.747 | 55% | 0.825 | 128 | 2002 (195/199 notes) | 0  | +1/-1 EM, answer_f1 CI -0.119 to +0.059, within noise |
| communities=True | 0.657 | 50% | 0.900 | 115 | 2325 (195/199 notes) | 2 {'research step': 2} | +1/-2 EM, answer_f1 CI -0.274 to +0.026, within noise |
