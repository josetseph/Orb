# Sweep round3-musique

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5}`
Varied: `{}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.158 | 12% | 0.447 | 0.492 | 149 | 6746 (369/526 notes) | 18 {'query analysis': 16, 'research step': 2} |  |
| ingestion_model=qwen35-9b-q4,EXTRACTION_ENABLE_THINKING=False | 0.062 | 4% | 0.218 | 0.293 | 137 | 51109 (230/526 notes) | 13 {'query analysis': 11, 'research step': 2} | +0/-4 EM, answer_f1 CI -0.171 to -0.032, within noise |
