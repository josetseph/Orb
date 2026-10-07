# Sweep round5-musique-a

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5}`
Varied: `{"MAX_LOOP_ITERATIONS": [8], "QUERY_ATTRIBUTE_MODE": ["final", "list"]}` (one-at-a-time)

## Rung: first 50 dev questions (5 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.125 | 8% | 0.445 | 0.517 | 454 | 22371 (379/526 notes) | 20 {'query analysis': 18, 'research step': 2} |  |
| MAX_LOOP_ITERATIONS=8 | 0.126 | 8% | 0.477 | 0.517 | 656 | 22371 (379/526 notes) | 34 {'query analysis': 31, 'research step': 3} | +1/-1 EM, answer_f1 CI -0.057 to +0.060, within noise |
| QUERY_ATTRIBUTE_MODE=final | 0.168 | 12% | 0.470 | 0.517 | 385 | 22371 (379/526 notes) | 10 {'query analysis': 8, 'research step': 2} | +2/-0 EM, answer_f1 CI -0.000 to +0.098, within noise |
| QUERY_ATTRIBUTE_MODE=list | 0.140 | 10% | 0.480 | 0.517 | 335 | 22371 (379/526 notes) | 5 {'query analysis': 2, 'research step': 3} | +1/-0 EM, answer_f1 CI -0.019 to +0.064, within noise |
| chat_model=qwen35-9b-q4,ENABLE_THINKING=False | 0.132 | 10% | 0.457 | 0.517 | 387 | 22371 (379/526 notes) | 7 {'research step': 7} | +1/-0 EM, answer_f1 CI -0.031 to +0.058, within noise |
