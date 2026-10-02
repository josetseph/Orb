# Sweep round2-qwen-extract

Dataset hotpotqa, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "qwen35-4b-q4", "EXTRACTION_ENABLE_THINKING": false}`
Varied: `{"ingestion_model": ["qwen35-9b-q4", "qwen35-2b-q4"], "EXTRACTION_JSON_CONSTRAINED": [true]}` (one-at-a-time)

## Rung: first 20 dev questions (4 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.413 | 25% | 0.700 | 133 | 8627 (163/199 notes) | 0  |  |
| ingestion_model=qwen35-9b-q4 | 0.696 | 60% | 0.700 | 178 | 13519 (163/199 notes) | 0  | +7/-0 EM, answer_f1 CI +0.051 to +0.516, real |
| ingestion_model=qwen35-2b-q4 | 0.298 | 20% | 0.525 | 97 | 7350 (99/199 notes) | 0  | +2/-3 EM, answer_f1 CI -0.372 to +0.145, within noise |
| EXTRACTION_JSON_CONSTRAINED=True | 0.439 | 25% | 0.700 | 124 | 19708 (164/199 notes) | 0  | +0/-0 EM, answer_f1 CI -0.007 to +0.080, within noise |
