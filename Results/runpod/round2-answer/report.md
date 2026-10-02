# Sweep round2-answer

Dataset hotpotqa, dev questions 0 to 20, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4"}`
Varied: `{"chat_model": ["gemma4-12b-q4", "gemma4-e2b-q4"], "MAX_LOOP_ITERATIONS": [5, 8]}` (one-at-a-time)

## Rung: first 20 dev questions (5 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.712 | 55% | 0.900 | 131 | 2304 (195/199 notes) | 1 {'query analysis': 1} |  |
| chat_model=gemma4-12b-q4 | 0.722 | 55% | 0.850 | 132 | 2304 (195/199 notes) | 0  | +0/-0 EM, answer_f1 CI -0.022 to +0.046, within noise |
| chat_model=gemma4-e2b-q4 | 0.466 | 30% | 0.775 | 101 | 2304 (195/199 notes) | 6 {'research step': 6} | +1/-6 EM, answer_f1 CI -0.452 to -0.066, within noise |
| MAX_LOOP_ITERATIONS=5 | 0.745 | 55% | 0.900 | 91 | 2304 (195/199 notes) | 1 {'query analysis': 1} | +1/-1 EM, answer_f1 CI -0.050 to +0.143, within noise |
| MAX_LOOP_ITERATIONS=8 | 0.723 | 55% | 0.900 | 89 | 2304 (195/199 notes) | 1 {'research step': 1} | +0/-0 EM, answer_f1 CI -0.027 to +0.058, within noise |
