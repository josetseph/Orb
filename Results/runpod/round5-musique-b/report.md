# Sweep round5-musique-b

Dataset musique, dev questions 0 to 50, evaluator full, metric answer_f1.
Baseline: `{"EMBED_MODEL_ID": "qwen3-embed-8b-q4", "RERANK_MODEL_ID": "qwen3-rerank-8b-q4", "EXTRACTION_JSON_CONSTRAINED": false, "chat_model": "gemma4-e4b-q4", "ingestion_model": "gemma4-12b-q4", "MAX_LOOP_ITERATIONS": 5, "EXTRACTION_PROMPT_SUFFIX": "Before you reply, check every relationship: its source_name and target_name must each be exactly the name of one of your nodes. Add a node for anything a relationship points to that is not yet one."}`
Varied: `{"EXTRACTION_JSON_CONSTRAINED": [true]}` (one-at-a-time)

## Rung: first 50 dev questions (2 variants)

| variant | answer_f1 | exact match | retrieval recall | gold in index | s / question | index build s (notes ingested) | unusable replies | vs base |
|---|---|---|---|---|---|---|---|---|
| base | 0.198 | 14% | 0.537 | 0.562 | 493 | 29034 (388/526 notes) | 15 {'query analysis': 13, 'research step': 2} |  |
| EXTRACTION_JSON_CONSTRAINED=True | 0.220 | 18% | 0.637 | 0.670 | 729 | 24697 (436/526 notes) | 10 {'query analysis': 10} | +2/-0 EM, answer_f1 CI -0.013 to +0.072, within noise |
