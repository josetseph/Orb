# Sweep round2-scale-musique

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5}`
Varied: `{"ingestion_model": ["gemma4-e4b-q4"]}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|
| base | 0.165 | 12% | 0.000 | 209 | 91584 (366/526 notes) | 20 {'query analysis': 18, 'research step': 2} |  |
| ingestion_model=gemma4-e4b-q4 | not run | | | | | | |
